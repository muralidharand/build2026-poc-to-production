"""
Durable Task orchestration for Fibey coordinator agent.

Architecture:
- ResponsesAgentServerHost serves the Hosted Agent responses/invoke protocol (unchanged)
- DurableTaskSchedulerWorker runs in the same process, connected to Azure DTS
- Orchestration wraps the investigation workflow with a human approval gate
- External callers can raise events via the DurableTaskSchedulerClient

Uses standalone durabletask-python SDK (NOT azure-durable-functions or AgentFunctionApp).
"""

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass, asdict
from datetime import timedelta
from typing import Optional

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

from durabletask import task
from durabletask.azuremanaged.client import DurableTaskSchedulerClient
from durabletask.azuremanaged.worker import DurableTaskSchedulerWorker

from agent_framework import Agent
from agent_framework.azure import DurableAIAgentOrchestrationContext, DurableAIAgentWorker

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DTS_ENDPOINT = os.getenv("DURABLE_TASK_ENDPOINT", "")
DTS_TASKHUB = os.getenv("DURABLE_TASK_TASKHUB", "fibey-hub")
APPROVAL_TIMEOUT_HOURS = float(os.getenv("APPROVAL_TIMEOUT_HOURS", "72"))
# Name of the MAF agent registered as a durable agent (must match agent.yaml).
AGENT_NAME = os.getenv("AGENT_NAME", "fibey-coordinator")


def _get_credential():
    """ManagedIdentity when deployed, DefaultAzureCredential for local dev."""
    client_id = os.getenv("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential()


# ---------------------------------------------------------------------------
# Activity functions (non-deterministic work wrapped for checkpointing)
# ---------------------------------------------------------------------------

def execute_destructive_action(ctx: task.ActivityContext, action_json: str) -> str:
    """Activity: Execute a destructive action AFTER human approval."""
    action = json.loads(action_json)
    logger.info(f"[DurableTask] Executing approved action: {action.get('action')}")
    
    # TODO: Wire into actual dispatch logic
    return json.dumps({
        "action": action.get("action"),
        "status": "dispatched",
        "work_order_id": f"WO-{uuid.uuid4().hex[:8].upper()}",
        "details": f"Emergency fiber splice dispatched for {action.get('location', 'Quincy North DC')}",
    })


def notify_approval_required(ctx: task.ActivityContext, notification_json: str) -> str:
    """Activity: Notify that human approval is required."""
    notification = json.loads(notification_json)
    logger.info(f"[DurableTask] ⏳ APPROVAL REQUIRED")
    logger.info(f"[DurableTask] Action: {notification.get('action')}")
    logger.info(f"[DurableTask] Agent is now paused — scales to zero while waiting.")
    return "notified"


# ---------------------------------------------------------------------------
# Orchestration function
# ---------------------------------------------------------------------------

def investigation_with_approval(ctx: task.OrchestrationContext, request_json: str):
    """Durable orchestration: investigate → approve → execute.
    
    Flow:
    1. Run agent investigation (checkpointed — survives restarts)
    2. If destructive action needed, pause for human approval
    3. Agent scales to zero while waiting (no compute consumed)
    4. On approval: execute the action
    5. On rejection/timeout: report back without executing
    """
    request = json.loads(request_json)

    # Step 1: Run the MAF agent inline via the Durable Extension for MAF.
    # The agent is hosted as a durable agent (chat history + tool calls
    # auto-persisted to DTS); the response is checkpointed by the orchestrator.
    agent_context = DurableAIAgentOrchestrationContext(ctx)
    agent = agent_context.get_agent(AGENT_NAME)
    session = agent.create_session()
    response = yield agent.run(
        messages=request.get("user_message", ""), session=session
    )
    investigation = {
        "summary": str(response.message) if hasattr(response, "message") else str(response)
    }
    
    destructive_action = request.get("destructive_action")
    if not destructive_action:
        return json.dumps({
            "status": "completed",
            "summary": investigation.get("summary", ""),
        })
    
    # Step 2: Notify human
    yield ctx.call_activity(notify_approval_required, input=json.dumps({
        "session_id": request.get("session_id"),
        "action": destructive_action,
        "summary": investigation.get("summary", ""),
        "findings": investigation.get("findings", ""),
    }))
    
    # Mark this orchestration as parked awaiting human approval. The custom
    # status is surfaced via DTS query (OrchestrationState.serialized_custom_status)
    # so the "Pending Approvals" page can list exactly the orchestrations that are
    # waiting for a human (not ones still mid-investigation), along with the
    # context a reviewer needs to decide.
    ctx.set_custom_status({
        "phase": "awaiting_approval",
        "session_id": request.get("session_id"),
        "action": destructive_action,
        "summary": investigation.get("summary", ""),
    })

    # Step 3: Wait for approval OR timeout (scales to zero here)
    approval_event = ctx.wait_for_external_event("HumanApproval")
    timeout_event = ctx.create_timer(timedelta(hours=APPROVAL_TIMEOUT_HOURS))
    
    winner = yield task.when_any([approval_event, timeout_event])
    ctx.set_custom_status({"phase": "resumed"})
    
    if winner == timeout_event:
        return json.dumps({
            "status": "timed_out",
            "summary": investigation.get("summary", ""),
            "message": f"No approval received within {APPROVAL_TIMEOUT_HOURS}h",
        })
    
    # Parse approval decision. Completed durable tasks expose their payload via
    # get_result() — accessing `.result` raises AttributeError on CancellableTask.
    approval_raw = approval_event.get_result()
    approval = json.loads(approval_raw) if isinstance(approval_raw, str) else approval_raw
    
    if not approval.get("approved", False):
        return json.dumps({
            "status": "rejected",
            "summary": investigation.get("summary", ""),
            "feedback": approval.get("feedback", ""),
            "approver": approval.get("approver", "unknown"),
        })
    
    # Step 4: Execute approved action
    result_json = yield ctx.call_activity(execute_destructive_action, input=json.dumps({
        "action": destructive_action,
        "location": investigation.get("location", "Quincy North DC"),
        "approver": approval.get("approver", "unknown"),
    }))
    
    return json.dumps({
        "status": "completed",
        "summary": investigation.get("summary", ""),
        "action_result": json.loads(result_json),
        "approver": approval.get("approver", "unknown"),
    })


# ---------------------------------------------------------------------------
# DurableTaskManager — lifecycle management
# ---------------------------------------------------------------------------

class DurableTaskManager:
    """Manages the DurableTask worker lifecycle alongside the agent host."""
    
    def __init__(self):
        self._worker: Optional[DurableTaskSchedulerWorker] = None
        self._client: Optional[DurableTaskSchedulerClient] = None
        self._thread: Optional[threading.Thread] = None
        self._credential = _get_credential()
        self._agent: Optional[Agent] = None

    def register_agent(self, agent: Agent):
        """Register the MAF agent to be hosted as a durable agent."""
        self._agent = agent

    def start(self):
        """Start the Durable Task worker in a background thread."""
        self._client = DurableTaskSchedulerClient(
            host_address=DTS_ENDPOINT,
            taskhub=DTS_TASKHUB,
            token_credential=self._credential,
        )
        
        self._worker = DurableTaskSchedulerWorker(
            host_address=DTS_ENDPOINT,
            taskhub=DTS_TASKHUB,
            token_credential=self._credential,
        )
        
        # Register the MAF agent as a durable agent (chat history + tool calls
        # auto-persisted to DTS by the Durable Extension for MAF).
        if self._agent is None:
            raise RuntimeError(
                "No MAF agent registered. Call register_agent(agent) before start()."
            )
        DurableAIAgentWorker(self._worker).add_agent(self._agent)

        # Register orchestrations and activities
        self._worker.add_orchestrator(investigation_with_approval)
        self._worker.add_activity(execute_destructive_action)
        self._worker.add_activity(notify_approval_required)
        
        # Start worker in background thread
        self._thread = threading.Thread(
            target=self._worker.start, name="durable-worker", daemon=True
        )
        self._thread.start()
        logger.info(f"[DurableTask] Worker started — endpoint={DTS_ENDPOINT} hub={DTS_TASKHUB}")
    
    def stop(self):
        """Stop the worker gracefully."""
        if self._worker:
            self._worker.stop()
            logger.info("[DurableTask] Worker stopped")
    
    def start_investigation(
        self, session_id: str, user_message: str,
        destructive_action: Optional[str] = None, context: Optional[dict] = None
    ) -> str:
        """Start a new investigation orchestration. Returns instance_id."""
        instance_id = f"inv-{uuid.uuid4().hex[:12]}"
        request = json.dumps({
            "session_id": session_id,
            "user_message": user_message,
            "destructive_action": destructive_action,
            "context": context,
        })
        self._client.schedule_new_orchestration(
            investigation_with_approval, input=request, instance_id=instance_id
        )
        logger.info(f"[DurableTask] Orchestration started: {instance_id}")
        return instance_id
    
    def approve(self, instance_id: str, approver: str = "user", feedback: str = ""):
        """Approve a pending orchestration."""
        self._client.raise_orchestration_event(
            instance_id, "HumanApproval",
            data=json.dumps({"approved": True, "approver": approver, "feedback": feedback})
        )
        logger.info(f"[DurableTask] ✅ Approved: {instance_id} by {approver}")
    
    def reject(self, instance_id: str, approver: str = "user", feedback: str = ""):
        """Reject a pending orchestration."""
        self._client.raise_orchestration_event(
            instance_id, "HumanApproval",
            data=json.dumps({"approved": False, "approver": approver, "feedback": feedback})
        )
        logger.info(f"[DurableTask] ❌ Rejected: {instance_id} by {approver}")
    
    def get_status(self, instance_id: str):
        """Get orchestration status."""
        return self._client.get_instance_metadata(instance_id)


# Module-level singleton
durable_manager = DurableTaskManager()

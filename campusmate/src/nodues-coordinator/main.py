"""NoDues Coordinator — MAF AgentRuntime with streaming ResponseEventStream.

Campus no-dues coordinator agent for Kovai Institute of Technology (KIT).
Checks pending dues, gates the final certificate behind human-in-the-loop approval,
and saves the dossier to persistent storage.

Uses MAF (Microsoft Agent Framework) AgentRuntime with FoundryChatClient
for proper tool orchestration, and ResponseEventStream for streaming output.
"""

import asyncio
import json
import logging
import os
import pathlib
import re
from datetime import datetime, timezone

from dotenv import load_dotenv

load_dotenv(override=False)

from azure.identity import DefaultAzureCredential
from azure.ai.agentserver.responses import (
    ResponseContext,
    ResponseEventStream,
    ResponsesAgentServerHost,
    ResponsesServerOptions,
    get_input_expanded,
)
from azure.ai.agentserver.responses.models import CreateResponse
from agent_framework import FunctionTool
from agent_framework_foundry import FoundryChatClient

from agent_framework.observability import enable_instrumentation

enable_instrumentation(enable_sensitive_data=True)

# ── Agent name and logger ────────────────────────────────────────────────────


def _read_agent_name() -> str:
    try:
        yaml_text = pathlib.Path("agent.yaml").read_text()
        m = re.search(r"^name:\s*(.+)$", yaml_text, re.MULTILINE)
        return m.group(1).strip() if m else "nodues-coordinator"
    except Exception:
        return "nodues-coordinator"


AGENT_NAME = _read_agent_name()
logger = logging.getLogger(AGENT_NAME)

# ── Configuration ─────────────────────────────────────────────────────────────

PROJECT_ENDPOINT = os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
if not PROJECT_ENDPOINT:
    raise ValueError("FOUNDRY_PROJECT_ENDPOINT must be set")

MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "") or os.getenv(
    "AZURE_AI_MODEL_DEPLOYMENT_NAME", "gpt-5"
)

# Home directory for file persistence — dossiers survive scale-to-zero
HOME_DIR = pathlib.Path(os.environ.get("HOME", "/tmp"))
NODUES_DIR = HOME_DIR / "nodues"

# ── Sample Student Data ────────────────────────────────────────────────────────
#
# DEMO POINT: one deliberate pending hostel due so the demo has a real obstacle
# to resolve on stage. The coordinator will ask for officer approval before
# issuing the certificate.

STUDENT = {
    "id": "22CSE114",
    "name": "Arjun",
    "dept": "CSE",
    "semester": 6,
    "dues": {"library": 0, "lab": 0, "hostel": 1500, "fees": 0},  # rupees pending
}


# ── Tool Implementations ─────────────────────────────────────────────────────


def check_dues(department: str = "") -> str:
    """Check pending dues (in rupees) for one department (library|lab|hostel|fees),
    or all departments if none is given.

    Args:
        department: Department name to check ('library', 'lab', 'hostel', 'fees').
                    Leave empty to check all departments.
    """
    if department:
        amt = STUDENT["dues"].get(department.lower(), 0)
        return json.dumps({"department": department, "pending_inr": amt, "cleared": amt == 0})
    return json.dumps({
        "student": STUDENT["name"],
        "student_id": STUDENT["id"],
        "dues": STUDENT["dues"],
        "all_cleared": all(v == 0 for v in STUDENT["dues"].values()),
    })


def clear_due(department: str) -> str:
    """Mark a department's due as paid/cleared (simulates the department responding).

    Args:
        department: Department whose due to clear ('library', 'lab', 'hostel', 'fees').
    """
    dept_key = department.lower()
    if dept_key not in STUDENT["dues"]:
        return json.dumps({"status": "error", "reason": f"Unknown department: {department}"})
    STUDENT["dues"][dept_key] = 0
    return json.dumps({"department": department, "status": "cleared"})


def request_approval(
    action: str, description: str, severity: str = "critical", context: str = ""
) -> str:
    """Request human approval before issuing the no-dues certificate.

    IMPORTANT: You MUST call this tool before issuing the no-dues certificate.
    The certificate will NOT be issued until a human officer approves it.

    Args:
        action: Short title of the proposed action (e.g. "Issue No-Dues Certificate for Arjun").
        description: Detailed explanation of what will happen and current dues status.
        severity: Risk level - "critical" or "warning".
        context: Additional context such as student ID, remaining dues, semester.
    """
    import uuid

    instance_id = f"inv-{uuid.uuid4().hex[:12]}"

    # NOTE: This tool must NOT schedule a DTS orchestration itself.
    # The durable approval gate IS the `investigation_with_approval`
    # orchestration, and that orchestration runs this same agent in Step 1 —
    # so if request_approval scheduled a new orchestration, every orchestration
    # would spawn another via its own agent step, creating an unbounded
    # feedback loop. Orchestration scheduling is owned by the trigger boundary
    # (the demo backend / entry point), not by this tool. Here we only surface
    # the approval request (return value + Teams card).

    # Proactively push the approval adaptive card to the officer in Teams (best effort).
    teams_notified = False
    try:
        from teams_bot import send_approval_card_sync

        teams_notified = send_approval_card_sync(
            action=action,
            description=description,
            severity=severity,
            approval_id=instance_id,
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("Teams approval card not sent: %s", e)

    result = {
        "status": "approval_pending",
        "approval_id": instance_id,
        "action": action,
        "description": description,
        "severity": severity,
        "teams_notified": teams_notified,
        "message": (
            f"⚠️ APPROVAL REQUIRED\n\n"
            f"Action: {action}\n"
            f"Impact: {description}\n"
            f"Severity: {severity}\n"
            f"Approval ID: {instance_id}\n\n"
            f"The no-dues certificate is paused and waiting for officer approval. "
            f"The officer must approve before the certificate can be issued."
        ),
    }
    return json.dumps(result, indent=2)


def issue_no_dues_certificate(officer_approved: bool = False) -> str:
    """Issue the final No-Dues certificate. Requires officer_approved=True
    (human-in-the-loop) and that NO dues remain. Refuses otherwise.

    Args:
        officer_approved: Set to True only after request_approval has been granted.
    """
    if not officer_approved:
        return json.dumps({"status": "blocked", "reason": "officer approval required"})
    if any(v > 0 for v in STUDENT["dues"].values()):
        return json.dumps({
            "status": "blocked",
            "reason": "dues still pending",
            "dues": STUDENT["dues"],
        })
    cert_id = f"NOC-{STUDENT['id']}-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    return json.dumps({
        "status": "issued",
        "certificate_id": cert_id,
        "student": STUDENT["name"],
        "student_id": STUDENT["id"],
        "dept": STUDENT["dept"],
        "semester": STUDENT["semester"],
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "message": f"No-Dues Certificate {cert_id} issued successfully for {STUDENT['name']}.",
    })


def save_request(filename: str, content: str, student_id: str = "") -> str:
    """Save a no-dues request dossier to persistent storage for audit trail.

    Args:
        filename: Name for the file (e.g., 'arjun-nodues-request.json').
        content: Content to save (dues status, approval notes, etc.).
        student_id: Student ID for cross-reference.
    """
    try:
        NODUES_DIR.mkdir(parents=True, exist_ok=True)
        filepath = NODUES_DIR / filename
        header = f"# No-Dues Request Dossier: {filename}\n"
        header += f"# Saved: {datetime.now(timezone.utc).isoformat()}\n"
        if student_id:
            header += f"# Student ID: {student_id}\n"
        header += "---\n\n"
        filepath.write_text(header + content)
        return json.dumps(
            {
                "status": "success",
                "message": f"Dossier saved to {filepath}",
                "path": str(filepath),
                "size_bytes": filepath.stat().st_size,
            },
            indent=2,
        )
    except Exception as e:
        return json.dumps(
            {"status": "error", "message": f"Failed to save: {e}"}, indent=2
        )


# ── System Prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are the NoDues Coordinator for Kovai Institute of Technology (KIT).

Your job is to help students obtain a No-Dues certificate before graduation or semester exit.
You check each department's pending dues, facilitate clearance, and issue the final certificate
only after all dues are cleared AND an officer has approved the request.

When a student asks for a no-dues certificate:
1. Call check_dues() (no arguments) to get the full dues picture for the student.
2. Report which departments have pending dues and the amounts in rupees.
3. If any dues remain, explain which department needs to be cleared first.
4. For the demo: call request_approval() to trigger the human-in-the-loop gate.
5. Only call issue_no_dues_certificate(officer_approved=True) AFTER the officer approves.
6. Save the final dossier with save_request() for the audit trail.

CRITICAL RULE: Never call issue_no_dues_certificate without first calling request_approval.
IMPORTANT: If the student or officer says "Approved", "Proceed", or confirms a pending approval,
do NOT call request_approval again. The approval has already been granted.
If the officer message includes confirmed due amounts or states dues are cleared, first call
clear_due() for each cleared department, then call issue_no_dues_certificate(officer_approved=True).
Never call issue_no_dues_certificate while any dues remain unpaid in the system.

When reporting dues:
- Be empathetic — students are under semester-end pressure
- State amounts clearly in rupees
- Suggest the student visit the relevant department counter to pay

Guidelines:
- Always check all dues before making recommendations
- Save the dossier for any multi-step interaction
- Be concise but thorough in status updates
- Reference the certificate ID for traceability once issued"""


# ── MAF Agent Setup ───────────────────────────────────────────────────────────

credential = DefaultAzureCredential()

_agent = None
_agent_lock = asyncio.Lock()


def _create_agent():
    """Create and return the MAF agent with local function tools."""
    chat_client = FoundryChatClient(
        project_endpoint=PROJECT_ENDPOINT,
        model=MODEL_DEPLOYMENT_NAME,
        credential=credential,
        allow_preview=True,
    )

    tools = [
        FunctionTool(func=check_dues,                 name="check_dues"),
        FunctionTool(func=clear_due,                  name="clear_due"),
        FunctionTool(func=request_approval,           name="request_approval"),
        FunctionTool(func=issue_no_dues_certificate,  name="issue_no_dues_certificate"),
        FunctionTool(func=save_request,               name="save_request"),
    ]

    agent = chat_client.as_agent(
        name=AGENT_NAME,
        instructions=SYSTEM_PROMPT,
        tools=tools,
    )

    logger.info(
        "[%s] starting up (model=%s, endpoint=%s)",
        AGENT_NAME,
        MODEL_DEPLOYMENT_NAME,
        PROJECT_ENDPOINT,
    )
    return agent


async def _get_agent():
    global _agent
    if _agent is not None:
        return _agent
    async with _agent_lock:
        if _agent is not None:
            return _agent
        _agent = _create_agent()
        return _agent


# ── Server ────────────────────────────────────────────────────────────────────

responses = ResponsesAgentServerHost(
    options=ResponsesServerOptions(default_fetch_history_count=20),
)


def _get_input_text(request: CreateResponse) -> str | None:
    """Extract plain text from a CreateResponse input."""
    inp = request.input
    if isinstance(inp, str):
        return inp
    items = get_input_expanded(request)
    for item in items:
        content = getattr(item, "content", None)
        if content is None:
            continue
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            for part in content:
                text = getattr(part, "text", None)
                if text:
                    return text
    return None


@responses.response_handler
async def handle_response(
    request: CreateResponse,
    context: ResponseContext,
    cancellation_signal: asyncio.Event,
):
    stream = ResponseEventStream(
        response_id=context.response_id,
        model=getattr(request, "model", None),
    )

    yield stream.emit_created()
    yield stream.emit_in_progress()

    user_input = _get_input_text(request) or ""
    if not user_input:
        message_item = stream.add_output_item_message()
        yield message_item.emit_added()
        for event in message_item.text_content("No input provided."):
            yield event
        yield message_item.emit_done()
        yield stream.emit_completed()
        return

    try:
        agent = await _get_agent()
        result = await asyncio.wait_for(
            agent.run(messages=user_input, stream=False),
            timeout=120.0,
        )
        # Extract text from MAF AgentResponse
        assistant_reply = (
            str(result.message) if hasattr(result, "message") else str(result)
        )
        if not assistant_reply:
            assistant_reply = "(Agent completed without text response)"
    except asyncio.TimeoutError:
        assistant_reply = "Request timed out. Please retry with a simpler query."
    except asyncio.CancelledError:
        assistant_reply = "Request was cancelled. Please retry."
    except Exception as e:
        logger.error("Failed to process request: %s", e, exc_info=True)
        assistant_reply = f"I encountered an error processing your request: {e}"

    message_item = stream.add_output_item_message()
    yield message_item.emit_added()

    for event in message_item.text_content(assistant_reply):
        yield event
    yield message_item.emit_done()

    yield stream.emit_completed()


# --- Durable Task Extension ---
# Eagerly create the MAF agent and register it as a durable agent
# (chat history + tool calls auto-persisted to DTS by the Durable
# Extension for Microsoft Agent Framework).
from durable_orchestration import durable_manager

_agent = _create_agent()
durable_manager.register_agent(_agent)

from durable_startup import bootstrap

bootstrap()

# --- Teams Activity Protocol passthrough ---
# Mount POST /api/messages on the same agentserver port so the Foundry
# activity-protocol endpoint can forward raw Bot Framework activities here.
import teams_bot

teams_bot.configure(agent_getter=_get_agent, durable_manager=durable_manager)
responses.add_route("/api/messages", teams_bot.handle_messages, methods=["POST"])

responses.run()

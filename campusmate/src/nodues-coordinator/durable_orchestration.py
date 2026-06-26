"""Durable orchestration for human-in-the-loop approval in the No-Dues Coordinator.

When DURABLE_TASK_ENDPOINT is set, uses Azure Durable Task (Managed).
Otherwise falls back to an in-process approval store so the agent works
offline without any Azure dependencies.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any


# ---------------------------------------------------------------------------
# In-process fallback store
# ---------------------------------------------------------------------------

class _InProcessStore:
    """Simple in-memory approval store used when Durable Task is not configured."""

    def __init__(self) -> None:
        self._instances: dict[str, dict[str, Any]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._decisions: dict[str, str] = {}

    def start_instance(self, instance_id: str, input_data: dict[str, Any]) -> None:
        self._instances[instance_id] = {
            "id": instance_id,
            "status": "Pending",
            "input": input_data,
            "output": None,
        }
        self._events[instance_id] = asyncio.Event()

    def raise_event(self, instance_id: str, decision: str) -> None:
        self._decisions[instance_id] = decision
        inst = self._instances.get(instance_id)
        if inst:
            inst["status"] = "Completed"
            inst["output"] = decision
        ev = self._events.get(instance_id)
        if ev:
            ev.set()

    def get_status(self, instance_id: str) -> dict[str, Any] | None:
        return self._instances.get(instance_id)

    async def wait_for_decision(self, instance_id: str, timeout: float = 300.0) -> str:
        ev = self._events.get(instance_id)
        if ev is None:
            return "not_found"
        try:
            await asyncio.wait_for(ev.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return "timeout"
        return self._decisions.get(instance_id, "unknown")


_in_process_store = _InProcessStore()


# ---------------------------------------------------------------------------
# Durable manager (public API)
# ---------------------------------------------------------------------------

class _DurableManager:
    """Unified API for durable orchestration, with in-process fallback."""

    def _use_durable(self) -> bool:
        return bool(os.getenv("DURABLE_TASK_ENDPOINT", "").strip())

    def start_approval_orchestration(
        self,
        action: str,
        description: str,
        context: str = "",
        severity: str = "critical",
        student_id: str = "",
    ) -> str:
        """Start an approval orchestration and return the instance ID."""
        instance_id = str(uuid.uuid4())
        input_data = {
            "action": action,
            "description": description,
            "context": context,
            "severity": severity,
            "student_id": student_id,
        }

        if self._use_durable():
            try:
                self._start_durable(instance_id, input_data)
                return instance_id
            except Exception as exc:
                print(f"[durable] Durable Task start failed ({exc}), using in-process store.")

        _in_process_store.start_instance(instance_id, input_data)
        return instance_id

    def _start_durable(self, instance_id: str, input_data: dict[str, Any]) -> None:
        """Start a real Durable Task orchestration."""
        try:
            from durabletask import client as durable_client  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("durabletask package not installed") from exc

        endpoint = os.getenv("DURABLE_TASK_ENDPOINT", "")
        task_hub = os.getenv("DURABLE_TASK_TASKHUB", "campusmate-approvals")

        c = durable_client.TaskHubGrpcClient(host_address=endpoint)
        c.schedule_new_orchestration(
            "approval_orchestration",
            input=input_data,
            instance_id=instance_id,
        )

    def approve(self, instance_id: str) -> bool:
        """Signal approval for a pending orchestration."""
        return self._raise_event(instance_id, "approved")

    def reject(self, instance_id: str) -> bool:
        """Signal rejection for a pending orchestration."""
        return self._raise_event(instance_id, "rejected")

    def _raise_event(self, instance_id: str, decision: str) -> bool:
        if self._use_durable():
            try:
                from durabletask import client as durable_client  # type: ignore[import]

                endpoint = os.getenv("DURABLE_TASK_ENDPOINT", "")
                c = durable_client.TaskHubGrpcClient(host_address=endpoint)
                c.raise_orchestration_event(
                    instance_id, "HumanApproval", data=decision
                )
                return True
            except Exception as exc:
                print(f"[durable] raise_event failed ({exc}), falling back.")

        _in_process_store.raise_event(instance_id, decision)
        return True

    def get_status(self, instance_id: str) -> dict[str, Any] | None:
        if self._use_durable():
            try:
                from durabletask import client as durable_client  # type: ignore[import]

                endpoint = os.getenv("DURABLE_TASK_ENDPOINT", "")
                c = durable_client.TaskHubGrpcClient(host_address=endpoint)
                state = c.get_orchestration_state(instance_id)
                if state is None:
                    return None
                return {
                    "id": instance_id,
                    "status": str(state.runtime_status),
                    "output": state.serialized_output,
                }
            except Exception as exc:
                print(f"[durable] get_status failed ({exc}), using in-process store.")

        return _in_process_store.get_status(instance_id)


durable_manager = _DurableManager()


# ---------------------------------------------------------------------------
# Orchestration function (registered with Durable Task worker)
# ---------------------------------------------------------------------------

async def approval_orchestration(ctx: Any, input_data: dict[str, Any]) -> str:
    """Durable orchestration that waits for a human approval event."""
    decision: str = await ctx.wait_for_external_event("HumanApproval")
    return decision

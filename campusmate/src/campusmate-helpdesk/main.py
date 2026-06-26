"""CampusMate helpdesk — entry point.

Starts the ResponsesAgentServerHost and registers the response handler
that drives the router → worker flow.
"""
from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

from dotenv import load_dotenv

load_dotenv()

# Instrumentation must be enabled before importing agent SDK components.
try:
    from azure.ai.agentserver.core.tracing import enable_instrumentation  # type: ignore[import]

    enable_instrumentation(
        enable_sensitive_data=os.getenv("ENABLE_SENSITIVE_DATA", "false").lower() == "true"
    )
except ImportError:  # pragma: no cover
    print("[main] Instrumentation not available — running without telemetry.")

try:
    from azure.ai.agentserver.responses import ResponsesAgentServerHost  # type: ignore[import]
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        "azure-ai-agentserver-responses is not installed.\n"
        "Run: pip install azure-ai-agentserver-responses==1.0.0b5"
    ) from exc

from router_agent import route
from task_store import TaskStatus, task_store
from worker_agent import create_worker_agent

app = ResponsesAgentServerHost()

# Lazily initialised worker agent (created on first request).
_worker_agent = None
_worker_lock = threading.Lock()


def _get_worker():
    global _worker_agent
    if _worker_agent is None:
        with _worker_lock:
            if _worker_agent is None:
                _worker_agent = create_worker_agent()
    return _worker_agent


def _run_worker_task(task_id: str, task_description: str, conversation: list[dict[str, Any]]) -> None:
    """Execute the worker agent in a background thread and store the result."""
    task_store.set_running(task_id)
    try:
        worker = _get_worker()
        result = worker.run(task_description, context=conversation)  # type: ignore[union-attr]
        task_store.set_completed(task_id, result)
    except Exception as exc:
        task_store.set_failed(task_id, str(exc))


@app.response_handler
async def handle_request(request: Any, response_stream: Any) -> None:  # type: ignore[type-arg]
    """Main response handler — routes each student message to the right action."""
    messages: list[dict[str, Any]] = request.messages or []

    # --- Route the request ---
    try:
        decision = route(messages)
    except Exception as exc:
        await response_stream.send_text(f"Sorry, I couldn't process your request: {exc}")
        return

    action: str = decision["action"]
    args: dict[str, Any] = decision["args"]

    # --- Handle each routing action ---

    if action == "respond_directly":
        await response_stream.send_text(args.get("message", ""))

    elif action == "start_task":
        task_desc: str = args.get("task_description", "")
        ack: str = args.get("ack_message", "Let me look into that for you — just a moment!")

        # Send ack immediately so the student isn't waiting.
        await response_stream.send_text(ack)

        # Start background task.
        task_id = task_store.create(task_desc)
        thread = threading.Thread(
            target=_run_worker_task,
            args=(task_id, task_desc, messages),
            daemon=True,
        )
        thread.start()

        # Poll briefly for a fast answer (up to 8 s).
        for _ in range(16):
            await asyncio.sleep(0.5)
            status = task_store.get_status(task_id)
            if status == TaskStatus.COMPLETED:
                result = task_store.get_result(task_id)
                await response_stream.send_text(f"\n\n{result}")
                return
            if status in (TaskStatus.FAILED, TaskStatus.CANCELLED):
                break

        status = task_store.get_status(task_id)
        if status == TaskStatus.COMPLETED:
            result = task_store.get_result(task_id)
            await response_stream.send_text(f"\n\n{result}")
        elif status == TaskStatus.FAILED:
            task = task_store.get(task_id)
            await response_stream.send_text(
                f"\n\nSorry, I ran into a problem: {task['error'] if task else 'unknown error'}. "
                "Please try again."
            )
        else:
            await response_stream.send_text(
                f"\n\nYour request is still being processed (task ID: **{task_id}**). "
                "Ask me for the result when you're ready."
            )

    elif action == "check_task_status":
        task_id = args.get("task_id", "")
        task = task_store.get(task_id)
        if not task:
            await response_stream.send_text(f"Task `{task_id}` not found.")
        else:
            await response_stream.send_text(
                f"Task `{task_id}` is currently **{task['status']}**."
            )

    elif action == "cancel_task":
        task_id = args.get("task_id", "")
        cancelled = task_store.cancel(task_id)
        if cancelled:
            await response_stream.send_text(f"Task `{task_id}` has been cancelled.")
        else:
            await response_stream.send_text(
                f"Task `{task_id}` could not be cancelled — it may have already finished."
            )

    elif action == "get_task_result":
        task_id = args.get("task_id", "")
        task = task_store.get(task_id)
        if not task:
            await response_stream.send_text(f"Task `{task_id}` not found.")
        elif task["status"] == TaskStatus.COMPLETED:
            await response_stream.send_text(task["result"] or "Task completed with no output.")
        elif task["status"] == TaskStatus.FAILED:
            await response_stream.send_text(
                f"Task `{task_id}` failed: {task['error']}"
            )
        else:
            await response_stream.send_text(
                f"Task `{task_id}` is still **{task['status']}** — please check back soon."
            )

    else:
        await response_stream.send_text(
            "I'm not sure how to handle that request. Could you rephrase?"
        )


if __name__ == "__main__":
    app.run()

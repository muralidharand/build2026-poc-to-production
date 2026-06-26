"""Startup helper — launches the durable worker and approval web server
alongside the main agent server.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any


async def start_durable_worker() -> None:
    """Start the Durable Task worker if DURABLE_TASK_ENDPOINT is configured."""
    endpoint = os.getenv("DURABLE_TASK_ENDPOINT", "").strip()
    if not endpoint:
        print("[durable_startup] DURABLE_TASK_ENDPOINT not set — skipping durable worker.")
        return

    try:
        from durabletask import worker as durable_worker  # type: ignore[import]
        from durable_orchestration import approval_orchestration  # type: ignore[import]

        task_hub = os.getenv("DURABLE_TASK_TASKHUB", "campusmate-approvals")

        w = durable_worker.TaskHubGrpcWorker(host_address=endpoint, task_hub_name=task_hub)
        w.add_orchestrator(approval_orchestration)

        print(f"[durable_startup] Starting durable worker on {endpoint} (hub={task_hub})")
        await w.start()
    except ImportError as exc:
        print(f"[durable_startup] durabletask not installed — {exc}")
    except Exception as exc:
        print(f"[durable_startup] Durable worker error: {exc}")


async def start_background_services() -> None:
    """Launch the durable worker and approval web server as background tasks."""
    from approval_web import run_approval_server  # type: ignore[import]

    tasks = [
        asyncio.create_task(run_approval_server(), name="approval-web"),
        asyncio.create_task(start_durable_worker(), name="durable-worker"),
    ]

    # Fire-and-forget — caller owns the event loop lifetime.
    for task in tasks:
        task.add_done_callback(_on_task_done)


def _on_task_done(task: asyncio.Task) -> None:  # type: ignore[type-arg]
    exc = task.exception() if not task.cancelled() else None
    if exc:
        print(f"[durable_startup] Background task {task.get_name()!r} failed: {exc}")

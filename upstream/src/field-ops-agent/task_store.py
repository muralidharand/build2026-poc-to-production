"""Task Store — shared state between Router and Worker agents.

Supports Demo Concept #3 (voice routing pattern). Tracks background tasks
through their lifecycle: QUEUED → RUNNING → COMPLETED → DELIVERED.

In production, swap the in-memory dict for Redis or Cosmos DB. The shape of
``Task`` and the ``TaskStore`` API stay the same.

Key behaviours:
  - ``collect_completed()`` is atomic: each finished result is delivered to
    the user exactly once (marks COMPLETED → DELIVERED).
  - ``delivered_tasks()`` powers follow-up recall ("what was that again?")
    without re-running the worker.
  - ``Task.asyncio_task`` lets the router cancel the underlying MAF
    ``agent.run()`` mid-flight (MAF's loop is a single black-box call;
    setting ``cancel_event`` alone wouldn't interrupt it).
"""

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum


class TaskStatus(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass
class Task:
    task_id: str
    query: str
    status: TaskStatus = TaskStatus.QUEUED
    current_tool: str | None = None
    rounds_completed: int = 0
    result: str | None = None
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: float = field(default_factory=time.time)
    # Handle to the asyncio Task running the worker. Used by cancel_task to
    # interrupt the MAF agent.run() (which is a single black-box call and
    # can only be aborted via Task.cancel()).
    asyncio_task: asyncio.Task | None = None


class TaskStore:
    """In-memory task registry. One instance shared across the app."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}

    def create_task(self, query: str) -> Task:
        task_id = uuid.uuid4().hex[:8]
        task = Task(task_id=task_id, query=query)
        self._tasks[task_id] = task
        return task

    def get(self, task_id: str) -> Task | None:
        if task_id == "latest":
            return self._get_latest()
        return self._tasks.get(task_id)

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued or running task.

        Sets ``cancel_event`` (cooperative signal, observed by the worker
        runner) AND transitions the status. For QUEUED tasks we mark
        CANCELLED immediately so a worker that hasn't started yet won't run.
        RUNNING tasks stay in RUNNING here — ``run_worker`` is responsible
        for the final transition (CANCELLED on ``CancelledError``, FAILED on
        timeout, etc.) once the in-flight ``agent.run()`` unwinds.
        """
        task = self.get(task_id)
        if not task or task.status not in (TaskStatus.QUEUED, TaskStatus.RUNNING):
            return False
        task.cancel_event.set()
        if task.status == TaskStatus.QUEUED:
            task.status = TaskStatus.CANCELLED
            task.result = "(Cancelled by user)"
        return True

    def active_tasks(self) -> list[Task]:
        return [t for t in self._tasks.values()
                if t.status in (TaskStatus.QUEUED, TaskStatus.RUNNING)]

    def completed_tasks(self) -> list[Task]:
        return [t for t in self._tasks.values()
                if t.status == TaskStatus.COMPLETED]

    def delivered_tasks(self) -> list[Task]:
        """Return tasks that have been delivered to the user (for context recall)."""
        return [t for t in self._tasks.values()
                if t.status == TaskStatus.DELIVERED]

    def peek_completed(self) -> list[Task]:
        """Return all completed tasks WITHOUT marking them delivered.

        Use this when you want to surface completed results to the router but
        don't yet know whether they will actually be emitted to the user this
        turn. Pair with :meth:`mark_delivered` once they've been emitted.
        """
        return [t for t in self._tasks.values() if t.status == TaskStatus.COMPLETED]

    def mark_delivered(self, task_ids) -> None:
        """Transition the given COMPLETED tasks to DELIVERED.

        Idempotent: tasks already in another terminal state are left alone.
        """
        for tid in task_ids:
            task = self._tasks.get(tid)
            if task and task.status == TaskStatus.COMPLETED:
                task.status = TaskStatus.DELIVERED

    def collect_completed(self) -> list[Task]:
        """Return all completed tasks and mark them as delivered.

        Equivalent to ``peek_completed()`` followed by ``mark_delivered(...)``.
        Use only when you're certain the caller will emit the results on the
        same turn — otherwise prefer the peek/mark pair so unread results
        survive to the next turn.
        """
        ready = self.peek_completed()
        self.mark_delivered(t.task_id for t in ready)
        return ready

    def cleanup(self):
        """Remove delivered/cancelled/failed tasks older than 5 minutes."""
        import time as _time
        cutoff = _time.time() - 300
        to_remove = [
            tid for tid, t in self._tasks.items()
            if t.status in (TaskStatus.DELIVERED, TaskStatus.CANCELLED, TaskStatus.FAILED)
            and t.created_at < cutoff
        ]
        for tid in to_remove:
            del self._tasks[tid]

    def _get_latest(self) -> Task | None:
        if not self._tasks:
            return None
        return list(self._tasks.values())[-1]

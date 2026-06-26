"""In-memory task/status store for the CampusMate helpdesk router."""
from __future__ import annotations

import threading
import uuid
from enum import Enum
from typing import Any


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStore:
    """Thread-safe in-memory store for background tasks."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tasks: dict[str, dict[str, Any]] = {}

    def create(self, description: str = "") -> str:
        task_id = str(uuid.uuid4())[:8]
        with self._lock:
            self._tasks[task_id] = {
                "id": task_id,
                "status": TaskStatus.PENDING,
                "description": description,
                "result": None,
                "error": None,
            }
        return task_id

    def set_running(self, task_id: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = TaskStatus.RUNNING

    def set_completed(self, task_id: str, result: Any) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = TaskStatus.COMPLETED
                self._tasks[task_id]["result"] = result

    def set_failed(self, task_id: str, error: str) -> None:
        with self._lock:
            if task_id in self._tasks:
                self._tasks[task_id]["status"] = TaskStatus.FAILED
                self._tasks[task_id]["error"] = error

    def cancel(self, task_id: str) -> bool:
        with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                if task["status"] in (TaskStatus.PENDING, TaskStatus.RUNNING):
                    task["status"] = TaskStatus.CANCELLED
                    return True
        return False

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_status(self, task_id: str) -> TaskStatus | None:
        task = self.get(task_id)
        return task["status"] if task else None

    def get_result(self, task_id: str) -> Any | None:
        task = self.get(task_id)
        if task and task["status"] == TaskStatus.COMPLETED:
            return task["result"]
        return None


# Module-level singleton
task_store = TaskStore()

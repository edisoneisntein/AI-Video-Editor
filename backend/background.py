"""
Background task runner using Python threading.

No Redis, no Celery, no external dependencies.
Tasks run in daemon threads and store their state in a simple dict.
The frontend polls GET /api/task/{task_id} every few seconds.

Flow:
  1. Endpoint receives request → validates → creates task → returns 202 immediately
  2. Task runs in background thread (analyze or render)
  3. Frontend polls /api/task/{task_id} until status is "completed" or "failed"
  4. On completion, result is stored in SQLite (edit_plan or output_path)
"""

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TaskInfo:
    """State of a background task."""

    task_id: str
    task_type: str  # "analyze" or "render"
    project_id: str
    status: TaskStatus = TaskStatus.PENDING
    progress: str = ""  # human-readable progress message
    result: dict | None = None  # result data on completion
    error: str | None = None  # error message on failure
    created_at: float = 0.0
    completed_at: float = 0.0

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type,
            "project_id": self.project_id,
            "status": self.status.value,
            "progress": self.progress,
            "result": self.result,
            "error": self.error,
            "elapsed": round(time.time() - self.created_at, 1) if self.created_at else 0,
        }


# ─── Global task registry ────────────────────────────────────────────────────────

_tasks: dict[str, TaskInfo] = {}
_lock = threading.Lock()


def create_task(task_type: str, project_id: str, target, args=(), kwargs=None) -> str:
    """
    Create and start a background task.

    Args:
        task_type: "analyze" or "render"
        project_id: Associated project ID
        target: The function to run in background
        args: Positional arguments for the function
        kwargs: Keyword arguments for the function

    Returns:
        task_id (str) — use this to poll status
    """
    task_id = f"{task_type}_{uuid.uuid4().hex[:8]}"
    kwargs = kwargs or {}

    task_info = TaskInfo(
        task_id=task_id,
        task_type=task_type,
        project_id=project_id,
        status=TaskStatus.PENDING,
        created_at=time.time(),
    )

    with _lock:
        _tasks[task_id] = task_info

    # Wrap the target to update task state
    def _worker():
        task = _tasks[task_id]
        task.status = TaskStatus.RUNNING
        task.progress = "Starting..."

        try:
            result = target(*args, task_info=task, **kwargs)
            task.status = TaskStatus.COMPLETED
            task.result = result
            task.completed_at = time.time()
            logger.info(f"[Task {task_id}] Completed in {task.completed_at - task.created_at:.1f}s")
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = f"{type(e).__name__}: {str(e)[:300]}"
            task.completed_at = time.time()
            logger.error(f"[Task {task_id}] Failed: {task.error}")

    thread = threading.Thread(target=_worker, daemon=True, name=f"task-{task_id}")
    thread.start()

    logger.info(f"[Task {task_id}] Started ({task_type} for project {project_id})")
    return task_id


def get_task(task_id: str) -> TaskInfo | None:
    """Get task info by ID."""
    return _tasks.get(task_id)


def get_tasks_for_project(project_id: str) -> list[TaskInfo]:
    """Get all tasks for a project."""
    return [t for t in _tasks.values() if t.project_id == project_id]


def cleanup_old_tasks(max_age_seconds: float = 3600):
    """Remove completed/failed tasks older than max_age."""
    now = time.time()
    with _lock:
        to_remove = [
            tid for tid, t in _tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
            and t.completed_at > 0
            and (now - t.completed_at) > max_age_seconds
        ]
        for tid in to_remove:
            del _tasks[tid]

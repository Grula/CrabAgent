from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    WAITING_USER = "waiting_user"
    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


OPEN_TASK_STATUSES = (
    TaskStatus.PENDING.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.WAITING_USER.value,
    TaskStatus.PARTIAL.value,
    TaskStatus.FAILED.value,
)
CLOSED_TASK_STATUSES = (
    TaskStatus.DONE.value,
    TaskStatus.CANCELLED.value,
)
TASK_STATUS_VALUES = tuple(status.value for status in TaskStatus)


def validate_task_status(status: str) -> str:
    if status not in TASK_STATUS_VALUES:
        allowed = ", ".join(TASK_STATUS_VALUES)
        raise ValueError(f"Invalid task status {status!r}; expected one of: {allowed}")
    return status

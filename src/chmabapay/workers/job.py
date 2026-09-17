"""Job dataclass for worker queue payloads."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(slots=True)
class Job:
    queue_name: str
    payload: dict[str, Any]
    dedup_key: str | None = None
    job_id: uuid.UUID = field(default_factory=uuid.uuid4)
    created_at: datetime = field(default_factory=_utcnow)
    attempts: int = 0
    max_attempts: int = 10
    result: dict[str, Any] | None = None


class JobStatus:
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    DEAD = "dead"

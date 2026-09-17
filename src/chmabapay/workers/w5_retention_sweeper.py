"""W5 RetentionSweeperWorker — enforce the gateway-payload retention window.

Enqueued once a day with a day-granularity dedup key, so several replicas never
purge the same day twice even after Phase 2 scales horizontally. The sweep is
idempotent, so a missed day (a restart, a stopped worker) costs nothing but a
day's delay.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..config import get_settings
from ..db import session_factory
from ..services.retention import purge_gateway_payloads
from .base import Worker
from .job import Job

QUEUE = "payments.retention"


def _now() -> datetime:
    return datetime.now(UTC)


class RetentionSweeperWorker(Worker):
    prefetch = 1
    concurrency = 1  # One sweep at a time, globally.

    queue_name = QUEUE

    async def process(self, job: Job) -> dict[str, Any]:
        settings = get_settings()
        days = settings.retention_gateway_raw_days
        async with session_factory() as session:
            purged = await purge_gateway_payloads(session, older_than_days=days)
        return {
            "ok": True,
            "purged": purged,
            "older_than_days": days,
            "at": _now().isoformat(),
        }


def retention_heartbeat_dedup_key(ts: datetime | None = None) -> str:
    """Day-granularity dedup key: a day's sweep never runs twice."""
    return f"retention-sweep-{(ts or _now()).strftime('%Y-%m-%d')}"


def retention_heartbeat_job_payload() -> dict[str, Any]:
    return {"heartbeat_at": _now().isoformat()}

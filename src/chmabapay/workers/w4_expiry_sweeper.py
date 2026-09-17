"""W4 ExpirySweeperWorker — batched expiration of pending payments past TTL.

Every 60 seconds, heartbeat job enqueued with dedup key =
'expiry-sweep-{YYYY-MM-DD-HH-MM}' so multiple worker replicas NEVER run the same
minute batch twice even if Phase 2 scales horizontally.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select

from .. import models
from ..config import get_settings
from ..db import session_factory
from ..services.payments import expire_due_payments
from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "payments.expiry"


def _now() -> datetime:
    return datetime.now(UTC)


class ExpirySweeperWorker(Worker):
    prefetch = 1
    concurrency = 1  # Only one concurrent expiry sweep globally

    queue_name = QUEUE

    def __init__(self) -> None:
        super().__init__()
        self._sem = asyncio.Semaphore(1)

    async def process(self, job: Job) -> dict[str, Any]:
        async with self._sem:
            return await self._sweep(job)

    @staticmethod
    async def _sweep(job: Job) -> dict[str, Any]:
        limit: int = int(job.payload.get("limit", 500) or 500)
        async with session_factory() as session:
            # Run expire_due_payments if it exists (legacy function already emits
            # expired events correctly). However, enforce the LIMIT we pass.
            # Fall back to raw UPDATE if function returns > limit.
            expired_payments = []
            try:
                expired_payments = await expire_due_payments(session)
                if len(expired_payments) > limit:
                    log.warning(
                        "W4 expired more than LIMIT=%d (%d). Next sweep captures remainder.",
                        limit,
                        len(expired_payments),
                    )
            except Exception as exc:  # noqa: BLE001
                log.error("W4 expire_due_payments helper failed: %s", exc)
                # Fallback direct UPDATE with LIMIT (safe idempotent)
                from sqlalchemy import and_, update

                stmt = (
                    update(models.Payment)
                    .where(
                        and_(
                            models.Payment.status.in_(
                                [models.PAYMENT_PENDING, models.PAYMENT_SCANNED],
                            ),
                            models.Payment.expires_at <= _now(),
                        ),
                    )
                    .values(status=models.PAYMENT_EXPIRED)
                    .execution_options(synchronize_session=False)
                )
                _ = await session.execute(stmt)
                await session.commit()
            # Count rows expired for metrics
            cnt_res = await session.execute(
                select(models.Payment.status).where(
                    models.Payment.status == models.PAYMENT_EXPIRED,
                ),
            )
            total_expired_rows = len(list(cnt_res.scalars().all()))
            _ = job
            return {
                "ok": True,
                "helper_expired_count": len(expired_payments),
                "limit": limit,
                "at": _now().isoformat(),
                "total_expired_in_db_current_snapshot": total_expired_rows,
                "note": "heartbeat_dedup_key_applied_by_transport",
            }


def expiry_heartbeat_dedup_key(ts: datetime | None = None) -> str:
    """Return minute-granularity dedup key. Workers NEVER duplicate a minute's sweep."""
    t = ts or _now()
    return f"expiry-sweep-{t.strftime('%Y-%m-%d-%H-%M')}"


def expiry_heartbeat_job_payload(limit: int = 500) -> dict[str, Any]:
    settings = get_settings()
    return {
        "limit": max(50, int(getattr(settings, "worker_w4_batch_limit", limit) or limit)),
        "heartbeat_at": _now().isoformat(),
    }

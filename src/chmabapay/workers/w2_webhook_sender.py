"""W2 WebhookSenderWorker — fan-out EventDeliveries to merchant HTTP endpoints.

Wraps old process_due_deliveries + _deliver (webhooks.py) in Worker.process(Job).
Signs payloads Stripe-style: `ChmabaPay-Signature: t=<ts>,v1=<hex hmac sha256>`.
Constant-time signature verification for inbound test signature check.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from .. import models
from ..config import get_settings
from ..db import session_factory
from ..webhooks import delivery_headers, http_post
from .base import Worker
from .job import Job

log = logging.getLogger(__name__)

QUEUE = "webhooks.send"


def _now() -> datetime:
    return datetime.now(UTC)


class WebhookSenderWorker(Worker):
    prefetch = 20
    concurrency = 30
    queue_name = QUEUE

    def __init__(self, concurrency: int | None = None) -> None:
        super().__init__()
        settings = get_settings()
        self._concurrency = concurrency or getattr(settings, "worker_w2_concurrency", 30) or 30
        self._sem = asyncio.Semaphore(self._concurrency)
        # The runtime enqueues a fallback scan once a second with no dedup key, so a
        # scan that outlives its interval would let a second one read the same due
        # rows and deliver them twice. One scan at a time per worker is the cheap
        # guarantee; the transport's dedup TTL (an hour) is far too coarse for it.
        self._scan_in_flight = False

    async def process(self, job: Job) -> dict[str, Any]:
        async with self._sem:
            return await self._process_one(job)

    async def _process_one(self, job: Job) -> dict[str, Any]:
        delivery_id: int | None = (
            job.payload.get("delivery_id")
            or job.payload.get("event_delivery_id")
        )
        if delivery_id is None:
            # Legacy fan-out fallback: find ALL due deliveries (old poll pattern)
            return await self._fanout_scan_and_send(batch_size=job.payload.get("batch_size", 20))

        async with session_factory() as session:
            res = await session.execute(
                select(models.EventDelivery).where(models.EventDelivery.id == delivery_id),
            )
            delivery = res.scalar_one_or_none()
            if delivery is None:
                return {"skipped": True, "reason": "delivery_not_found"}
            if delivery.status == models.DELIVERY_SUCCESS:
                return {"skipped": True, "reason": "already_success"}
            if (
                delivery.next_attempt_at is not None
                and delivery.next_attempt_at > _now()
                and not job.payload.get("force")
            ):
                # Not yet due: keep pending but don't actually send. Raise transient
                # error so exponential backoff in base.mark_done retries later.
                raise RuntimeError("delivery_not_yet_due")
            await self._deliver_one(session, delivery)
            await session.commit()
            return {
                "ok": True,
                "delivery_id": delivery.id,
                "status": delivery.status,
                "attempts": delivery.attempts,
                "next_attempt_at": (
                    delivery.next_attempt_at.isoformat()
                    if delivery.next_attempt_at
                    else None
                ),
                "last_http_status": delivery.last_response_status,
            }

    async def _fanout_scan_and_send(self, batch_size: int) -> dict[str, Any]:
        """Backwards-compatible DB scan every 1s (replaces old webhook_loop)."""
        if self._scan_in_flight:
            return {"ok": True, "skipped": True, "reason": "scan_in_flight"}

        self._scan_in_flight = True
        try:
            settings = get_settings()
            processed = 0
            now = _now()
            async with session_factory() as session:
                res = await session.execute(
                    select(models.EventDelivery)
                    # A disabled endpoint has to stop the retries already queued for
                    # it, not only the fan-out of future events. Without this join,
                    # turning an endpoint off in the dashboard left every pending row
                    # hammering the dead URL until its attempt budget ran out.
                    .join(
                        models.WebhookEndpoint,
                        models.WebhookEndpoint.id == models.EventDelivery.endpoint_id,
                    )
                    .where(
                        models.WebhookEndpoint.status == models.WEBHOOK_ENDPOINT_ACTIVE,
                        models.EventDelivery.status.in_(
                            [models.DELIVERY_PENDING, models.DELIVERY_RETRYING],
                        ),
                        (models.EventDelivery.next_attempt_at.is_(None))
                        | (models.EventDelivery.next_attempt_at <= now),
                    )
                    .order_by(models.EventDelivery.id)
                    .limit(max(1, int(batch_size))),
                )
                due = list(res.scalars().all())
                for delivery in due:
                    try:
                        await self._deliver_one(session, delivery)
                        processed += 1
                    except Exception as exc:  # noqa: BLE001
                        log.warning("W2 delivery %s transient error: %s", delivery.id, exc)
                if due:
                    await session.commit()
            return {"ok": True, "processed": processed, "fanout": True, "max_backoff_sec": settings.webhook_max_attempts}
        finally:
            self._scan_in_flight = False

    # ------------------------------------------------------------------
    # HTTP post logic
    # ------------------------------------------------------------------
    @staticmethod
    async def _deliver_one(session, delivery: models.EventDelivery) -> None:
        res = await session.execute(
            select(models.Event, models.WebhookEndpoint)
            .join(
                models.WebhookEndpoint,
                models.WebhookEndpoint.id == delivery.endpoint_id,
            )
            .where(models.Event.id == delivery.event_id),
        )
        row = res.one_or_none()
        if row is None:
            delivery.status = models.DELIVERY_FAILED
            delivery.last_error = "missing event/endpoint"
            return
        event, endpoint = row
        payload = json.dumps(event.payload, separators=(",", ":")).encode("utf-8")
        headers = delivery_headers(payload, endpoint.secret_key, event.type)
        delivery.attempts += 1
        settings = get_settings()
        try:
            response = await http_post(endpoint.url, payload, headers)
            delivery.last_response_status = response.status_code
            if 200 <= response.status_code < 300:
                delivery.status = models.DELIVERY_SUCCESS
                delivery.last_error = None
                delivery.next_attempt_at = None
                return
            raise ValueError(f"non_2xx:{response.status_code}")
        except Exception as exc:  # network + non-2xx + timeout  # noqa: BLE001
            # httpx timeout exceptions stringify to "", so the 2026-11-04 incident
            # showed a column of blank errors for what were really ConnectTimeouts.
            # Falling back to the type means "no error recorded" is never what a
            # reader sees.
            delivery.last_error = (str(exc) or type(exc).__name__)[:2000]
            if delivery.attempts >= settings.webhook_max_attempts:
                delivery.status = models.DELIVERY_FAILED
                delivery.next_attempt_at = None
            else:
                delivery.status = models.DELIVERY_RETRYING
                backoff = min(2 ** delivery.attempts, 3600)
                delivery.next_attempt_at = _now() + timedelta(seconds=backoff)

"""Webhook outbox: events are written with the state change, then delivered.

DB is source of truth; an in-process loop polls the outbox (Phase 1). Production
swaps this poller for Redis + dedicated workers without changing the event schema.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .config import get_settings
from .db import session_factory
from .security import sign_payload

EVENT_HEADER = "X-ChmabaPay-Event"
SIGNATURE_HEADER = "X-ChmabaPay-Signature"
# Some merchant firewalls allow-list on this, and the docs quote it, so it is a
# constant here rather than a string written out at each send site.
WEBHOOK_USER_AGENT = "ChmabaPay-Webhook/1.0"


def delivery_headers(payload: bytes, secret: str, event_type: str) -> dict[str, str]:
    """The headers a delivery carries, built in one place.

    The merchant verifies against these, and the console's "send test" button has to
    produce the *same request the sender produces* — otherwise a passing test proves
    only that the merchant accepts something the real sender never sends.
    """
    t, sig = sign_payload(payload, secret)
    return {
        "Content-Type": "application/json",
        EVENT_HEADER: event_type,
        SIGNATURE_HEADER: f"t={t},v1={sig}",
        "User-Agent": WEBHOOK_USER_AGENT,
    }


def _now() -> datetime:
    return datetime.now(UTC)


# The events that move money, and therefore the only ones an accounting integration
# may book against. Everything else is operational. `payment.expired` is the one
# that matters: it is widely read as "sale lost" and is nothing of the sort — the
# same payment can settle afterwards, and on 2026-09-17 one did, nine minutes after
# this event fired. A consumer must branch on `financial`, not on the event name.
#
# `payment.reversed` is financial in the opposite direction: it is the negative of a
# `payment.completed`, not an operational notice.
FINANCIAL_EVENTS = frozenset({models.EVENT_COMPLETED, models.EVENT_REVERSED})


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


def build_event_payload(event_id: str, event_type: str, payment, store) -> dict:
    # Only meaningful on a settled payment, and only true when the money arrived
    # after the code had been withdrawn — which is a different operational story
    # from an ordinary sale and should not be invisible in a report.
    settled_late = bool(
        payment.paid_at
        and payment.expires_at
        and payment.paid_at > payment.expires_at
    )
    return {
        "id": event_id,
        "type": event_type,
        "created": _now().isoformat(),
        "financial": event_type in FINANCIAL_EVENTS,
        "data": {
            "payment": {
                "id": payment.public_id,
                "status": payment.status,
                "amount": f"{payment.amount_cents / 100:.2f}",
                "currency": payment.currency,
                "reference_id": payment.reference_id,
                "metadata": payment.metadata_,
                "approved_at": _iso(payment.approved_at),
                # Both ends of the money's journey. A sale is attributed to the day
                # it was made (`created_at`) and the cash to the day it arrived
                # (`paid_at`), and for a late settlement those are different days —
                # so a report needs both rather than whichever one we happened to
                # call "the" timestamp.
                "created_at": _iso(payment.created_at),
                "expires_at": _iso(payment.expires_at),
                "paid_at": _iso(payment.paid_at),
                "reversed_at": _iso(getattr(payment, "reversed_at", None)),
                "settled_late": settled_late,
            },
            "store": {
                "id": store.public_id,
                "name": store.name,
                "redirect_success_url": store.redirect_success_url,
            },
            # The caller's own merchant identifier for this store, so a POS
            # platform can route the event without keeping an id mapping table.
            # Null for stores that were not created with an external_id.
            "merchant": {"external_id": store.external_id},
        },
    }


async def _target_endpoints(
    session: AsyncSession, account_id: int
) -> list[models.WebhookEndpoint]:
    res = await session.execute(
        select(models.WebhookEndpoint).where(
            models.WebhookEndpoint.status == models.WEBHOOK_ENDPOINT_ACTIVE,
            models.WebhookEndpoint.account_id == account_id,
        )
    )
    return list(res.scalars().all())


def _endpoint_wants(endpoint: models.WebhookEndpoint, event_type: str) -> bool:
    """Whether an endpoint subscribed to this event type.

    No subscription list, or a `*` entry, means every event type.
    """
    events = endpoint.events
    if not events or "*" in events:
        return True
    return event_type in events


async def enqueue_event(
    session: AsyncSession,
    *,
    payment,
    store,
    event_type: str,
) -> models.Event:
    """Write one outbox event (payload) and schedule it for every active endpoint.

    T9 enqueue-on-write for W2: after flush, fire per-event:endpoint W2 jobs with
    dedup so retries are idempotent.
    """
    event_id = uuid.uuid4().hex
    event = models.Event(
        id=event_id,
        account_id=store.account_id,
        store_id=store.id,
        payment_id=payment.id,
        type=event_type,
        payload=build_event_payload(event_id, event_type, payment, store),
    )
    session.add(event)
    await session.flush()

    delivery_rows: list[models.EventDelivery] = []
    for endpoint in await _target_endpoints(session, store.account_id):
        if not _endpoint_wants(endpoint, event_type):
            continue
        exists = await session.execute(
            select(models.EventDelivery).where(
                models.EventDelivery.event_id == event_id,
                models.EventDelivery.endpoint_id == endpoint.id,
            )
        )
        if exists.scalar_one_or_none() is None:
            delivery = models.EventDelivery(
                event_id=event_id,
                endpoint_id=endpoint.id,
                status=models.DELIVERY_PENDING,
            )
            session.add(delivery)
            delivery_rows.append(delivery)
    if delivery_rows:
        await session.flush()
        # --- T9: W2 fanout — one job per event delivery row. Late import avoids circular import. ---
        try:
            # ruff: noqa: E402,I001 - Late import required to break circular import chain
            from .workers import Q_WEBHOOK as _Q_WEBHOOK, get_global_transport as _get_tp

            tp = _get_tp()
            if tp is not None:
                for _d in delivery_rows:
                    try:
                        await tp.enqueue(
                            _Q_WEBHOOK,
                            dedup_key=f"send:{_d.event_id}:{_d.endpoint_id}",
                            payload={
                                "delivery_id": _d.id,
                                "event_id": _d.event_id,
                                "endpoint_id": _d.endpoint_id,
                            },
                        )
                    except Exception as _w2err:  # noqa: BLE001
                        import logging as _log

                        _log.getLogger(__name__).debug(
                            "W2 enqueue skipped for delivery %s: %s", _d.id, _w2err
                        )
        except Exception as _import_err:  # noqa: BLE001 - workers package not yet ready
            import logging as _log

            _log.getLogger(__name__).debug(
                "Workers not ready during import, skipping W2 enqueue: %s", _import_err
            )
    return event


async def http_post(url: str, payload: bytes, headers: dict) -> httpx.Response:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.webhook_timeout_seconds) as client:
        return await client.post(url, content=payload, headers=headers)


async def _deliver(session: AsyncSession, delivery: models.EventDelivery) -> None:
    res = await session.execute(
        select(models.Event, models.WebhookEndpoint)
        .join(models.WebhookEndpoint, models.WebhookEndpoint.id == delivery.endpoint_id)
        .where(models.Event.id == delivery.event_id)
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
    try:
        response = await http_post(endpoint.url, payload, headers)
        delivery.last_response_status = response.status_code
        if 200 <= response.status_code < 300:
            delivery.status = models.DELIVERY_SUCCESS
            delivery.last_error = None
            return
        raise httpx.HTTPStatusError(
            f"status {response.status_code}", request=response.request, response=response
        )
    except Exception as exc:  # network + non-2xx
        settings = get_settings()
        delivery.last_error = str(exc)[:2000]
        if delivery.attempts >= settings.webhook_max_attempts:
            delivery.status = models.DELIVERY_FAILED
        else:
            delivery.status = models.DELIVERY_RETRYING
            backoff = min(2**delivery.attempts, 3600)
            delivery.next_attempt_at = _now() + timedelta(seconds=backoff)


async def process_due_deliveries(batch: int = 50) -> int:
    """Legacy helper retained so existing CLI/dev imports still resolve.

    NFR-2: The W2 WebhookSenderWorker worker class (workers/w2_webhook_sender.py)
    actually drives deliveries via the Worker.process(Job) interface inside the
    single canonical QueueTransport.run_workers dispatcher. This function is NOT
    called inside any loop anymore. It remains for tests/scripts.
    """
    now = _now()
    processed = 0
    async with session_factory() as session:
        res = await session.execute(
            select(models.EventDelivery)
            .where(
                models.EventDelivery.status.in_(
                    [models.DELIVERY_PENDING, models.DELIVERY_RETRYING]
                ),
                (models.EventDelivery.next_attempt_at.is_(None))
                | (models.EventDelivery.next_attempt_at <= now),
            )
            .order_by(models.EventDelivery.id)
            .limit(batch)
        )
        due = list(res.scalars().all())
        for delivery in due:
            await _deliver(session, delivery)
            processed += 1
        if due:
            await session.commit()
    return processed

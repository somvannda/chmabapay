"""The W2 sweep: what it must not send, and what it must leave behind.

Written after the 2026-11-04 incident, where a merchant's endpoint returned 405 for
six weeks and the three retries still queued for it kept being attempted after it was
disabled — and where every timeout was recorded as an empty string, so the delivery
log looked like no error had happened at all.
"""

from __future__ import annotations

from uuid import uuid4

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.main import app
from chmabapay.workers import w2_webhook_sender as w2

BASE_URL = "http://localhost"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def _seed_due_delivery(client, account, store, endpoint) -> models.EventDelivery:
    """One pending delivery pointing at `endpoint`, built on rows that really exist.

    The payment goes through the real route so the event's foreign keys are valid on
    Postgres too; the delivery itself is written directly because the test is about
    the scan, not about waiting out the retry budget.
    """
    raw_key, _ = await make_key(account)
    created = await client.post(
        "/api/v1/payments",
        json={
            "amount": 1.25,
            "reference_id": f"sender-{uuid4().hex[:8]}",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert created.status_code < 300, created.text
    payment_public_id = created.json()["id"]

    event_id = f"evt_sender_{uuid4().hex[:12]}"
    async with session_factory() as session:
        store_row = (
            await session.execute(
                select(models.Store).where(models.Store.public_id == store.public_id)
            )
        ).scalar_one()
        payment_row = (
            await session.execute(
                select(models.Payment).where(models.Payment.public_id == payment_public_id)
            )
        ).scalar_one()
        session.add(
            models.Event(
                id=event_id,
                account_id=account.id,
                store_id=store_row.id,
                payment_id=payment_row.id,
                type=models.EVENT_COMPLETED,
                payload={},
            )
        )
        await session.flush()
        delivery = models.EventDelivery(
            event_id=event_id,
            endpoint_id=endpoint.id,
            status=models.DELIVERY_PENDING,
        )
        session.add(delivery)
        await session.commit()
        await session.refresh(delivery)
        return delivery


async def _set_endpoint_status(endpoint, status: str) -> None:
    async with session_factory() as session:
        row = await session.get(models.WebhookEndpoint, endpoint.id)
        row.status = status
        await session.commit()


async def test_a_disabled_endpoint_stops_retries_already_queued(client, monkeypatch):
    account = await make_account()
    store = await make_store(account)
    endpoint = await make_webhook(account)
    delivery = await _seed_due_delivery(client, account, store, endpoint)
    await _set_endpoint_status(endpoint, "disabled")

    calls: list[str] = []

    async def fake_post(url, payload, headers):
        calls.append(url)
        raise AssertionError("a disabled endpoint must not be posted to")

    monkeypatch.setattr(w2, "http_post", fake_post)

    result = await w2.WebhookSenderWorker()._fanout_scan_and_send(batch_size=10)

    assert result["processed"] == 0
    assert calls == []
    async with session_factory() as session:
        row = await session.get(models.EventDelivery, delivery.id)
        assert row.status == models.DELIVERY_PENDING
        assert row.attempts == 0


async def test_a_timeout_records_its_type_rather_than_an_empty_error(client, monkeypatch):
    account = await make_account()
    store = await make_store(account)
    endpoint = await make_webhook(account)
    delivery = await _seed_due_delivery(client, account, store, endpoint)

    async def fake_post(url, payload, headers):
        raise httpx.ConnectTimeout("")

    monkeypatch.setattr(w2, "http_post", fake_post)

    result = await w2.WebhookSenderWorker()._fanout_scan_and_send(batch_size=10)

    assert result["processed"] == 1
    async with session_factory() as session:
        row = await session.get(models.EventDelivery, delivery.id)
        assert row.status == models.DELIVERY_RETRYING
        assert row.attempts == 1
        # Empty is what the incident produced, and it read as "no error happened".
        assert row.last_error == "ConnectTimeout"


async def test_a_second_scan_does_not_read_the_same_due_rows(client, monkeypatch):
    account = await make_account()
    store = await make_store(account)
    endpoint = await make_webhook(account)
    await _seed_due_delivery(client, account, store, endpoint)

    calls: list[str] = []

    async def fake_post(url, payload, headers):
        calls.append(url)
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr(w2, "http_post", fake_post)

    worker = w2.WebhookSenderWorker()
    # What a scan that outlived its one-second interval looks like while it is still
    # running: the next job must return without reading the same rows again.
    worker._scan_in_flight = True
    skipped = await worker._fanout_scan_and_send(batch_size=10)
    assert skipped["skipped"] is True
    assert calls == []

    worker._scan_in_flight = False
    sent = await worker._fanout_scan_and_send(batch_size=10)
    assert sent["processed"] == 1
    assert calls == ["https://sink.example.com/hook"]
    # The guard must clear on the way out, or one slow scan wedges the queue forever.
    assert worker._scan_in_flight is False

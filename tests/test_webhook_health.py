"""Merchant-facing webhook delivery health.

The operator console could see a failing endpoint; the merchant whose store was on the
other end of it could not. These tests pin the shape that lets the dashboard say *how
many* deliveries failed and *which store's* events were in them, scoped to the account
that owns the endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook, session_client
from sqlalchemy import select

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.main import app

BASE_URL = "http://localhost"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


async def _make_delivery(
    client: httpx.AsyncClient,
    account,
    store,
    endpoint,
    *,
    status: str = models.DELIVERY_FAILED,
    hours_ago: int = 0,
    raw_key: str | None = None,
) -> models.EventDelivery:
    """One delivery for one event of `store`, with a controllable age.

    Built through the real payment route so the event's foreign keys point at rows that
    exist on Postgres too; the delivery state itself is set directly because the suite
    must not wait out the retry budget.
    """
    if raw_key is None:
        raw_key, _ = await make_key(account)
    created = await client.post(
        "/api/v1/payments",
        json={
            "amount": 1.25,
            "reference_id": f"health-{uuid4().hex[:8]}",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert created.status_code < 300, created.text
    payment_public_id = created.json()["id"]

    event_id = f"evt_health_{uuid4().hex[:12]}"
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
            status=status,
            attempts=6,
            last_error="connection refused",
            last_response_status=502,
        )
        session.add(delivery)
        await session.commit()
        await session.refresh(delivery)
        if hours_ago:
            delivery.updated_at = datetime.now(UTC) - timedelta(hours=hours_ago)
            await session.commit()
        return delivery


async def test_health_reports_failures_by_endpoint_and_store(client):
    account = await make_account()
    store = await make_store(account, name="Sokha Cafe")
    endpoint = await make_webhook(account, url="https://sokha.example.com/hook")
    raw_key, _ = await make_key(account)

    await _make_delivery(client, account, store, endpoint, raw_key=raw_key)
    await _make_delivery(client, account, store, endpoint, raw_key=raw_key)
    await _make_delivery(
        client,
        account,
        store,
        endpoint,
        status=models.DELIVERY_RETRYING,
        raw_key=raw_key,
    )

    async with session_client(account) as portal:
        res = await portal.get("/api/v1/webhooks/health")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["window_hours"] == 24
    assert body["failed_count"] == 2
    assert body["retrying_count"] == 1
    assert body["affected_endpoint_count"] == 1
    assert body["affected_store_count"] == 1

    endpoint_row = body["endpoints"][0]
    assert endpoint_row["endpoint_id"] == endpoint.id
    assert endpoint_row["url"] == "https://sokha.example.com/hook"
    assert endpoint_row["failed_count"] == 2
    assert endpoint_row["retrying_count"] == 1
    assert endpoint_row["affected_store_count"] == 1
    # The cause the merchant can act on, not just the count.
    assert endpoint_row["last_error"] == "connection refused"
    assert endpoint_row["last_response_status"] == 502

    store_row = endpoint_row["stores"][0]
    assert store_row["store_name"] == "Sokha Cafe"
    assert store_row["failed_count"] == 2
    assert store_row["retrying_count"] == 1


async def test_health_is_scoped_to_the_account_and_its_window(client):
    account = await make_account()
    other = await make_account(email="other@chmaba.test", name="Other")
    store = await make_store(account, name="Sokha Cafe")
    endpoint = await make_webhook(account)
    other_store = await make_store(other, name="Other Store")
    other_endpoint = await make_webhook(other, url="https://other.example.com/hook")

    # This account's failure is old enough to fall out of the default 24h window.
    await _make_delivery(client, account, store, endpoint, hours_ago=48)
    # Another account's recent failure must never appear in this account's health.
    await _make_delivery(client, other, other_store, other_endpoint)

    async with session_client(account) as portal:
        default = await portal.get("/api/v1/webhooks/health")
        wider = await portal.get("/api/v1/webhooks/health?window_hours=72")

    assert default.status_code == 200, default.text
    assert default.json()["failed_count"] == 0
    assert default.json()["endpoints"] == []

    assert wider.status_code == 200, wider.text
    wider_body = wider.json()
    assert wider_body["failed_count"] == 1
    assert wider_body["endpoints"][0]["url"] == endpoint.url
    assert wider_body["endpoints"][0]["stores"][0]["store_name"] == "Sokha Cafe"


async def test_delivery_log_names_the_store_the_event_came_from(client):
    account = await make_account()
    store = await make_store(account, name="Sokha Cafe")
    endpoint = await make_webhook(account)
    delivery = await _make_delivery(client, account, store, endpoint)

    async with session_client(account) as portal:
        res = await portal.get(f"/api/v1/webhooks/{endpoint.id}/deliveries")

    assert res.status_code == 200, res.text
    rows = res.json()
    assert len(rows) == 1
    assert rows[0]["delivery_id"] == delivery.id
    assert rows[0]["store_name"] == "Sokha Cafe"
    assert rows[0]["store_id"] == store.id

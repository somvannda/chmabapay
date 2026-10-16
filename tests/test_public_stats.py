"""The public traction endpoint behind the homepage metrics row.

Two things are being protected here: that an unauthenticated caller can read the
aggregates at all (that is the whole point — the marketing page is anonymous),
and that the platform's own internal stores stay out of them. Summing plan fees
collected into "ChmabaPay HQ" into merchant volume would overstate the one number
customers are meant to trust.
"""

from __future__ import annotations

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store
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


async def _pay(client: httpx.AsyncClient, merchant, store, *, amount: float) -> dict:
    raw_key, _key = await make_key(merchant)
    res = await client.post(
        "/api/v1/payments",
        json={
            "amount": amount,
            "reference_id": "order_1",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code < 300, res.text
    payment = res.json()
    settle = await client.post(f"/_dev/payments/{payment['id']}/pay")
    assert settle.status_code == 200, settle.text
    return payment


async def _make_internal_store(account, name: str = "ChmabaPay HQ") -> models.Store:
    """A store that belongs to the platform itself, not a tenant."""
    store = await make_store(account, name=name, owner=name.replace(" ", ""))
    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        assert row is not None
        row.is_internal = True
        await session.commit()
    return store


async def test_stats_are_readable_without_auth_and_count_merchant_activity(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")
    await _pay(client, merchant, store, amount=12.5)

    # No Authorization header, no cookie: this is the anonymous homepage caller.
    res = await client.get("/api/v1/public/stats")
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["active_customers"] == 1
    assert body["active_stores"] == 1
    assert body["transactions_processed"] == 1
    assert body["volumes"]["USD"] == 1250
    assert body["volumes"]["KHR"] == 0
    # The response is cacheable — it is four aggregate queries per hit otherwise.
    assert "max-age" in res.headers.get("cache-control", "")


async def test_internal_stores_are_excluded_from_every_figure(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")
    await _pay(client, merchant, store, amount=10.0)

    # The platform's own store, plus a paid plan fee flowing through it.
    hq = await _make_internal_store(merchant)
    await _pay(client, merchant, hq, amount=99.0)

    body = (await client.get("/api/v1/public/stats")).json()

    # One merchant store, not two; one merchant payment, not two; volume is the
    # merchant's $10, never the $99 plan fee.
    assert body["active_stores"] == 1
    assert body["transactions_processed"] == 1
    assert body["volumes"]["USD"] == 1000


async def test_unpaid_payments_do_not_count(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")

    raw_key, _key = await make_key(merchant)
    res = await client.post(
        "/api/v1/payments",
        json={"amount": 5.0, "reference_id": "order_2", "store": store.public_id, "hosted_qr": False},
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code < 300, res.text

    body = (await client.get("/api/v1/public/stats")).json()
    # Created but never settled: not a transaction "processed".
    assert body["transactions_processed"] == 0
    assert body["volumes"]["USD"] == 0


async def test_disabled_stores_are_not_counted_as_active(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")
    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        row.status = models.STORE_DISABLED
        await session.commit()

    body = (await client.get("/api/v1/public/stats")).json()
    assert body["active_stores"] == 0

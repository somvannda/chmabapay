"""The overview's "needs attention" block, and the filters behind its counts.

A counter an operator cannot reach the rows from is a number, not a signal — so each
test here checks the count *and* the filtered list that count links to.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest_asyncio
from conftest import make_account, make_key, make_store, make_webhook
from sqlalchemy import select

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import session_factory
from chmabapay.main import app
from chmabapay.security import hash_password

BASE_URL = "http://localhost"
PASSWORD = "correct horse battery"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


@pytest_asyncio.fixture(autouse=True)
async def unconfigured_hq_store(monkeypatch):
    """Start with no plan-fee collection configured, whatever the environment says.

    A deployment that has set `CHMABAPAY_HQ_PAYWAY_LINK` seeds an HQ store the first
    time a platform admin signs in, and this file counts stores.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "chmabapay_hq_store_id", None, raising=False)
    monkeypatch.setattr(settings, "chmabapay_hq_payway_link", None, raising=False)
    return settings


async def make_admin() -> models.Account:
    account = await make_account(email="duke@chmaba.test", name="Duke")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        assert row is not None
        row.is_platform_admin = True
        row.password_hash = hash_password(PASSWORD)
        await session.commit()
    return account


async def sign_in(client: httpx.AsyncClient, email: str) -> None:
    res = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200, res.text


async def make_payment(
    client: httpx.AsyncClient, merchant, store, *, amount: float
) -> dict:
    raw_key, _key = await make_key(merchant)
    res = await client.post(
        "/v1/payments",
        json={
            "amount": amount,
            "reference_id": "order_99",
            "store": store.public_id,
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )
    assert res.status_code < 300, res.text
    return res.json()


async def _payment_pk(public_id: str) -> int:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.Payment.id).where(models.Payment.public_id == public_id)
            )
        ).scalar_one()


async def _store_pk(public_id: str) -> int:
    async with session_factory() as session:
        return (
            await session.execute(
                select(models.Store.id).where(models.Store.public_id == public_id)
            )
        ).scalar_one()


def _counts(overview: dict) -> dict[str, int]:
    return {item["key"]: item["count"] for item in overview["needs_attention"]}


async def _age_one_row(client, merchant, store, *, amount: float) -> dict:
    """A payment whose window closed while it was still pending."""
    created = await make_payment(client, merchant, store, amount=amount)
    async with session_factory() as session:
        row = await session.get(models.Payment, await _payment_pk(created["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(minutes=30)
        await session.commit()
    return created


async def _abandon_one_row(client, merchant, store, *, amount: float) -> dict:
    """A payment we stopped watching that was never paid."""
    created = await make_payment(client, merchant, store, amount=amount)
    async with session_factory() as session:
        row = await session.get(models.Payment, await _payment_pk(created["id"]))
        row.detection_closed_at = datetime.now(UTC)
        await session.commit()
    return created


async def _seed_stuck_rows(client, merchant, store) -> tuple[dict, dict]:
    """One row behind each of the two payment counters."""
    return (
        await _age_one_row(client, merchant, store, amount=5.0),
        await _abandon_one_row(client, merchant, store, amount=7.0),
    )


async def test_every_counter_matches_a_seeded_row(client):
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")

    stale, unwatched = await _seed_stuck_rows(client, merchant, store)

    # Settled today, which is the volume number.
    settled = await make_payment(client, merchant, store, amount=12.5)
    assert (await client.post(f"/_dev/payments/{settled['id']}/pay")).status_code == 200

    # One webhook endpoint that gave up in the last day.
    endpoint = await make_webhook(merchant)
    store_pk = await _store_pk(store.public_id)
    payment_pk = await _payment_pk(settled["id"])
    async with session_factory() as session:
        session.add(
            models.Event(
                id="evt_attention_test",
                account_id=merchant.id,
                store_id=store_pk,
                payment_id=payment_pk,
                type=models.EVENT_COMPLETED,
                payload={},
            )
        )
        await session.flush()
        session.add(
            models.EventDelivery(
                event_id="evt_attention_test",
                endpoint_id=endpoint.id,
                status=models.DELIVERY_FAILED,
                attempts=6,
                last_error="connection refused",
            )
        )
        await session.commit()

    await sign_in(client, admin.email)
    overview = (await client.get("/v1/admin/overview")).json()

    counts = _counts(overview)
    assert counts["pending_past_expiry"] == 1
    assert counts["detection_closed_unpaid"] == 1
    assert counts["deliveries_failed_24h"] == 1

    # The two stuck payments are the ones counted, not something incidental.
    listing = (await client.get("/v1/admin/payments?per_page=50")).json()
    by_id = {row["id"]: row for row in listing["data"]}
    assert by_id[stale["id"]]["detection_closed_at"] is None
    assert by_id[unwatched["id"]]["detection_closed_at"] is not None

    assert overview["paid_today_count"] == 1
    assert overview["paid_today_cents"] == 1250
    assert overview["stores_total"] == 1
    assert overview["stores_active"] == 1
    # The admin account is counted too, and the console is the thing reading it.
    assert overview["accounts_total"] == 2

    # Every item that is not zero carries the filter that shows its rows.
    for item in overview["needs_attention"]:
        if item["count"]:
            assert item["href"], item["key"]


async def test_the_counts_lead_to_the_rows_they_counted(client):
    admin = await make_admin()
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    store = await make_store(merchant, owner="Sokha")

    stale, unwatched = await _seed_stuck_rows(client, merchant, store)
    healthy = await make_payment(client, merchant, store, amount=9.0)

    await sign_in(client, admin.email)

    expired_rows = (
        await client.get("/v1/admin/payments?attention=pending_past_expiry")
    ).json()
    assert [row["id"] for row in expired_rows["data"]] == [stale["id"]]

    closed_rows = (
        await client.get("/v1/admin/payments?attention=detection_closed_unpaid")
    ).json()
    assert [row["id"] for row in closed_rows["data"]] == [unwatched["id"]]

    # The live code is in neither list: `status=pending` on its own would have included
    # it, which is the whole reason these two filters exist.
    for payload in (expired_rows, closed_rows):
        assert healthy["id"] not in [row["id"] for row in payload["data"]]


async def test_the_worker_signals_are_absent_rather_than_zero_in_process(client):
    """With the in-process transport there is no cross-process stamp to read.

    Reporting a zero there would turn "not measurable" into "all clear" — the console
    says it cannot see instead, and the stale-queue card is left out rather than
    claiming zero wedged workers.
    """
    admin = await make_admin()
    await sign_in(client, admin.email)

    overview = (await client.get("/v1/admin/overview")).json()
    ops = overview["ops"]

    assert ops["watched"] is False
    assert ops["stale_queues"] is None
    assert ops["queue_depth"] is None
    assert ops["error"] is None
    assert "stale_worker_queues" not in _counts(overview)


async def test_the_overview_and_its_filters_are_admin_only(client):
    merchant = await make_account(email="sokha@chmaba.test", name="Sokha Cafe")
    await make_store(merchant, owner="Sokha")

    assert (await client.get("/v1/admin/overview")).status_code == 401
    assert (
        await client.get("/v1/admin/payments?attention=pending_past_expiry")
    ).status_code == 401

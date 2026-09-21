"""Tests that reach the real ABA PayWay rail.

Excluded from the default run (`addopts = -m "not live"`) and therefore from CI.
Run them deliberately:

    CHMABAPAY_LIVE_PAYWAY_LINK=https://link.payway.com.kh/<slug> pytest -m live

Every payment created here spends a real ABA checkout session and mints a QR a
human could actually pay, which is why they are opt-in.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from conftest import make_account, make_key, make_store
from sqlalchemy import select, update

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.schemas import LinkIn
from chmabapay.services import stores as store_svc
from chmabapay.services.payments import expire_due_payments

pytestmark = pytest.mark.live

LIVE_LINK = os.getenv("CHMABAPAY_LIVE_PAYWAY_LINK", "").strip()

requires_live_link = pytest.mark.skipif(
    not LIVE_LINK,
    reason="set CHMABAPAY_LIVE_PAYWAY_LINK to a real ABA PayWay link",
)


@pytest_asyncio.fixture
async def live_setup():
    """A store on a real PayWay link, plus a live-mode key that owns it.

    The link is written directly rather than derived through `make_store`, which
    builds it from the owner name and *lowercases* it — and a PayWay slug is
    case-sensitive, so `ABAPAYpe518710Y` became `abapaype518710y`. That page has
    no `aba_data`, so every mint failed with `link_page_missing_aba_data`, a
    message that reads exactly like ABA changing its markup and is really a
    broken fixture. These two tests had therefore never once passed against a
    live link; found on 2026-09-17 while settling a real payment, and re-run
    against the real slug afterwards.
    """
    slug = LIVE_LINK.rstrip("/").rsplit("/", 1)[-1]
    account = await make_account(email="live-aba@chmaba.test", name="Live ABA")
    store = await make_store(account, name="Live Store")

    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        assert row is not None
        await store_svc.attach_link_to_store(
            session,
            row,
            LinkIn(
                raw_link=LIVE_LINK,
                merchant_account_id=slug,
                merchant_name="Live Store",
            ),
            # This is the one place a link genuinely is verified: the test only runs
            # when a real slug is configured and it talks to ABA immediately after.
            verification=models.LINK_VERIFIED,
        )
        await session.commit()

    raw_key, _ = await make_key(account)
    return store, {"Authorization": f"Bearer {raw_key}"}


@requires_live_link
async def test_hosted_checkout_mints_a_real_aba_session(client, live_setup):
    """ABA issues the QR and hands back a session we can poll.

    This is the mint half of the money path, and the part a machine can prove:
    ABA answers with a QR payload plus a client_id/token, and its status endpoint
    recognises that session. Whether a payment *settles* still needs a human with
    a wallet — see docs/production-readiness.md P0-2.
    """
    store, headers = live_setup

    created = await client.post(
        "/v1/payments",
        json={"amount": 0.01, "store": store.public_id},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    payment = created.json()
    assert payment["status"] == "pending"
    # ABA's own payload, not one we built offline.
    assert payment["qr_string"], "ABA returned no QR payload"
    assert payment["expires_at"], "ABA returned no expiry"

    # The session is read from the row, not from the response body. `PaymentOut`
    # declares `gateway_raw` (aliased to `gateway_status_raw`) but `payment_out`
    # never populates it, so that key is always null on the API response — and the
    # session this test must poll only ever existed on the row. Asserting on the
    # response made the test impossible to pass, which went unnoticed because
    # `live` tests are excluded from the default run and no live link was ever
    # configured. Found while settling a real payment; see P0-2.
    async with session_factory() as lookup:
        hosted = (
            await lookup.execute(
                select(models.Payment.gateway_status_raw).where(
                    models.Payment.public_id == payment["id"]
                )
            )
        ).scalar_one()

    session = (hosted or {}).get("payway_hosted") or {}
    assert session.get("client_id"), "no ABA session to poll for this payment"
    assert session.get("token"), "no ABA session token"

    # ABA must be able to answer for the session it just handed us; without that
    # the payment could never be confirmed.
    status = await client.post(
        "/v1/khqr/payway/status",
        json={
            "client_id": session["client_id"],
            "request_time": session["request_time"],
            "token": session["token"],
        },
        headers=headers,
    )
    assert status.status_code == 200, status.text
    body = status.json()
    assert body["action"] in {
        "request_qr",
        "scanned",
        "rqpay",
        "processing-payment",
        "approved",
    }
    assert body["paid"] is False, "a QR nobody has scanned cannot be paid yet"


@requires_live_link
async def test_a_real_aba_code_stops_being_served_once_it_expires(client, live_setup):
    """The 410 rule holds against a real ABA-issued code, not only our own.

    ABA's window is short, so this expires the row directly rather than waiting
    three minutes for the sweeper.
    """
    store, headers = live_setup

    created = await client.post(
        "/v1/payments",
        json={"amount": 0.01, "store": store.public_id},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    public_id = created.json()["id"]

    live_svg = await client.get(f"/pay/{public_id}/qr.svg")
    assert live_svg.status_code == 200
    assert live_svg.headers["content-type"].startswith("image/svg+xml")

    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == public_id)
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        await expire_due_payments(session)

    dead = await client.get(f"/pay/{public_id}/qr.svg")
    assert dead.status_code == 410
    assert dead.json()["detail"] == "payment_expired"

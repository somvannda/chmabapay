"""The hourly synthetic check must never write into a merchant's store."""

from __future__ import annotations

from conftest import make_account, make_key, make_store
from sqlalchemy import func, select, update

from chmabapay import models
from chmabapay.config import get_settings
from chmabapay.db import session_factory


async def _make_platform_admin(*, email: str = "ops@chmaba.test"):
    account = await make_account(email=email, name="ChmabaPay Ops")
    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.is_platform_admin = True
        await session.commit()
    return account


async def _make_internal_store(account, *, name: str = "ChmabaPay HQ"):
    store = await make_store(account, name=name, owner="hq")
    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        row.is_internal = True
        await session.commit()
    return store


async def _payment_count() -> int:
    async with session_factory() as session:
        return (
            await session.execute(select(func.count(models.Payment.id)))
        ).scalar_one()


async def test_probe_refuses_a_merchant_key(client):
    account = await make_account()
    await make_store(account)
    raw_key, _ = await make_key(account)

    response = await client.post(
        "/api/v1/internal/payment-probe",
        json={"amount": 0.10},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "platform_admin_required"
    assert await _payment_count() == 0


async def test_probe_uses_the_internal_store_and_writes_no_payment(client):
    merchant = await make_account(email="merchant@chmaba.test", name="Merchant")
    customer_store = await make_store(merchant, name="Customer Store")

    ops = await _make_platform_admin()
    hq_store = await _make_internal_store(ops)
    raw_key, _ = await make_key(ops)

    response = await client.post(
        "/api/v1/internal/payment-probe",
        json={
            "amount": 0.10,
            # The old monitor's body may still name a customer store. It must be
            # ignored, not honoured.
            "store": customer_store.public_id,
            "reference_id": "health-smoke:old-monitor-body",
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["probe"] == "internal"
    assert body["store"] == hq_store.public_id
    assert body["currency"] == "USD"
    assert body["amount"] == "0.10"
    assert body["amount_cents"] == 10
    assert body["hosted"] is False
    assert body["qr_md5"]
    assert "qr_string" not in body

    # The whole point: no Payment row was written for either store.
    assert await _payment_count() == 0


async def test_probe_can_exercise_the_hosted_mint_without_writing_a_payment(client):
    """`hosted_qr=true` spends an ABA session, so it must still write no row.

    The suite replaces the one network function, so this asserts our own branch
    (a hosted session was requested and returned) rather than calling ABA.
    """
    ops = await _make_platform_admin()
    await _make_internal_store(ops)
    raw_key, _ = await make_key(ops)

    response = await client.post(
        "/api/v1/internal/payment-probe",
        json={"amount": 0.10, "hosted_qr": True},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hosted"] is True
    assert body["qr_md5"]
    assert await _payment_count() == 0


async def test_probe_accepts_an_empty_body(client):
    ops = await _make_platform_admin()
    await _make_internal_store(ops)
    raw_key, _ = await make_key(ops)

    response = await client.post(
        "/api/v1/internal/payment-probe",
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 200
    assert response.json()["amount"] == "0.10"
    assert response.json()["hosted"] is False
    assert await _payment_count() == 0


async def test_probe_accepts_the_scoped_token_without_an_admin_key(client, monkeypatch):
    """The token is scoped to this endpoint; it carries no account context.

    This is the path an external monitor should use: it cannot create stores, read
    accounts or call the console the way a platform-admin API key can.
    """
    ops = await _make_platform_admin()
    await _make_internal_store(ops)
    monkeypatch.setattr(
        get_settings(), "internal_probe_token", "probe-secret", raising=False
    )

    response = await client.post(
        "/api/v1/internal/payment-probe",
        json={"amount": 0.10},
        headers={"Authorization": "Bearer probe-secret"},
    )

    assert response.status_code == 200
    assert response.json()["probe"] == "internal"
    assert await _payment_count() == 0


async def test_probe_rejects_a_wrong_scoped_token(client, monkeypatch):
    monkeypatch.setattr(
        get_settings(), "internal_probe_token", "probe-secret", raising=False
    )

    response = await client.post(
        "/api/v1/internal/payment-probe",
        headers={"Authorization": "Bearer not-the-token"},
    )

    assert response.status_code == 401


async def test_probe_refuses_when_the_hq_store_is_not_internal(client):
    ops = await _make_platform_admin()
    await make_store(ops, name="Ops Store")
    raw_key, _ = await make_key(ops)

    response = await client.post(
        "/api/v1/internal/payment-probe",
        json={"amount": 0.10},
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "internal_probe_store_not_internal"


async def test_synthetic_reference_is_refused_on_a_customer_store(client):
    account = await make_account()
    await make_store(account)
    raw_key, _ = await make_key(account)

    response = await client.post(
        "/api/v1/payments",
        json={
            "amount": 0.10,
            "reference_id": "health-smoke:should-not-land-here",
            "hosted_qr": False,
        },
        headers={"Authorization": f"Bearer {raw_key}"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "synthetic_payment_requires_internal_store"
    assert await _payment_count() == 0


async def test_synthetic_rows_are_hidden_from_the_merchant_list(client):
    """Legacy probe rows already in a merchant store stop being shown.

    The guard stops new ones; this keeps the historical noise out of the portal
    and the reports without deleting an audit row.
    """
    account = await make_account()
    await make_store(account)
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/api/v1/payments",
            json={"amount": 1.0, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == created["id"])
            .values(reference_id="health-smoke:legacy-row")
        )
        await session.commit()

    listed = await client.get("/api/v1/payments", headers=headers)
    assert listed.status_code == 200
    assert created["id"] not in [row["id"] for row in listed.json()["data"]]

    report = await client.get("/api/v1/reports/payments.json", headers=headers)
    assert report.status_code == 200
    assert created["id"] not in [row["public_id"] for row in report.json()["data"]]

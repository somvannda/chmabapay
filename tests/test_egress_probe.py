"""The egress probe is the check that would have caught the 2026-11-04 outage.

`/health` answered 200 throughout it; the internal payment probe never leaves the
box. This one proves the application can still resolve a name and open an outbound
connection, and answers 503 when it cannot, so a monitor needs only the status code.
"""

from __future__ import annotations

from conftest import make_account, make_key
from sqlalchemy import update

from chmabapay import models
from chmabapay.db import session_factory
from chmabapay.routers import probe


async def _make_platform_admin(*, email: str = "ops-egress@chmaba.test"):
    account = await make_account(email=email, name="ChmabaPay Ops")
    async with session_factory() as session:
        await session.execute(
            update(models.Account)
            .where(models.Account.id == account.id)
            .values(is_platform_admin=True)
        )
        await session.commit()
    return account


async def test_egress_refuses_an_anonymous_caller(client):
    response = await client.get("/api/v1/internal/egress")

    assert response.status_code == 401


async def test_egress_refuses_a_merchant_key(client):
    account = await make_account()
    raw_key, _ = await make_key(account)

    response = await client.get(
        "/api/v1/internal/egress", headers={"Authorization": f"Bearer {raw_key}"}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "platform_admin_required"


async def test_egress_reports_ok_when_every_target_answers(client, monkeypatch):
    admin = await _make_platform_admin()
    raw_key, _ = await make_key(admin)

    async def fake_check(name, host, port, timeout):
        return probe.EgressCheckOut(
            name=name, host=host, ok=True, detail="DNS 3ms, connect 5ms", ms=8
        )

    monkeypatch.setattr(probe, "_egress_check", fake_check)

    response = await client.get(
        "/api/v1/internal/egress", headers={"Authorization": f"Bearer {raw_key}"}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "ok"
    assert {check["name"] for check in body["checks"]} == {"telegram", "aba"}


async def test_egress_answers_503_when_a_target_cannot_be_reached(client, monkeypatch):
    admin = await _make_platform_admin()
    raw_key, _ = await make_key(admin)

    async def fake_check(name, host, port, timeout):
        ok = name == "telegram"
        return probe.EgressCheckOut(
            name=name,
            host=host,
            ok=ok,
            detail="DNS 3ms, connect 4ms" if ok else "DNS timed out after 5.0s",
            ms=8 if ok else 5000,
        )

    monkeypatch.setattr(probe, "_egress_check", fake_check)

    response = await client.get(
        "/api/v1/internal/egress", headers={"Authorization": f"Bearer {raw_key}"}
    )

    # 503 is the whole contract for a monitor: no JSON parsing required.
    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "degraded"
    failed = [check for check in body["checks"] if not check["ok"]]
    assert len(failed) == 1
    assert "DNS" in failed[0]["detail"]

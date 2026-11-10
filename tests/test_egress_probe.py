"""The egress probe is the check that would have caught the 2026-11-04 outage.

`/health` answered 200 throughout it; the internal payment probe never leaves the
box. This one proves the application can still resolve a name and open an outbound
connection, and answers 503 when it cannot, so a monitor needs only the status code.
"""

from __future__ import annotations

import asyncio
import socket

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
    assert {check["name"] for check in body["checks"]} == {
        "telegram",
        "aba",
        "cloudflare",
    }


async def test_egress_stays_ok_when_one_target_fails(client, monkeypatch):
    """One flaky third party is not a broken host (the 2026-11-08 false page).

    Telegram alone failed to connect while ABA answered in 2ms, and `all(...)`
    turned that into "the host cannot reach the internet".
    """
    admin = await _make_platform_admin(email="ops-quorum@chmaba.test")
    raw_key, _ = await make_key(admin)

    async def fake_check(name, host, port, timeout):
        ok = name != "telegram"
        return probe.EgressCheckOut(
            name=name,
            host=host,
            ok=ok,
            detail="DNS 3ms, connect 4ms (IPv4)" if ok else "DNS 3ms, connect failed",
            ms=8 if ok else 5000,
        )

    monkeypatch.setattr(probe, "_egress_check", fake_check)

    response = await client.get(
        "/api/v1/internal/egress", headers={"Authorization": f"Bearer {raw_key}"}
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ok"


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
    assert len(failed) == 2
    assert all("DNS" in check["detail"] for check in failed)


def test_connect_attempts_tries_ipv4_before_ipv6():
    """The 2026-11-08 alert was a blackholed IPv6 route, not a dead host.

    `getaddrinfo` returns AAAA first; the probe must not let that address spend
    the whole budget when an IPv4 address would answer in milliseconds.
    """
    infos = [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2001:db8::1", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.10", 443)),
    ]

    attempts = probe._connect_attempts(infos)

    assert [family for family, _, _ in attempts] == [socket.AF_INET, socket.AF_INET6]
    # Duplicate addresses are collapsed: one connect attempt each.
    assert len(attempts) == 2


async def test_connect_first_falls_back_past_a_dead_address():
    async def accept(reader, writer):
        writer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    attempts = [
        (socket.AF_INET6, socket.IPPROTO_TCP, ("2001:db8::1", port)),
        (socket.AF_INET, socket.IPPROTO_TCP, ("127.0.0.1", port)),
    ]
    try:
        family, elapsed, failures = await probe._connect_first(
            attempts, port, timeout=0.4
        )
    finally:
        server.close()
        await server.wait_closed()

    assert family == socket.AF_INET
    assert failures, "the unreachable IPv6 address should have been recorded"
    assert elapsed < 0.4

"""The merchant alert channel must not report a send it never made.

`POST /api/v1/stores/{id}/telegram/test` used to log "Would send Telegram test msg"
and answer `ok: true` regardless, so a merchant could be told their alerts worked
while nothing ever left the building. These tests pin the honest contract: a
success answer is reachable only through a send Telegram actually accepted.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import make_account, make_key, make_store

from chmabapay.config import Settings
from chmabapay.services import telegram

BOT_TOKEN = "8123456789:AAF-test-bot-token"
CHAT_ID = "-1002233445566"


@pytest.fixture
def bot_token(monkeypatch):
    """A deployment that holds the token alerting needs."""
    monkeypatch.setattr(
        telegram, "get_settings", lambda: Settings(telegram_bot_token=BOT_TOKEN)
    )
    return BOT_TOKEN


@pytest.fixture
def outbound(monkeypatch):
    """Capture outbound calls instead of making them. Answers as Telegram does."""
    sent: list[dict] = []

    async def fake_post(url: str, payload: dict) -> httpx.Response:
        sent.append({"url": url, "payload": payload})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 7}})

    monkeypatch.setattr(telegram, "http_post", fake_post)
    return sent


async def _provision(client, *, chat_id: str | None = CHAT_ID):
    """A store on a fresh account, with its alert chat configured via the API."""
    account = await make_account()
    store = await make_store(account, name="Sokha Cafe")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    if chat_id is not None:
        saved = await client.patch(
            f"/api/v1/stores/{store.public_id}",
            json={"telegram_chat_id": chat_id},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text

    return store, headers


async def test_the_test_alert_really_sends(client, bot_token, outbound):
    """A success answer must mean a message reached the wire."""
    store, headers = await _provision(client)

    response = await client.post(
        f"/api/v1/stores/{store.public_id}/telegram/test", headers=headers
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "chat_id": CHAT_ID}
    # The assertion that fails if the handler completes without attempting a send.
    assert len(outbound) == 1
    assert outbound[0]["url"] == f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    assert outbound[0]["payload"]["chat_id"] == CHAT_ID
    assert store.name in outbound[0]["payload"]["text"]


@pytest.mark.parametrize(
    ("refusal", "detail"),
    [
        # Telegram reports a bad chat id as HTTP 200 carrying ok:false, so the
        # status code alone is not the verdict.
        (
            httpx.Response(200, json={"ok": False, "description": "chat not found"}),
            "telegram_send_failed: chat not found",
        ),
        (
            httpx.Response(401, json={"ok": False, "description": "Unauthorized"}),
            "telegram_send_failed: Unauthorized",
        ),
    ],
)
async def test_a_refusal_from_telegram_is_not_a_success(
    client, bot_token, outbound, monkeypatch, refusal, detail
):
    store, headers = await _provision(client)

    async def refuse(url: str, payload: dict) -> httpx.Response:
        outbound.append({"url": url, "payload": payload})
        return refusal

    monkeypatch.setattr(telegram, "http_post", refuse)

    response = await client.post(
        f"/api/v1/stores/{store.public_id}/telegram/test", headers=headers
    )

    assert response.status_code == 502
    assert response.json()["detail"] == detail


async def test_an_unreachable_telegram_is_a_gateway_error(
    client, bot_token, outbound, monkeypatch
):
    store, headers = await _provision(client)

    async def unreachable(url: str, payload: dict) -> httpx.Response:
        outbound.append({"url": url, "payload": payload})
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(telegram, "http_post", unreachable)

    response = await client.post(
        f"/api/v1/stores/{store.public_id}/telegram/test", headers=headers
    )

    assert response.status_code == 502
    assert "could not reach Telegram" in response.json()["detail"]


async def test_a_store_without_a_chat_id_is_refused_and_nothing_is_sent(
    client, bot_token, outbound
):
    store, headers = await _provision(client, chat_id=None)

    response = await client.post(
        f"/api/v1/stores/{store.public_id}/telegram/test", headers=headers
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "telegram_chat_id_not_set"
    assert outbound == []


async def test_a_deployment_without_a_bot_token_refuses_rather_than_lying(
    client, outbound, monkeypatch
):
    """No token means no alert can be sent, so saying `ok` would be a lie."""
    monkeypatch.setattr(
        telegram, "get_settings", lambda: Settings(telegram_bot_token=None)
    )
    store, headers = await _provision(client)

    response = await client.post(
        f"/api/v1/stores/{store.public_id}/telegram/test", headers=headers
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "telegram_not_configured"
    assert outbound == []

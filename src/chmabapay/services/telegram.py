"""Telegram Bot API client — the merchant alert channel.

A store carries a `telegram_chat_id`; the deployment holds a bot token. Both are
required, and every way of falling short is reported rather than swallowed.
Previously `POST /api/v1/stores/{id}/telegram/test` logged "Would send Telegram test
msg" and returned `ok: true` without sending anything, which told a merchant their
alerts worked when nothing had ever left the building.
"""

from __future__ import annotations

import httpx

from ..config import get_settings

API_BASE = "https://api.telegram.org"


class TelegramError(RuntimeError):
    """Telegram did not accept the message. The message explains why."""


def is_configured() -> bool:
    """Whether this deployment holds the bot token alerting needs."""
    return bool(get_settings().telegram_bot_token)


async def http_post(url: str, payload: dict) -> httpx.Response:
    """The single outbound call, kept at module level so tests can capture it."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.telegram_timeout_seconds) as client:
        return await client.post(url, json=payload)


async def send_message(chat_id: str, text: str) -> None:
    """Deliver one message to a chat. Raises `TelegramError` if it was not sent."""
    token = get_settings().telegram_bot_token
    if not token:
        raise TelegramError("telegram_bot_token is not configured")

    try:
        response = await http_post(
            f"{API_BASE}/bot{token}/sendMessage",
            {"chat_id": chat_id, "text": text},
        )
    except httpx.HTTPError as exc:
        raise TelegramError(f"could not reach Telegram: {exc}") from exc

    # A bad chat id comes back as HTTP 200 carrying {"ok": false, "description":
    # ...}, so the status code alone is not the verdict.
    if response.status_code != 200:
        raise TelegramError(_description(response))
    body = response.json()
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram refused the message")


def _description(response: httpx.Response) -> str:
    try:
        detail = response.json().get("description")
    except ValueError:
        detail = None
    return detail or f"HTTP {response.status_code}"


async def get_updates() -> list[dict]:
    """Every update the bot has not consumed yet, for discovering a chat id.

    Telegram reports a group only once the bot is a *member* of it and something has
    been said there: a bot cannot be added by invite link, cannot open a conversation,
    and there is no API that turns ``t.me/+…`` into a numeric id. This is how an
    operator turns "I invited the bot" into the id a deployment needs.

    Raises `TelegramError` rather than returning a partial answer. A bot with a webhook
    set is refused here (Telegram answers 409, because the two delivery modes are
    exclusive) and the operator needs to hear that, not see an empty list.
    """
    token = get_settings().telegram_bot_token
    if not token:
        raise TelegramError("telegram_bot_token is not configured — set TELEGRAM_BOT_TOKEN")

    try:
        async with httpx.AsyncClient(
            timeout=get_settings().telegram_timeout_seconds
        ) as client:
            response = await client.get(f"{API_BASE}/bot{token}/getUpdates")
    except httpx.HTTPError as exc:
        raise TelegramError(f"could not reach Telegram: {exc}") from exc

    if response.status_code != 200:
        raise TelegramError(_description(response))
    body = response.json()
    if not body.get("ok"):
        raise TelegramError(body.get("description") or "Telegram refused the request")
    result = body.get("result")
    return result if isinstance(result, list) else []

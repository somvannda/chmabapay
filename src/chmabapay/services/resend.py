"""Resend — the email channel for billing reminders, and nothing else.

The only outbound mail in the product: one message per dunning tier, telling a merchant an
invoice is due. Deliberately the same shape as `telegram.py` — one module-level HTTP call so a
test can capture it, a typed error, and `is_configured()` as the single "can this deployment
send?" question rather than an `if settings.x` at every call site.

Three properties, all inherited from the activity feed for the same reasons:

* **It never raises to its caller on a delivery failure** — well, it does raise, and the
  *caller* decides. `send_email` reports; `billing.record_due_reminders` catches and records the
  outcome in `plan_invoice_reminders.detail`, because a provider outage must not fail the sweep
  that also writes the in-app rows.
* **Unset is silence, not failure.** No API key means no email channel. That was the state of
  every deployment before Phase B, and it costs the merchant nothing: the banner is derived
  from `due_at` and needs no provider at all.
* **The provider's idempotency key is set from the message, not from a clock.** A sweep that
  dies between the send and the commit retries the same tier with the same key, so Resend
  deduplicates it rather than delivering a second copy. The database constraint is still what
  prevents the ordinary double-send; this only covers the crash in between.
"""

from __future__ import annotations

import logging

import httpx

from ..config import get_settings

log = logging.getLogger(__name__)

API_BASE = "https://api.resend.com"


class ResendError(RuntimeError):
    """Resend did not accept the message. The message explains why."""


def is_configured() -> bool:
    """Whether this deployment holds the API key the email channel needs."""
    return bool((get_settings().resend_api_key or "").strip())


async def http_post(url: str, payload: dict, headers: dict) -> httpx.Response:
    """The single outbound call, kept at module level so tests can capture it."""
    settings = get_settings()
    async with httpx.AsyncClient(timeout=settings.resend_timeout_seconds) as client:
        return await client.post(url, json=payload, headers=headers)


async def send_email(
    *,
    to: str,
    subject: str,
    text: str,
    idempotency_key: str | None = None,
) -> str:
    """Deliver one message. Returns the provider's id. Raises `ResendError` if it did not go.

    The provider id is returned rather than discarded because it is the only thing that can
    reconcile a send against the Resend dashboard later — "we recorded that we sent it" and
    "they received it" are two different claims, and only one of them is ours.
    """
    settings = get_settings()
    key = (settings.resend_api_key or "").strip()
    if not key:
        raise ResendError("resend_api_key is not configured")

    headers = {"Authorization": f"Bearer {key}"}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key

    payload = {
        "from": settings.billing_email_from,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    try:
        response = await http_post(f"{API_BASE}/emails", payload, headers)
    except httpx.HTTPError as exc:
        raise ResendError(f"could not reach Resend: {exc}") from exc

    if response.status_code >= 400:
        raise ResendError(_detail(response))
    body = response.json()
    return str(body.get("id") or "")


def _detail(response: httpx.Response) -> str:
    """Resend answers with `{"message": ..., "name": ...}`; both are worth keeping.

    `name` is the machine-readable half (`validation_error`, `invalid_api_key`) and `message`
    the human one, so an operator reading the log gets the cause and a searchable term.
    """
    try:
        body = response.json()
    except ValueError:
        return f"HTTP {response.status_code}"
    message = body.get("message") or ""
    name = body.get("name") or ""
    return f"{name}: {message}".strip(": ") or f"HTTP {response.status_code}"

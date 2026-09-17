"""Webhook endpoints management with hybrid session + Bearer auth."""

from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models, webhooks
from ..auth import AuthContext, get_current_auth_context
from ..db import get_session
from ..security import new_secret, sign_payload, verify_signature

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8)
    secret_key: str | None = None
    events: list[str] | None = None


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, min_length=8)
    events: list[str] | None = None
    status: str | None = None
    enabled: bool | None = None


class WebhookOut(BaseModel):
    id: int
    url: str
    events: list[str] | None
    status: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, ep: models.WebhookEndpoint) -> WebhookOut:
        return cls(
            id=ep.id,
            url=ep.url,
            events=ep.events,
            status=ep.status,
            created_at=ep.created_at,
            updated_at=ep.updated_at,
        )


class WebhookTestOut(BaseModel):
    http_status: int | None
    response_body_preview: str | None
    signature_valid_vs_local: bool
    headers_sent: dict[str, str]


def _scope_endpoints_query(
    ctx: AuthContext,
    stmt: Any,
) -> Any:
    return stmt.where(models.WebhookEndpoint.account_id == ctx.account.id)


async def _load_endpoint(
    session: AsyncSession,
    ctx: AuthContext,
    endpoint_id: int,
) -> models.WebhookEndpoint:
    stmt = _scope_endpoints_query(
        ctx, select(models.WebhookEndpoint)
    ).where(models.WebhookEndpoint.id == endpoint_id)
    res = await session.execute(stmt)
    ep = res.scalar_one_or_none()
    if ep is None:
        raise HTTPException(status_code=404, detail="webhook_not_found")
    return ep


def _build_test_event_payload() -> dict:
    event_id = secrets.token_hex(16)
    now = datetime.now(UTC)
    return {
        "id": event_id,
        "type": "payment.completed",
        "created": now.isoformat(),
        "data": {
            "payment": {
                "id": "pay_test_" + secrets.token_urlsafe(8),
                "status": "paid",
                "amount": "25.50",
                "currency": "USD",
                "reference_id": "TEST-INV-001",
                "metadata": {"test": True},
                "approved_at": now.isoformat(),
            },
            "store": {
                "id": "store_test_" + secrets.token_urlsafe(4),
                "name": "Test Store",
                "owner_email": "test@example.com",
                "redirect_success_url": "https://example.com/success",
            },
        },
    }


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = _scope_endpoints_query(
        ctx, select(models.WebhookEndpoint)
    ).order_by(models.WebhookEndpoint.id.desc())
    res = await session.execute(stmt)
    endpoints = list(res.scalars().all())
    return [WebhookOut.from_model(ep) for ep in endpoints]


async def _get_active_plan(
    session: AsyncSession, account_id: int
) -> models.Plan | None:
    res = await session.execute(
        select(models.Plan)
        .join(
            models.PlanSubscription,
            models.PlanSubscription.plan_id == models.Plan.id,
        )
        .where(
            models.PlanSubscription.account_id == account_id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    return res.scalar_one_or_none()


async def _enforce_webhook_limit(
    session: AsyncSession,
    ctx: AuthContext,
) -> None:
    """Enforce the plan's account-wide webhook-endpoint limit.

    Endpoints are account-scoped — one endpoint serves every store — so the cap
    is per account, not per store.
    """
    plan = await _get_active_plan(session, ctx.account.id)
    if plan is None:
        return
    limit = getattr(plan, "max_webhooks_per_account", None)
    if limit is None:
        return
    res = await session.execute(
        select(func.count(models.WebhookEndpoint.id)).where(
            models.WebhookEndpoint.account_id == ctx.account.id
        )
    )
    count = res.scalar_one() or 0
    if count >= limit:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Max webhook endpoints ({limit}) reached for your plan. "
                "Delete one or upgrade."
            ),
        )


@router.post("", status_code=201)
async def create_webhook(
    body: WebhookCreate,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    await _enforce_webhook_limit(session, ctx)
    secret = body.secret_key if body.secret_key else new_secret()
    ep = models.WebhookEndpoint(
        account_id=ctx.account.id,
        url=body.url,
        secret_key=secret,
        events=body.events,
        status=models.WEBHOOK_ENDPOINT_ACTIVE,
    )
    session.add(ep)
    await session.flush()
    # A webhook endpoint is where a merchant's payment events are delivered, and
    # the signing secret is what proves they came from us — both are privileged.
    # The secret itself is never written to the trail.
    audit.record(
        session,
        actor=ctx.account,
        action="webhook.created",
        target_type="WebhookEndpoint",
        target_id=ep.id,
        details={"url": ep.url, "events": ep.events},
    )
    await session.commit()
    await session.refresh(ep)
    out = WebhookOut.from_model(ep).model_dump()
    out["signing_secret"] = secret
    return out


@router.patch("/{endpoint_id}", response_model=WebhookOut)
async def update_webhook(
    endpoint_id: int,
    body: WebhookPatch,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    ep = await _load_endpoint(session, ctx, endpoint_id)

    # Recorded as from/to rather than "the caller sent these fields": a webhook
    # URL being pointed somewhere new is how a merchant's events would be
    # diverted, and the old value is the half that makes that visible.
    changed: dict[str, Any] = {}
    if body.url is not None and body.url != ep.url:
        changed["url"] = {"from": ep.url, "to": body.url}
        ep.url = body.url
    if body.events is not None and body.events != ep.events:
        changed["events"] = {"from": ep.events, "to": body.events}
        ep.events = body.events
    if body.enabled is not None:
        status = "active" if body.enabled else "disabled"
        if status != ep.status:
            changed["status"] = {"from": ep.status, "to": status}
            ep.status = status
    elif body.status is not None and body.status != ep.status:
        changed["status"] = {"from": ep.status, "to": body.status}
        ep.status = body.status

    if not changed:
        return WebhookOut.from_model(ep)

    ep.updated_at = datetime.now(UTC)
    audit.record(
        session,
        actor=ctx.account,
        action="webhook.updated",
        target_type="WebhookEndpoint",
        target_id=ep.id,
        details={"changes": changed},
    )
    await session.commit()
    await session.refresh(ep)
    return WebhookOut.from_model(ep)


@router.delete("/{endpoint_id}", response_model=WebhookOut)
async def delete_webhook(
    endpoint_id: int,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    ep = await _load_endpoint(session, ctx, endpoint_id)
    details = {"url": ep.url}
    # Delivery rows reference the endpoint, so they have to go first or the
    # delete trips the foreign key.
    await session.execute(
        delete(models.EventDelivery).where(models.EventDelivery.endpoint_id == ep.id)
    )
    await session.delete(ep)
    # Written after the delete so the endpoint's id is free to reuse if the row
    # was the last reference to it; the url in `details` keeps it identifiable.
    audit.record(
        session,
        actor=ctx.account,
        action="webhook.deleted",
        target_type="WebhookEndpoint",
        target_id=endpoint_id,
        details=details,
    )
    await session.commit()
    return WebhookOut.from_model(ep)


@router.post("/{endpoint_id}/rotate-secret")
async def rotate_webhook_secret(
    endpoint_id: int,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    ep = await _load_endpoint(session, ctx, endpoint_id)
    secret = new_secret()
    ep.secret_key = secret
    ep.updated_at = datetime.now(UTC)
    # Rotating the secret invalidates what the merchant has deployed, so it is an
    # event the trail must carry. The secret itself is never recorded.
    audit.record(
        session,
        actor=ctx.account,
        action="webhook.secret_rotated",
        target_type="WebhookEndpoint",
        target_id=ep.id,
        details={"url": ep.url},
    )
    await session.commit()
    return {"signing_secret": secret}


@router.post("/{endpoint_id}/test", response_model=WebhookTestOut)
async def test_webhook(
    endpoint_id: int,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    ep = await _load_endpoint(session, ctx, endpoint_id)
    payload_dict = _build_test_event_payload()
    payload_bytes = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    t, sig = sign_payload(payload_bytes, ep.secret_key)
    sig_header = f"t={t},v1={sig}"
    headers = {
        "Content-Type": "application/json",
        webhooks.EVENT_HEADER: "payment.completed",
        webhooks.SIGNATURE_HEADER: sig_header,
    }
    local_valid = verify_signature(payload_bytes, ep.secret_key, sig_header)
    http_status: int | None = None
    body_preview: str | None = None
    try:
        async with httpx.AsyncClient(timeout=5.0, http2=False) as client:
            resp = await client.post(ep.url, content=payload_bytes, headers=headers)
            http_status = resp.status_code
            raw = resp.text
            body_preview = raw[:500] if raw else None
    except Exception as exc:
        body_preview = f"delivery_error: {str(exc)[:400]}"
    return WebhookTestOut(
        http_status=http_status,
        response_body_preview=body_preview,
        signature_valid_vs_local=local_valid,
        headers_sent=headers,
    )


class WebhookDeliveryOut(BaseModel):
    delivery_id: int
    event_id: str
    event_type: str
    http_status: int | None
    attempt_count: int
    response_body_preview: str | None
    created_at: datetime
    completed_at: datetime | None


@router.get("/{endpoint_id}/deliveries", response_model=list[WebhookDeliveryOut])
async def list_webhook_deliveries(
    endpoint_id: int,
    limit: int = Query(default=200, ge=1, le=500),
    page: int = Query(default=1, ge=1),
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    ep = await _load_endpoint(session, ctx, endpoint_id)
    offset = (page - 1) * limit
    stmt = (
        select(models.EventDelivery, models.Event)
        .join(models.Event, models.EventDelivery.event_id == models.Event.id)
        .where(models.EventDelivery.endpoint_id == ep.id)
        .order_by(models.EventDelivery.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    res = await session.execute(stmt)
    rows = list(res.all())
    deliveries = []
    for delivery, event in rows:
        is_completed = delivery.status in (models.DELIVERY_SUCCESS, models.DELIVERY_FAILED)
        preview: str | None = None
        if delivery.last_error:
            preview = delivery.last_error[:500]
        elif delivery.status == models.DELIVERY_SUCCESS:
            preview = "[delivery succeeded]"
        deliveries.append(
            WebhookDeliveryOut(
                delivery_id=delivery.id,
                event_id=delivery.event_id,
                event_type=event.type,
                http_status=delivery.last_response_status,
                attempt_count=delivery.attempts,
                response_body_preview=preview,
                created_at=delivery.created_at,
                completed_at=delivery.updated_at if is_completed else None,
            )
        )
    return deliveries

"""Webhook endpoints management with hybrid session + Bearer auth."""

from __future__ import annotations

import ipaddress
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models, webhooks
from ..auth import AuthContext, get_current_auth_context
from ..db import get_session
from ..openapi import AUTH_ERRORS, AUTH_SECURITY
from ..security import new_secret, verify_signature

router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["webhooks"],
    dependencies=AUTH_SECURITY,
    responses=AUTH_ERRORS,
)


def _validate_webhook_url(value: str) -> str:
    """Refuse a URL that cannot be a receiver, and the ones that must not be.

    The delivery POST is made by *this* server, so the URL is an instruction about
    where the platform connects. Scheme is therefore restricted to http(s) -
    `file://` or `gopher://` would be a protocol-smuggling primitive - and loopback,
    private, link-local and reserved addresses are refused because they aim that
    request at the platform's own network (metadata endpoints, the database, the
    API). Credentials in the URL are refused too: they would be logged on every
    attempt.

    This cannot stop a public hostname that resolves to a private address, and it is
    not meant to: the sender already posts to arbitrary merchant URLs by design. It
    stops the configurations that are never legitimate and are trivially avoidable.
    """
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https"):
        raise ValueError("url must be an http or https URL")
    if not parsed.hostname:
        raise ValueError("url must include a host")
    if parsed.username or parsed.password:
        raise ValueError("url must not include credentials")

    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".localhost") or host.endswith(".local"):
        raise ValueError("url must not point at a local host")

    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return value
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
    ):
        raise ValueError("url must not point at a private or local address")
    return value


class WebhookCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=8)
    secret_key: str | None = None
    events: list[str] | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str) -> str:
        return _validate_webhook_url(value)


class WebhookPatch(BaseModel):
    url: str | None = Field(default=None, min_length=8)
    events: list[str] | None = None
    status: str | None = None
    enabled: bool | None = None

    @field_validator("url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return None if value is None else _validate_webhook_url(value)


class WebhookOut(BaseModel):
    id: int
    url: str
    events: list[str] | None
    status: str
    # Derived from `status`, and published because the console edits this endpoint by
    # round-tripping it: it initialised the form from an `enabled` field that this
    # schema never returned, so the checkbox always resolved to true and saving an
    # unrelated edit silently re-enabled a disabled endpoint.
    enabled: bool
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, ep: models.WebhookEndpoint) -> WebhookOut:
        return cls(
            id=ep.id,
            url=ep.url,
            events=ep.events,
            status=ep.status,
            enabled=ep.status == models.WEBHOOK_ENDPOINT_ACTIVE,
            created_at=ep.created_at,
            updated_at=ep.updated_at,
        )


class WebhookTestOut(BaseModel):
    http_status: int | None
    response_body_preview: str | None
    signature_valid_vs_local: bool
    headers_sent: dict[str, str]


class WebhookFailureStoreOut(BaseModel):
    """One store whose events are stuck on a failing endpoint.

    Endpoints are account-scoped, so the same endpoint can carry events from every
    store. Breaking the count down by store is what turns "your webhook is failing"
    into "Sokha Cafe's payments are not reaching your server".
    """

    store_id: int
    store_name: str
    failed_count: int
    retrying_count: int
    last_failure_at: datetime | None


class WebhookHealthEndpointOut(BaseModel):
    endpoint_id: int
    url: str
    status: str
    failed_count: int
    retrying_count: int
    affected_store_count: int
    last_failure_at: datetime | None
    last_error: str | None
    last_response_status: int | None
    stores: list[WebhookFailureStoreOut]


class WebhookHealthOut(BaseModel):
    """What a merchant needs to answer "is my webhook working, and where is it failing?".

    `failed_count` mirrors the operator console's 24h count: terminal failures only,
    because a delivery still retrying is not yet a lost event. `retrying_count` is
    reported beside it so an endpoint that is currently down is visible before the
    attempt budget runs out.
    """

    window_hours: int
    failed_count: int
    retrying_count: int
    affected_endpoint_count: int
    affected_store_count: int
    generated_at: datetime
    endpoints: list[WebhookHealthEndpointOut]


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
    stmt = _scope_endpoints_query(ctx, select(models.WebhookEndpoint)).where(
        models.WebhookEndpoint.id == endpoint_id
    )
    res = await session.execute(stmt)
    ep = res.scalar_one_or_none()
    if ep is None:
        raise HTTPException(status_code=404, detail="webhook_not_found")
    return ep


def _build_test_event_payload() -> dict:
    """A test event built by the *real* payload builder.

    A hand-written payload is worse than none: it passes against a merchant handler
    written for a shape we never actually send, so "send test" reports success and the
    first live payment fails. The fields this used to omit — `financial` (the one the
    docs tell integrators to branch on), the `merchant` block, `paid_at`,
    `expires_at`, `settled_late` — were exactly the ones an integrator writes logic
    against.

    The models are transient: `build_event_payload` only reads, so nothing needs a
    database row to say what a real event looks like.
    """
    now = datetime.now(UTC)
    payment = models.Payment(
        public_id="pay_test_" + secrets.token_urlsafe(8),
        status=models.PAYMENT_PAID,
        amount_cents=2550,
        currency="USD",
        reference_id="TEST-INV-001",
        metadata_={"test": True},
        created_at=now,
        expires_at=now + timedelta(minutes=3),
        paid_at=now,
        approved_at=now,
    )
    store = models.Store(
        public_id="store_test_" + secrets.token_urlsafe(4),
        name="Test Store",
        redirect_success_url="https://example.com/success",
        external_id="TEST-STORE-001",
    )
    return webhooks.build_event_payload(
        secrets.token_hex(16), models.EVENT_COMPLETED, payment, store
    )


@router.get("/health", response_model=WebhookHealthOut)
async def webhook_health(
    window_hours: int = Query(default=24, ge=1, le=720),
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Delivery health for this account, grouped by endpoint and store.

    The merchant-facing deliveries route answers "what happened on this one endpoint"
    after the merchant already suspects something. This route is the notification
    surface: it says how many recent deliveries failed, which endpoint gave up, and
    which store's events were in them — before the merchant thinks to look.

    It deliberately uses the same window and status the operator console counts
    (`failed` in the last 24 hours by default), so the number on the merchant's
    dashboard and the number an operator sees are the same number. `retrying` rows are
    reported separately rather than folded in: a delivery still being retried is at
    risk, not yet lost, and conflating the two would cry wolf on every transient 500.
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=window_hours)
    problem_statuses = [models.DELIVERY_FAILED, models.DELIVERY_RETRYING]

    # One grouped query, not one per endpoint: an account can have several endpoints
    # and the console reads this on every dashboard page. Grouping by (endpoint,
    # store, status) gives both totals and the per-store breakdown in one pass.
    grouped = (
        await session.execute(
            select(
                models.WebhookEndpoint.id,
                models.WebhookEndpoint.url,
                models.WebhookEndpoint.status,
                models.Store.id,
                models.Store.name,
                models.EventDelivery.status,
                func.count(models.EventDelivery.id),
                func.max(models.EventDelivery.updated_at),
            )
            .join(
                models.Event,
                models.Event.id == models.EventDelivery.event_id,
            )
            .join(
                models.WebhookEndpoint,
                models.WebhookEndpoint.id == models.EventDelivery.endpoint_id,
            )
            .join(models.Store, models.Store.id == models.Event.store_id)
            .where(
                models.WebhookEndpoint.account_id == ctx.account.id,
                models.EventDelivery.status.in_(problem_statuses),
                models.EventDelivery.updated_at >= cutoff,
            )
            .group_by(
                models.WebhookEndpoint.id,
                models.WebhookEndpoint.url,
                models.WebhookEndpoint.status,
                models.Store.id,
                models.Store.name,
                models.EventDelivery.status,
            )
        )
    ).all()

    # The newest problem row per endpoint, for the one line of cause the merchant can
    # act on. Fetching by max(id) keeps this portable (SQLite and Postgres) and avoids
    # a window function for a single value per endpoint.
    latest_ids = (
        select(func.max(models.EventDelivery.id).label("delivery_id"))
        .join(
            models.WebhookEndpoint,
            models.WebhookEndpoint.id == models.EventDelivery.endpoint_id,
        )
        .where(
            models.WebhookEndpoint.account_id == ctx.account.id,
            models.EventDelivery.status.in_(problem_statuses),
            models.EventDelivery.updated_at >= cutoff,
        )
        .group_by(models.EventDelivery.endpoint_id)
        .subquery()
    )
    latest_rows = (
        await session.execute(
            select(
                models.EventDelivery.endpoint_id,
                models.EventDelivery.last_error,
                models.EventDelivery.last_response_status,
            ).join(
                latest_ids,
                models.EventDelivery.id == latest_ids.c.delivery_id,
            )
        )
    ).all()
    latest_by_endpoint = {
        endpoint_id: (last_error, last_response_status)
        for endpoint_id, last_error, last_response_status in latest_rows
    }

    endpoints: dict[int, dict[str, Any]] = {}
    for (
        endpoint_id,
        url,
        endpoint_status,
        store_id,
        store_name,
        delivery_status,
        count,
        last_seen,
    ) in grouped:
        entry = endpoints.setdefault(
            endpoint_id,
            {
                "endpoint_id": endpoint_id,
                "url": url,
                "status": endpoint_status,
                "failed_count": 0,
                "retrying_count": 0,
                "last_failure_at": None,
                "stores": {},
            },
        )
        if delivery_status == models.DELIVERY_FAILED:
            entry["failed_count"] += int(count or 0)
        else:
            entry["retrying_count"] += int(count or 0)
        if last_seen is not None and (
            entry["last_failure_at"] is None or last_seen > entry["last_failure_at"]
        ):
            entry["last_failure_at"] = last_seen

        store = entry["stores"].setdefault(
            store_id,
            {
                "store_id": store_id,
                "store_name": store_name,
                "failed_count": 0,
                "retrying_count": 0,
                "last_failure_at": last_seen,
            },
        )
        if delivery_status == models.DELIVERY_FAILED:
            store["failed_count"] += int(count or 0)
        else:
            store["retrying_count"] += int(count or 0)
        if last_seen is not None and (
            store["last_failure_at"] is None or last_seen > store["last_failure_at"]
        ):
            store["last_failure_at"] = last_seen

    endpoint_rows: list[WebhookHealthEndpointOut] = []
    affected_store_ids: set[int] = set()
    for entry in endpoints.values():
        stores = sorted(
            entry["stores"].values(),
            key=lambda s: (s["failed_count"], s["retrying_count"]),
            reverse=True,
        )
        affected_store_ids.update(s["store_id"] for s in stores)
        last_error, last_response_status = latest_by_endpoint.get(
            entry["endpoint_id"], (None, None)
        )
        endpoint_rows.append(
            WebhookHealthEndpointOut(
                endpoint_id=entry["endpoint_id"],
                url=entry["url"],
                status=entry["status"],
                failed_count=entry["failed_count"],
                retrying_count=entry["retrying_count"],
                affected_store_count=len(stores),
                last_failure_at=entry["last_failure_at"],
                last_error=last_error,
                last_response_status=last_response_status,
                stores=[WebhookFailureStoreOut(**s) for s in stores],
            )
        )

    endpoint_rows.sort(
        key=lambda e: (e.failed_count, e.retrying_count, e.endpoint_id),
        reverse=True,
    )
    return WebhookHealthOut(
        window_hours=window_hours,
        failed_count=sum(e.failed_count for e in endpoint_rows),
        retrying_count=sum(e.retrying_count for e in endpoint_rows),
        affected_endpoint_count=len(endpoint_rows),
        affected_store_count=len(affected_store_ids),
        generated_at=now,
        endpoints=endpoint_rows,
    )


@router.get("", response_model=list[WebhookOut])
async def list_webhooks(
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = _scope_endpoints_query(ctx, select(models.WebhookEndpoint)).order_by(
        models.WebhookEndpoint.id.desc()
    )
    res = await session.execute(stmt)
    endpoints = list(res.scalars().all())
    return [WebhookOut.from_model(ep) for ep in endpoints]


async def _get_active_plan(session: AsyncSession, account_id: int) -> models.Plan | None:
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
                f"Max webhook endpoints ({limit}) reached for your plan. Delete one or upgrade."
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
    # Same builder the sender uses, so the headers the merchant sees here are the
    # headers they will see on a real payment — including the User-Agent their
    # firewall may be allow-listing.
    headers = webhooks.delivery_headers(payload_bytes, ep.secret_key, models.EVENT_COMPLETED)
    local_valid = verify_signature(payload_bytes, ep.secret_key, headers[webhooks.SIGNATURE_HEADER])
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
    store_id: int
    store_name: str
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
        select(models.EventDelivery, models.Event, models.Store)
        .join(models.Event, models.EventDelivery.event_id == models.Event.id)
        .join(models.Store, models.Store.id == models.Event.store_id)
        .where(models.EventDelivery.endpoint_id == ep.id)
        .order_by(models.EventDelivery.created_at.desc())
        .offset(offset)
        .limit(limit)
    )
    res = await session.execute(stmt)
    rows = list(res.all())
    deliveries = []
    for delivery, event, store in rows:
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
                store_id=store.id,
                store_name=store.name,
                http_status=delivery.last_response_status,
                attempt_count=delivery.attempts,
                response_body_preview=preview,
                created_at=delivery.created_at,
                completed_at=delivery.updated_at if is_completed else None,
            )
        )
    return deliveries

"""Merchant support API — open a request, read the thread, reply, close.

Every route is scoped to `ctx.account.id` the way `routers/payments.py` and
`routers/webhooks.py` are: a request belonging to another account answers 404, never
another tenant's thread. The dashboard is the only intended caller, but a support
thread carries whatever a merchant chose to write, so the tenancy rule is enforced
server-side rather than trusted to the client.

Frozen (`restricted`) accounts are handled without a line of code here: the read gate
in `routers/auth.py` serves every GET and refuses every write except the billing escape
hatch, so a frozen merchant can read their threads and cannot open a new one — which is
exactly what F-03 asks for.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..auth import AuthContext, get_current_auth_context
from ..db import get_session
from ..openapi import AUTH_ERRORS, AUTH_SECURITY
from ..services import support

router = APIRouter(
    prefix="/api/v1/support",
    tags=["support"],
    dependencies=AUTH_SECURITY,
    responses=AUTH_ERRORS,
)


class SupportMessageOut(BaseModel):
    id: int
    author_kind: str
    body: str
    created_at: datetime

    @classmethod
    def from_model(cls, message: models.SupportMessage) -> SupportMessageOut:
        return cls(
            id=message.id,
            author_kind=message.author_kind,
            body=message.body,
            created_at=message.created_at,
        )


class SupportRequestOut(BaseModel):
    id: str
    subject: str
    category: str
    status: str
    priority: str
    first_response_at: datetime | None
    resolved_at: datetime | None
    created_at: datetime
    # The target for the account's plan, published so the portal states the number the
    # platform measures against instead of hardcoding "24 hours" a second time — see
    # `services.support.response_target_hours`.
    response_target_hours: int | None
    messages: list[SupportMessageOut]

    @classmethod
    def from_model(
        cls,
        request: models.SupportRequest,
        *,
        messages: list[models.SupportMessage],
        target_hours: int | None,
    ) -> SupportRequestOut:
        return cls(
            id=request.public_id,
            subject=request.subject,
            category=request.category,
            status=request.status,
            priority=request.priority,
            first_response_at=request.first_response_at,
            resolved_at=request.resolved_at,
            created_at=request.created_at,
            response_target_hours=target_hours,
            messages=[SupportMessageOut.from_model(m) for m in messages],
        )


class SupportRequestListOut(BaseModel):
    data: list[SupportRequestOut]
    # Repeated at the top level on purpose. The portal has to state the account's
    # response-time target (F-10), and an account that has never opened a request has no
    # request to read it from — so a per-request-only field would leave the platform's
    # own promise invisible to exactly the merchants who have not needed it yet. It is
    # the same number the per-request field carries; there is one place it is computed
    # (`services.support.response_target_hours`) and nothing here recomputes it.
    response_target_hours: int | None = None


class SupportRequestCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=200)
    # A closed set, so the operator queue's category filter is always meaningful. A
    # `pattern` built from the constants rather than a copied `Literal`, so adding a
    # category is one edit in `models.py`.
    category: str = Field(
        default=models.SUPPORT_CATEGORY_OTHER,
        pattern="^(" + "|".join(models.SUPPORT_CATEGORIES) + ")$",
    )
    body: str = Field(min_length=1, max_length=8000)


class SupportReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=8000)


async def _load_request(
    session: AsyncSession, ctx: AuthContext, public_id: str
) -> models.SupportRequest:
    """Load a request the caller's account owns, or 404.

    The account filter is part of the lookup, not a check on the result, so a request
    belonging to another account is indistinguishable from one that does not exist.
    """
    res = await session.execute(
        select(models.SupportRequest).where(
            models.SupportRequest.account_id == ctx.account.id,
            models.SupportRequest.public_id == public_id,
        )
    )
    request = res.scalar_one_or_none()
    if request is None:
        raise HTTPException(status_code=404, detail="support_request_not_found")
    return request


async def _request_out(
    session: AsyncSession, request: models.SupportRequest, account_id: int
) -> SupportRequestOut:
    messages = await support.load_messages(session, request.id)
    target_hours = await support.target_hours_for_account(session, account_id)
    return SupportRequestOut.from_model(
        request, messages=messages, target_hours=target_hours
    )


@router.post("/requests", status_code=201, response_model=SupportRequestOut)
async def open_request(
    body: SupportRequestCreate,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Open a request with its opening text as message #1.

    The alert to the operator channel runs *after* the commit: it is a courtesy, and a
    Telegram outage must not turn an opened request into a failed one. See
    `services.support.alert_priority_opened`.
    """
    request = await support.open_request(
        session, ctx.account, body.subject, body.category, body.body
    )
    await session.commit()
    await session.refresh(request)
    await support.alert_priority_opened(request, ctx.account)
    return await _request_out(session, request, ctx.account.id)


@router.get("/requests", response_model=SupportRequestListOut)
async def list_requests(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """The account's own requests, newest first.

    Ordered by `id` rather than `created_at`: ids are monotonic and `created_at` is not
    guaranteed distinct, so a tie would let a row appear on two pages.
    """
    res = await session.execute(
        select(models.SupportRequest)
        .where(models.SupportRequest.account_id == ctx.account.id)
        .order_by(models.SupportRequest.id.desc())
        .limit(limit)
        .offset(offset)
    )
    requests = list(res.scalars().all())
    threads = await support.messages_by_request(session, [r.id for r in requests])
    target_hours = await support.target_hours_for_account(session, ctx.account.id)
    return SupportRequestListOut(
        data=[
            SupportRequestOut.from_model(
                request,
                messages=threads.get(request.id, []),
                target_hours=target_hours,
            )
            for request in requests
        ],
        response_target_hours=target_hours,
    )


@router.get("/requests/{public_id}", response_model=SupportRequestOut)
async def get_request(
    public_id: str,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    request = await _load_request(session, ctx, public_id)
    return await _request_out(session, request, ctx.account.id)


@router.post("/requests/{public_id}/reply", response_model=SupportRequestOut)
async def reply_to_request(
    public_id: str,
    body: SupportReplyIn,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Add the merchant's message to the thread.

    This does not satisfy the first-response target and does not touch
    `first_response_at` — see `services.support.record_merchant_reply`.
    """
    request = await _load_request(session, ctx, public_id)
    await support.record_merchant_reply(session, request, ctx.account, body.body)
    await session.commit()
    await session.refresh(request)
    return await _request_out(session, request, ctx.account.id)


@router.post("/requests/{public_id}/close", response_model=SupportRequestOut)
async def close_request(
    public_id: str,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    """Resolve the thread.

    Idempotent: closing an already-resolved request returns it unchanged rather than
    moving `resolved_at`, which would rewrite when the platform finished with it.
    """
    request = await _load_request(session, ctx, public_id)
    if request.status != models.SUPPORT_RESOLVED:
        support.resolve_request(session, request, datetime.now(UTC))
        await session.commit()
        await session.refresh(request)
    return await _request_out(session, request, ctx.account.id)

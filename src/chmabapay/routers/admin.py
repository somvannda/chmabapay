"""Admin management router with hybrid auth + admin gate."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..auth import KeyContext, resolve_key_context
from ..config import get_settings
from ..db import get_session
from ..healthcheck import DEFAULT_MAX_AGE_SECONDS, queue_heartbeat_ages
from ..schemas import LinkIn
from ..services import billing as billing_svc
from ..services import stores as store_svc
from ..services.bakong import get_bakong_client
from ..services.payments import (
    count_paid_payments_this_month,
    gen_public_id,
    mark_paid,
)

# The same slug parser the HQ seed uses: a PayWay share link's merchant account id is
# the last path segment, and `_extract_slug` is the one place that knows the shape.
from ..services.payway_parser import _extract_slug
from ..services.status_reconciler import reconcile_payment
from ..workers.job import JobStatus
from ..workers.runtime import build_transport, worker_registry
from .auth import get_current_session_account, session_auth_method

router = APIRouter(
    prefix="/v1/admin",
    tags=["admin"],
    # Not published. The console's surface is internal — it is reached from
    # admin.chmaba.com behind a platform-admin session, and every route in it is
    # hidden from the public `openapi.json` so the platform's own operator API is not
    # advertised to merchants reading `/docs`. The routes still exist and still
    # enforce `get_hybrid_admin_context`.
    include_in_schema=False,
)

# Pricing-page feature bullets, edited per plan in the console.
FEATURES_MAX = 12
FEATURE_MAX_LEN = 80


@dataclass
class HybridAuthContext:
    account: models.Account
    key_ctx: KeyContext | None
    is_session: bool


async def get_hybrid_admin_context(
    request: Request,
    authorization: str | None = Header(default=None),
    session_account: models.Account | None = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
) -> HybridAuthContext:
    """Resolve an admin caller, or refuse.

    Two gates, and the second one used to live only in the browser. The console
    tells operators it is password-only (`web/admin/README.md`), and the React
    shell does refuse an SSO session — but nothing on the server did, so an
    admin's Google session could call every `/v1/admin/*` route directly with
    curl. `session_auth_method` existed for exactly this and was only ever read
    by `GET /v1/me`. It is enforced here now, and it fails *closed*: a token
    minted before the `amr` claim existed reports "unknown" and is refused
    rather than assumed to be a password session.

    The dev gateway is the one exception, and it is conditional on the flag that
    also mounts the dev routes — which production hardcodes to false, so the
    exception cannot exist there. Without it, a local console would need a
    hand-set password before it could be opened at all.

    An API key is still accepted: a `ck_` value is a revocable, hashed,
    workspace-scoped credential rather than an SSO session, so the rule this
    guard exists to enforce does not apply to it.
    """
    if authorization and authorization.startswith("Bearer ck_"):
        key_ctx = await resolve_key_context(session, authorization)
        ctx = HybridAuthContext(
            account=key_ctx.account, key_ctx=key_ctx, is_session=False
        )
    elif session_account is not None:
        method = session_auth_method(request)
        allowed = method == "password" or (
            method == "dev" and get_settings().enable_dev_gateway
        )
        if not allowed:
            raise HTTPException(
                status_code=403, detail="password_session_required"
            )
        ctx = HybridAuthContext(
            account=session_account, key_ctx=None, is_session=True
        )
    else:
        raise HTTPException(status_code=401, detail="unauthorized")
    if not ctx.account.is_platform_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    return ctx


def _account_profile(account: models.Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "email": account.email,
        "name": account.name,
        "status": account.status,
        "whitelabel_enabled": account.whitelabel_enabled,
        "is_platform_admin": account.is_platform_admin,
        "has_password": account.password_hash is not None,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


class Pagination(BaseModel):
    page: int
    per_page: int
    total_rows: int
    total_pages: int


def _clamp_paging(page: int, per_page: int) -> tuple[int, int, int]:
    """Normalise page/per_page and return the offset."""
    page = max(page, 1)
    per_page = min(max(per_page, 1), 100)
    return page, per_page, (page - 1) * per_page


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #


class AdminAccountRowOut(BaseModel):
    id: int
    email: str
    name: str
    status: str
    is_platform_admin: bool
    plan_code: str | None
    plan_name: str | None
    subscription_status: str | None
    stores_count: int
    payments_count: int
    created_at: datetime


class AdminAccountListOut(BaseModel):
    data: list[AdminAccountRowOut]
    pagination: Pagination


async def _active_admin_count(session: AsyncSession) -> int:
    """How many platform admins can still sign in.

    Suspended admins are excluded deliberately: they do not count as a way back in.
    """
    return (
        await session.execute(
            select(func.count(models.Account.id)).where(
                models.Account.is_platform_admin.is_(True),
                models.Account.status == models.ACCOUNT_ACTIVE,
            )
        )
    ).scalar_one() or 0


async def _counts_by_account(
    session: AsyncSession, account_ids: list[int]
) -> tuple[dict[int, int], dict[int, int]]:
    """Store and payment totals for one page of accounts, in two queries."""
    if not account_ids:
        return {}, {}
    store_rows = (
        await session.execute(
            select(models.Store.account_id, func.count(models.Store.id))
            .where(models.Store.account_id.in_(account_ids))
            .group_by(models.Store.account_id)
        )
    ).all()
    payment_rows = (
        await session.execute(
            select(models.Store.account_id, func.count(models.Payment.id))
            .join(models.Payment, models.Payment.store_id == models.Store.id)
            .where(models.Store.account_id.in_(account_ids))
            .group_by(models.Store.account_id)
        )
    ).all()
    return (
        {aid: count for aid, count in store_rows},
        {aid: count for aid, count in payment_rows},
    )


async def _plans_by_account(
    session: AsyncSession, account_ids: list[int]
) -> dict[int, tuple[str, str, str]]:
    if not account_ids:
        return {}
    rows = (
        await session.execute(
            select(
                models.PlanSubscription.account_id,
                models.Plan.code,
                models.Plan.name,
                models.PlanSubscription.status,
            )
            .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
            .where(
                models.PlanSubscription.account_id.in_(account_ids),
                # `pending` is included on purpose: a plan purchase waiting on its
                # invoice is the only record of what that invoice buys, so hiding it
                # left the console showing "—" next to an open invoice.
                models.PlanSubscription.status.in_(["trial", "active", "pending"]),
            )
            # Ordered by preference, so an account mid-upgrade still reports the plan
            # it is *on*: active first, then trial, then a pending purchase.
            .order_by(
                case(
                    (models.PlanSubscription.status == "active", 0),
                    (models.PlanSubscription.status == "trial", 1),
                    else_=2,
                ),
                models.PlanSubscription.id,
            )
        )
    ).all()
    plans: dict[int, tuple[str, str, str]] = {}
    for aid, code, name, status in rows:
        # First row per account wins, which is the highest-priority status above.
        plans.setdefault(aid, (code, name, status))
    return plans


@router.get("/accounts", response_model=AdminAccountListOut)
async def list_accounts(
    q: str | None = None,
    page: int = 1,
    per_page: int = 25,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    page, per_page, offset = _clamp_paging(page, per_page)

    filters = []
    if q and q.strip():
        like = f"%{q.strip()}%"
        filters.append(
            or_(models.Account.email.ilike(like), models.Account.name.ilike(like))
        )

    total_rows = (
        await session.execute(
            select(func.count(models.Account.id)).where(*filters)
        )
    ).scalar_one() or 0

    accounts = list(
        (
            await session.execute(
                select(models.Account)
                .where(*filters)
                .order_by(models.Account.id)
                .limit(per_page)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    account_ids = [a.id for a in accounts]
    store_counts, payment_counts = await _counts_by_account(session, account_ids)
    plans = await _plans_by_account(session, account_ids)

    data = []
    for account in accounts:
        plan_code, plan_name, sub_status = plans.get(account.id, (None, None, None))
        data.append(
            AdminAccountRowOut(
                id=account.id,
                email=account.email,
                name=account.name,
                status=account.status,
                is_platform_admin=account.is_platform_admin,
                plan_code=plan_code,
                plan_name=plan_name,
                subscription_status=sub_status,
                stores_count=store_counts.get(account.id, 0),
                payments_count=payment_counts.get(account.id, 0),
                created_at=account.created_at,
            )
        )

    return AdminAccountListOut(
        data=data,
        pagination=Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ),
    )


@router.get("/accounts/{account_id}")
async def get_account_detail(
    account_id: int,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    account = await session.get(models.Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")

    store_counts, payment_counts = await _counts_by_account(session, [account_id])
    plan_code, plan_name, sub_status = (
        await _plans_by_account(session, [account_id])
    ).get(account_id, (None, None, None))

    stores = list(
        (
            await session.execute(
                select(models.Store)
                .where(models.Store.account_id == account_id)
                .order_by(models.Store.id)
            )
        )
        .scalars()
        .all()
    )
    invoices = list(
        (
            await session.execute(
                select(models.PlanInvoice)
                .where(models.PlanInvoice.account_id == account_id)
                .order_by(models.PlanInvoice.period_month.desc())
                .limit(24)
            )
        )
        .scalars()
        .all()
    )
    keys = list(
        (
            await session.execute(
                select(models.ApiKey)
                .where(models.ApiKey.account_id == account_id)
                .order_by(models.ApiKey.id)
            )
        )
        .scalars()
        .all()
    )
    # The plan's limits and where the account stands against them, so a decision to
    # suspend, revoke or comp is made with the merchant's actual position visible
    # rather than discovered afterwards.
    plan_row = (
        (
            await session.execute(
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
        )
        .scalars()
        .first()
    )

    return {
        "account": _account_profile(account),
        "plan": {
            "code": plan_code,
            "name": plan_name,
            "subscription_status": sub_status,
        },
        "counts": {
            "stores": store_counts.get(account_id, 0),
            "payments": payment_counts.get(account_id, 0),
        },
        "limits": (
            None
            if plan_row is None
            else {
                "plan_code": plan_row.code,
                "max_stores": plan_row.max_stores,
                "max_keys_per_account": plan_row.max_keys_per_account,
                "payments_included": plan_row.base_payments_included,
            }
        ),
        "usage": {
            "keys_active": sum(
                1 for key in keys if key.status == models.ACCOUNT_ACTIVE
            ),
            "payments_this_month": await count_paid_payments_this_month(
                session, account_id
            ),
        },
        "keys": [
            {
                "id": key.id,
                "name": key.name,
                "key_prefix": key.key_prefix,
                "mode": key.mode,
                "status": key.status,
                "last_used_at": key.last_used_at,
                "created_at": key.created_at,
                "revoked_at": key.revoked_at,
            }
            for key in keys
        ],
        "stores": [
            {
                "id": store.public_id,
                "name": store.name,
                "external_id": store.external_id,
                "status": store.status,
                "is_internal": store.is_internal,
            }
            for store in stores
        ],
        "invoices": [_invoice_row(inv, account.email) for inv in invoices],
    }


class AdminAccountPatch(BaseModel):
    """Entitlements an operator can grant or revoke by hand, plus standing."""

    whitelabel_enabled: bool | None = None
    # `Account.status` is enforced at sign-in and on every authenticated request,
    # but nothing wrote it: the platform could lock an operator out by setting the
    # column by hand and had no supported way to do it, and no record of who did.
    status: str | None = Field(
        default=None, pattern=f"^({models.ACCOUNT_ACTIVE}|{models.ACCOUNT_SUSPENDED})$"
    )
    reason: str | None = Field(default=None, max_length=500)


# A standing change is named for what it does. An operator reading the trail
# should not have to diff a field to find the suspension.
_STATUS_ACTIONS = {
    models.ACCOUNT_SUSPENDED: "account.suspended",
    models.ACCOUNT_ACTIVE: "account.activated",
}


@router.patch("/accounts/{account_id}")
async def update_account(
    account_id: int,
    body: AdminAccountPatch,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    account = await session.get(models.Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")

    changed: dict[str, Any] = {}
    if body.whitelabel_enabled is not None:
        changed["whitelabel_enabled"] = {
            "from": account.whitelabel_enabled,
            "to": body.whitelabel_enabled,
        }
        account.whitelabel_enabled = body.whitelabel_enabled
    if body.status is not None and body.status != account.status:
        if (
            body.status == models.ACCOUNT_SUSPENDED
            and account.is_platform_admin
            and await _active_admin_count(session) <= 1
        ):
            # Suspension is enforced at sign-in and on every authenticated request, so
            # suspending the last active platform admin locks the console for good —
            # there is no route back in that does not involve a database session.
            raise HTTPException(status_code=409, detail="last_platform_admin")
        changed["status"] = {"from": account.status, "to": body.status}
        account.status = body.status

    if not changed:
        return _account_profile(account)

    account.updated_at = datetime.now(UTC)
    details: dict[str, Any] = {"changes": changed}
    if body.reason:
        details["reason"] = body.reason
    action = "account.updated"
    if "status" in changed:
        action = _STATUS_ACTIONS.get(changed["status"]["to"], action)
    audit.record(
        session,
        actor=ctx.account,
        action=action,
        target_type="Account",
        target_id=account.id,
        details=details,
    )
    await session.commit()
    await session.refresh(account)
    return _account_profile(account)


class AdminPlanAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_code: str = Field(min_length=1, max_length=40)
    # Mandatory. This is the one route on the platform that hands out a paid tier with
    # no money attached to it, so the answer to "why is this account on Pro" has to be
    # in the record rather than in somebody's memory.
    reason: str = Field(min_length=3, max_length=500)


@router.patch("/accounts/{account_id}/plan")
async def assign_account_plan(
    account_id: int,
    body: AdminPlanAssignIn,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Put an account on a plan without an invoice, on an operator's authority.

    The merchant-facing route *sells* a plan: it parks the subscription as `pending`
    and raises an invoice that activates it when paid. That is the right default and
    the wrong only-tool — a comped account, a migration, or an invoice settled outside
    the platform all need a plan in force with no money attached.

    Deliberately blunt, and the reason is why: whatever the account is on is cancelled
    and the named plan is active immediately, with no invoice and no proration. The
    audit row records the operator, both plan codes and the monthly fee that was
    given up.
    """
    account = await session.get(models.Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="account_not_found")

    plan = (
        await session.execute(
            select(models.Plan).where(models.Plan.code == body.plan_code)
        )
    ).scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")

    now = datetime.now(UTC)
    current = (
        (
            await session.execute(
                select(models.PlanSubscription).where(
                    models.PlanSubscription.account_id == account_id,
                    models.PlanSubscription.status.in_(["trial", "active"]),
                )
            )
        )
        .scalars()
        .first()
    )
    current_plan_code: str | None = None
    if current is not None:
        current_plan_code = (
            await session.execute(
                select(models.Plan.code).where(models.Plan.id == current.plan_id)
            )
        ).scalar_one_or_none()
        if current.plan_id == plan.id:
            # Re-selecting the plan the account is already on is not a change, and
            # recording it would put a "granted Pro" row in the trail for nothing.
            raise HTTPException(status_code=400, detail="plan_unchanged")
        current.status = "canceled"
        current.canceled_at = now
        current.updated_at = now

    subscription = models.PlanSubscription(
        account_id=account_id,
        plan_id=plan.id,
        status="active",
        started_at=now,
        # The same 30-day period the paid path uses, so a comped plan comes back
        # through the billing sweep on schedule instead of living forever by accident.
        next_billing_at=now + billing_svc.CREDIT_PERIOD,
    )
    session.add(subscription)
    await session.flush()

    audit.record(
        session,
        actor=ctx.account,
        action="account.plan_assigned",
        target_type="Account",
        target_id=account_id,
        details={
            "from_plan": current_plan_code,
            "to_plan": plan.code,
            "monthly_fee_cents": plan.monthly_fee_cents,
            "reason": body.reason,
        },
    )
    await session.commit()
    await session.refresh(subscription)
    return {
        "account_id": account_id,
        "plan_code": plan.code,
        "subscription_id": subscription.id,
        "subscription_status": subscription.status,
        "next_billing_at": subscription.next_billing_at,
    }


# --------------------------------------------------------------------------- #
# Plans
# --------------------------------------------------------------------------- #


class AdminPlanOut(BaseModel):
    id: int
    code: str
    name: str
    monthly_fee_cents: int
    base_payments_included: int
    max_stores: int | None
    max_keys_per_account: int
    max_webhooks_per_account: int
    priority_support: bool
    is_public: bool
    is_active: bool
    tagline: str | None
    features: list[str] | None
    is_featured: bool
    subscriptions_count: int

    @classmethod
    def from_model(
        cls, plan: models.Plan, subscriptions_count: int = 0
    ) -> AdminPlanOut:
        return cls(
            id=plan.id,
            code=plan.code,
            name=plan.name,
            monthly_fee_cents=plan.monthly_fee_cents,
            base_payments_included=plan.base_payments_included,
            max_stores=plan.max_stores,
            max_keys_per_account=plan.max_keys_per_account,
            max_webhooks_per_account=plan.max_webhooks_per_account,
            priority_support=plan.priority_support,
            is_public=plan.is_public,
            is_active=plan.is_active,
            tagline=plan.tagline,
            features=list(plan.features) if plan.features else None,
            is_featured=plan.is_featured,
            subscriptions_count=subscriptions_count,
        )


async def _plan_subscription_counts(
    session: AsyncSession, plan_ids: list[int]
) -> dict[int, int]:
    """How many subscriptions reference each plan — plans in use cannot be deleted."""
    if not plan_ids:
        return {}
    rows = await session.execute(
        select(
            models.PlanSubscription.plan_id,
            func.count(models.PlanSubscription.id),
        )
        .where(models.PlanSubscription.plan_id.in_(plan_ids))
        .group_by(models.PlanSubscription.plan_id)
    )
    return {plan_id: count for plan_id, count in rows.all()}


async def _plan_out(session: AsyncSession, plan: models.Plan) -> AdminPlanOut:
    counts = await _plan_subscription_counts(session, [plan.id])
    return AdminPlanOut.from_model(plan, counts.get(plan.id, 0))


def _clean_features(value: list[str] | None) -> list[str] | None:
    """Trim the bullets and enforce sane bounds so the pricing page cannot be fed
    an unbounded list from the admin console."""
    if value is None:
        return None
    cleaned = [item.strip() for item in value if item and item.strip()]
    if len(cleaned) > FEATURES_MAX:
        raise ValueError(f"too_many_features: max {FEATURES_MAX}")
    for item in cleaned:
        if len(item) > FEATURE_MAX_LEN:
            raise ValueError(f"feature_too_long: max {FEATURE_MAX_LEN} characters")
    return cleaned


class AdminPlanPatch(BaseModel):
    """Partial plan update. Omitted fields are left alone; sending
    `"max_stores": null` clears the cap (unlimited)."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    monthly_fee_cents: int | None = Field(default=None, ge=0)
    base_payments_included: int | None = Field(default=None, ge=0)
    max_stores: int | None = Field(default=None, ge=0)
    max_keys_per_account: int | None = Field(default=None, ge=0)
    max_webhooks_per_account: int | None = Field(default=None, ge=0)
    priority_support: bool | None = None
    is_public: bool | None = None
    is_active: bool | None = None
    tagline: str | None = Field(default=None, max_length=160)
    features: list[str] | None = None
    is_featured: bool | None = None

    _validate_features = field_validator("features")(_clean_features)


class AdminPlanCreate(BaseModel):
    """Create a plan. `code` is the stable identifier the API and the pricing page
    reference, so it is validated and immutable after creation."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=2, max_length=32, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=64)
    monthly_fee_cents: int = Field(default=0, ge=0)
    base_payments_included: int = Field(default=0, ge=0)
    max_stores: int | None = Field(default=None, ge=0)
    max_keys_per_account: int = Field(default=5, ge=0)
    max_webhooks_per_account: int = Field(default=5, ge=0)
    priority_support: bool = False
    is_public: bool = True
    is_active: bool = True
    tagline: str | None = Field(default=None, max_length=160)
    features: list[str] | None = None
    is_featured: bool = False

    _validate_features = field_validator("features")(_clean_features)


class AdminPlanDeleteOut(BaseModel):
    """A plan referenced by subscriptions is retired (hidden + inactive) instead of
    deleted, so historical invoices keep resolving."""

    retired: bool
    deleted: bool
    plan: AdminPlanOut | None


@router.get("/plans", response_model=list[AdminPlanOut])
async def list_all_plans(
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Every plan, including retired and hidden ones."""
    plans = list(
        (
            await session.execute(
                select(models.Plan).order_by(
                    models.Plan.monthly_fee_cents, models.Plan.id
                )
            )
        )
        .scalars()
        .all()
    )
    counts = await _plan_subscription_counts(session, [p.id for p in plans])
    return [AdminPlanOut.from_model(p, counts.get(p.id, 0)) for p in plans]


@router.post("/plans", status_code=201, response_model=AdminPlanOut)
async def create_plan(
    body: AdminPlanCreate,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    existing = (
        await session.execute(select(models.Plan).where(models.Plan.code == body.code))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=409, detail="plan_code_exists")

    plan = models.Plan(**body.model_dump())
    session.add(plan)
    await session.flush()
    audit.record(
        session,
        actor=ctx.account,
        action="plan.created",
        target_type="Plan",
        target_id=plan.id,
        details={"plan_code": plan.code, "name": plan.name},
    )
    await session.commit()
    await session.refresh(plan)
    return await _plan_out(session, plan)


@router.patch("/plans/{plan_id}", response_model=AdminPlanOut)
async def update_plan(
    plan_id: int,
    body: AdminPlanPatch,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    plan = await session.get(models.Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")

    changed: dict[str, Any] = {}
    for field in body.model_fields_set:
        new_value = getattr(body, field)
        old_value = getattr(plan, field)
        if old_value != new_value:
            changed[field] = {"from": old_value, "to": new_value}
            setattr(plan, field, new_value)

    if not changed:
        return await _plan_out(session, plan)

    plan.updated_at = datetime.now(UTC)
    audit.record(
        session,
        actor=ctx.account,
        action="plan.updated",
        target_type="Plan",
        target_id=plan.id,
        details={"plan_code": plan.code, "changes": changed},
    )
    await session.commit()
    await session.refresh(plan)
    return await _plan_out(session, plan)


@router.delete("/plans/{plan_id}", response_model=AdminPlanDeleteOut)
async def delete_plan(
    plan_id: int,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Delete a plan outright, or retire it when subscriptions still reference it
    (retiring keeps them — and their invoices — resolvable)."""
    plan = await session.get(models.Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")

    counts = await _plan_subscription_counts(session, [plan.id])
    in_use = counts.get(plan.id, 0) > 0

    if in_use:
        plan.is_active = False
        plan.is_public = False
        plan.updated_at = datetime.now(UTC)
        audit.record(
            session,
            actor=ctx.account,
            action="plan.retired",
            target_type="Plan",
            target_id=plan.id,
            details={"plan_code": plan.code, "subscriptions": counts[plan.id]},
        )
        await session.commit()
        await session.refresh(plan)
        return AdminPlanDeleteOut(
            retired=True, deleted=False, plan=await _plan_out(session, plan)
        )

    code = plan.code
    await session.delete(plan)
    audit.record(
        session,
        actor=ctx.account,
        action="plan.deleted",
        target_type="Plan",
        target_id=plan_id,
        details={"plan_code": code},
    )
    await session.commit()
    return AdminPlanDeleteOut(retired=False, deleted=True, plan=None)


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #


def _invoice_row(
    invoice: models.PlanInvoice,
    account_email: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    due_at = billing_svc.as_utc(invoice.due_at)
    moment = now or datetime.now(UTC)
    return {
        "id": invoice.id,
        "account_id": invoice.account_id,
        "account_email": account_email,
        "period_month": invoice.period_month,
        "period_start": invoice.period_start,
        "period_end": invoice.period_end,
        "status": invoice.status,
        "base_fee_cents": invoice.base_fee_cents,
        "usage_payments_count": invoice.usage_payments_count,
        "overage_fee_cents": invoice.overage_fee_cents,
        "total_due_cents": invoice.total_due_cents,
        "due_at": due_at,
        # Derived, never read off `status`: the worker writes `open` for an invoice due next
        # week and for one a week late, so a status column cannot answer "what is about to
        # lapse" — the question this list exists to answer.
        "is_overdue": (
            invoice.status in billing_svc.UNPAID_STATUSES
            and due_at is not None
            and due_at < moment
        ),
        "paid_at": invoice.paid_at,
        "voided_at": invoice.voided_at,
        "void_reason": invoice.void_reason,
        "created_at": invoice.created_at,
    }


@router.get("/invoices")
async def list_all_invoices(
    period_month: str | None = None,
    status: str | None = None,
    overdue: bool = False,
    page: int = 1,
    per_page: int = 25,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Plan invoices across every account.

    `overdue=true` is the operator's dunning queue: unpaid invoices whose `due_at` has
    passed, most overdue first, so a freeze can be seen coming without opening each row.
    """
    page, per_page, offset = _clamp_paging(page, per_page)
    now = datetime.now(UTC)

    filters = []
    if period_month:
        filters.append(models.PlanInvoice.period_month == period_month)
    if status:
        filters.append(models.PlanInvoice.status == status)
    if overdue:
        filters.append(models.PlanInvoice.status.in_(billing_svc.UNPAID_STATUSES))
        filters.append(models.PlanInvoice.due_at.is_not(None))
        filters.append(models.PlanInvoice.due_at < now)

    total_rows = (
        await session.execute(
            select(func.count(models.PlanInvoice.id)).where(*filters)
        )
    ).scalar_one() or 0

    order = (
        # Most overdue first, so the queue reads as an order of work.
        [models.PlanInvoice.due_at.asc(), models.PlanInvoice.id.asc()]
        if overdue
        else [models.PlanInvoice.period_month.desc(), models.PlanInvoice.id.desc()]
    )

    rows = (
        await session.execute(
            select(models.PlanInvoice, models.Account.email)
            .join(models.Account, models.Account.id == models.PlanInvoice.account_id)
            .where(*filters)
            .order_by(*order)
            .limit(per_page)
            .offset(offset)
        )
    ).all()

    return {
        "data": [_invoice_row(inv, email, now) for inv, email in rows],
        "pagination": Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ).model_dump(),
    }


class InvoiceResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["mark-paid", "waive", "credit", "void"]
    reason: str = Field(min_length=3, max_length=500)
    # `credit` only: how much was given up. Defaults to the whole invoice. Never
    # subtracted from `total_due_cents` — the invoice goes on saying what was billed,
    # and the audit row says what was forgiven.
    amount_cents: int | None = Field(default=None, ge=0)


_RESOLVED_INVOICE_STATUS = {
    "mark-paid": "paid",
    "waive": "waived",
    "credit": "credited",
    "void": billing_svc.INVOICE_VOID,
}


@router.post("/invoices/{invoice_id}/resolve")
async def resolve_invoice(
    invoice_id: int,
    body: InvoiceResolveIn,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Close an invoice by hand: settle it, waive it, credit it, or withdraw it.

    All four exist for the same reason, which is why they are one route: a `pending`
    subscription waiting on an invoice nobody will ever pay leaves the merchant stuck
    on their old plan with no in-product way out, and correcting that used to need a
    database session. The activation step and the audit shape are identical, and four
    copies of them would be four places to drift.

    Only `mark-paid` writes `paid_at`. A waiver and a credit are not income, and
    stamping a settlement date on one would make a revenue report count money that
    never arrived.

    `void` is the odd one out and the reason it is not a fourth settlement: it grants
    nothing at all. It withdraws a claim the platform should not have made, so the
    subscription is left exactly as it was and the next sweep is free to bill that
    window again — which is what the partial unique index exists to allow.
    """
    invoice = await session.get(models.PlanInvoice, invoice_id)
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")
    if invoice.status in _RESOLVED_INVOICE_STATUS.values():
        raise HTTPException(
            status_code=409, detail=f"invoice_already_{invoice.status}"
        )

    now = datetime.now(UTC)

    if body.action == "mark-paid":
        # The same settlement the rail runs, so a transfer quoted over the phone leaves the
        # account in exactly the state a QR payment would: unfrozen, with the coverage the
        # invoice bought in force. Splitting this into its own branch is what makes an operator
        # able to fix the one case with no in-product way out — a merchant who has paid by bank
        # transfer and is otherwise stranded, frozen, unable to mint the code they were asked
        # for.
        await billing_svc.settle_invoice(session, invoice, paid_at=now, source="admin")
    elif body.action == "void":
        # No `paid_at`, no `activate_subscription`, no `extend_coverage`: notably unlike the
        # waiver, a void does not buy the period it was raised for. `next_billing_at` is left
        # where it was, so W3 raises the window again on its next sweep — the operator is
        # saying "not this invoice", not "free month".
        #
        # It still lifts the hold. A freeze is keyed on this invoice being unpaid, so voiding it
        # would otherwise strand the merchant frozen with nothing left to pay and no way to
        # unfreeze — the dead end T-23 exists to prevent, reached from the operator side.
        invoice.status = billing_svc.INVOICE_VOID
        invoice.voided_at = now
        invoice.void_reason = billing_svc.VOID_OPERATOR
        invoice.updated_at = now
        billing_svc.lift_billing_hold(await session.get(models.Account, invoice.account_id))
    else:
        # A waiver and a credit forgive the debt without any money arriving: neither is income,
        # so neither writes `paid_at`. Both still resolve the invoice the freeze is keyed on and
        # therefore have to lift it, or an operator forgiving $9.99 would leave the merchant
        # frozen over a debt that no longer exists.
        invoice.status = _RESOLVED_INVOICE_STATUS[body.action]
        invoice.updated_at = now
        if invoice.subscription_id is not None:
            # The same activation the rail path runs, so the coverage the waiver bought is in
            # force. `extend_coverage` is part of that state: without it a waived invoice would
            # leave the subscription with a period end in the past, so the sweep would never
            # renew the merchant the operator just unblocked.
            await billing_svc.activate_subscription(session, invoice.subscription_id)
            await billing_svc.extend_coverage(session, invoice)
        billing_svc.lift_billing_hold(await session.get(models.Account, invoice.account_id))

    details: dict[str, Any] = {
        "resolution": body.action,
        "account_id": invoice.account_id,
        "period_month": invoice.period_month,
        "total_due_cents": invoice.total_due_cents,
        "reason": body.reason,
    }
    if body.action == "credit":
        details["credited_cents"] = (
            invoice.total_due_cents if body.amount_cents is None else body.amount_cents
        )
    audit.record(
        session,
        actor=ctx.account,
        action="invoice.resolved",
        target_type="PlanInvoice",
        target_id=invoice.id,
        details=details,
    )
    await session.commit()
    await session.refresh(invoice)
    return _invoice_row(invoice)


@router.post("/keys/{key_id}/revoke")
async def revoke_account_key(
    key_id: int,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Kill one API key.

    Until this existed the only platform-side way to stop a leaked key was suspending
    the merchant — which takes their store offline and stops every other key with it.
    Revoking the one credential is what the situation calls for.

    Quiet on a repeat: a second revoke changes nothing, so it writes no second audit
    row. The trail should read "this key was revoked", not "revoked five times because
    an operator clicked twice".
    """
    key = await session.get(models.ApiKey, key_id)
    if key is None:
        raise HTTPException(status_code=404, detail="key_not_found")

    if key.status != models.ACCOUNT_ACTIVE:
        return {
            "id": key.id,
            "account_id": key.account_id,
            "status": key.status,
            "revoked_at": key.revoked_at,
            "revoked": False,
        }

    key.status = "revoked"
    key.revoked_at = datetime.now(UTC)
    audit.record(
        session,
        actor=ctx.account,
        action="key.revoked",
        target_type="ApiKey",
        target_id=key.id,
        # The operator's revocation and the merchant's own carry the same action name,
        # because it is the same event; `actor_account_id` is what says who did it.
        details={
            "account_id": key.account_id,
            "name": key.name,
            "key_prefix": key.key_prefix,
        },
    )
    await session.commit()
    await session.refresh(key)
    return {
        "id": key.id,
        "account_id": key.account_id,
        "status": key.status,
        "revoked_at": key.revoked_at,
        "revoked": True,
    }


@router.post("/stores/{store_public_id}/disable")
async def disable_account_store(
    store_public_id: str,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Stop one store taking payments.

    A store's destination being wrong is a reason to stop *that* store, not the whole
    account — and the merchant could already do this to themselves, which meant the
    operator had to ask them to. Disabling is enforced in `create_payment`, so this
    stops new codes immediately; codes already issued are not recalled, and the
    response says so by returning the store rather than claiming a retroactive effect.
    """
    store = (
        await session.execute(
            select(models.Store).where(models.Store.public_id == store_public_id)
        )
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")

    if store.status == models.STORE_DISABLED:
        return {
            "id": store.public_id,
            "account_id": store.account_id,
            "status": store.status,
            "disabled": False,
        }

    store.status = models.STORE_DISABLED
    audit.record(
        session,
        actor=ctx.account,
        action="store.disabled",
        target_type="Store",
        target_id=store.id,
        details={"account_id": store.account_id, "name": store.name},
    )
    await session.commit()
    await session.refresh(store)
    return {
        "id": store.public_id,
        "account_id": store.account_id,
        "status": store.status,
        "disabled": True,
    }


@router.post("/stores/{store_public_id}/enable")
async def enable_account_store(
    store_public_id: str,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Bring back a store an operator disabled.

    Disabling had no counterpart on this side: the console could stop a store
    mid-incident, but the only route back was the merchant-authenticated
    `POST /v1/stores/{public_id}/enable`, so an operator who disabled the wrong
    store — or whose fix the merchant had already made — had to ask the merchant
    to undo it. The status it restores to is derived the same way the merchant
    route derives it: `active` only while the store still has a payment link,
    otherwise `draft`, because reading `active` on a store with no destination
    would advertise a capability `create_payment` then refuses.
    """
    store = (
        await session.execute(
            select(models.Store).where(models.Store.public_id == store_public_id)
        )
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")

    if store.status != models.STORE_DISABLED:
        # Already live. Nothing changed, so nothing is recorded — the same rule the
        # disable route follows, and the same rule `services.stores.enable_store`
        # follows for a store that was never disabled.
        return {
            "id": store.public_id,
            "account_id": store.account_id,
            "status": store.status,
            "enabled": False,
        }

    has_link = (
        await session.execute(
            select(models.PaymentLink.id).where(
                models.PaymentLink.store_id == store.id
            )
        )
    ).scalar_one_or_none()
    restored = models.STORE_ACTIVE if has_link is not None else models.STORE_DRAFT
    store.status = restored
    audit.record(
        session,
        actor=ctx.account,
        action="store.enabled",
        target_type="Store",
        target_id=store.id,
        details={
            "account_id": store.account_id,
            "name": store.name,
            "status": restored,
        },
    )
    await session.commit()
    await session.refresh(store)
    return {
        "id": store.public_id,
        "account_id": store.account_id,
        "status": store.status,
        "enabled": True,
    }


class StoreInternalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_internal: bool
    # Optional, and recorded when given: flagging a store internal is what stops it
    # counting against the owning account's quota and moves its takings out of merchant
    # volume, so a one-line explanation is worth keeping next to the change.
    reason: str | None = Field(default=None, max_length=500)


@router.put("/stores/{store_public_id}/internal")
async def set_account_store_internal(
    store_public_id: str,
    body: StoreInternalIn,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Mark (or unmark) a store as the platform's own.

    An internal store is the platform's own storefront — "ChmabaPay HQ" is the one this
    exists for — and is treated differently from a merchant tenant: it is exempt from
    the account's monthly quota, writes no usage-ledger row, and its takings are
    reported as platform revenue rather than merchant GMV. This route makes the concept
    editable by hand instead of only something the HQ link route sets implicitly.

    Quiet on a repeat: setting the flag to the value it already holds changes nothing,
    so it records nothing — the same rule the disable and enable routes follow.
    """
    store = (
        await session.execute(
            select(models.Store).where(models.Store.public_id == store_public_id)
        )
    ).scalar_one_or_none()
    if store is None:
        raise HTTPException(status_code=404, detail="store_not_found")

    if store.is_internal == body.is_internal:
        return {
            "id": store.public_id,
            "account_id": store.account_id,
            "is_internal": store.is_internal,
            "changed": False,
        }

    previous = store.is_internal
    store.is_internal = body.is_internal
    details: dict[str, Any] = {
        "account_id": store.account_id,
        "name": store.name,
        "from": previous,
        "to": body.is_internal,
    }
    if body.reason:
        details["reason"] = body.reason
    audit.record(
        session,
        actor=ctx.account,
        action="store.internal_changed",
        target_type="Store",
        target_id=store.id,
        details=details,
    )
    await session.commit()
    await session.refresh(store)
    return {
        "id": store.public_id,
        "account_id": store.account_id,
        "is_internal": store.is_internal,
        "changed": True,
    }


# --------------------------------------------------------------------------- #
# Payments
# --------------------------------------------------------------------------- #


class AdminPaymentRowOut(BaseModel):
    id: str
    status: str
    amount_cents: int
    currency: str
    reference_id: str | None
    account_id: int
    account_email: str | None
    store_public_id: str
    store_name: str
    created_at: datetime
    expires_at: datetime
    paid_at: datetime | None
    reversed_at: datetime | None
    # When reconciliation for this payment stopped. Null means we are still watching
    # it, which is the difference between "we looked and found nothing" and "we
    # never looked" — the distinction an operator needs when a customer claims the
    # money left their account.
    detection_closed_at: datetime | None


class AdminPaymentListOut(BaseModel):
    data: list[AdminPaymentRowOut]
    pagination: Pagination


@router.get("/payments", response_model=AdminPaymentListOut)
async def list_all_payments(
    status: str | None = None,
    account_id: int | None = None,
    q: str | None = None,
    attention: Literal["pending_past_expiry", "detection_closed_unpaid"] | None = None,
    page: int = 1,
    per_page: int = 25,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Every payment on the platform, newest first, with its account and store.

    Read-only and unscoped on purpose: the operator's question is "is anything
    wrong anywhere", and until this existed the console could only count payments
    per account — it could not show one. A merchant reporting that a payment
    "never settled" had no in-product answer platform-side.

    `q` matches the payment's public id or the merchant's own `reference_id`,
    which is the string a support thread actually contains. `status` covers the
    states worth hunting for (`pending`, `scanned`, `failed`, `expired`,
    `superseded`, `reversed`), and `account_id` narrows it to one merchant.

    `attention` is the two conditions the overview counts and a bare `status` cannot
    express: `pending` alone includes codes a customer is scanning right now.
    """
    page, per_page, offset = _clamp_paging(page, per_page)

    filters = []
    if status:
        filters.append(models.Payment.status == status)
    if account_id is not None:
        filters.append(models.Store.account_id == account_id)
    if attention == "pending_past_expiry":
        filters.append(models.Payment.status == models.PAYMENT_PENDING)
        filters.append(models.Payment.expires_at < datetime.now(UTC))
    elif attention == "detection_closed_unpaid":
        filters.append(models.Payment.detection_closed_at.is_not(None))
        filters.append(
            models.Payment.status.not_in(
                [models.PAYMENT_PAID, models.PAYMENT_REVERSED]
            )
        )
    if q:
        needle = f"%{q.strip()}%"
        filters.append(
            or_(
                models.Payment.public_id.ilike(needle),
                models.Payment.reference_id.ilike(needle),
            )
        )

    total_rows = (
        await session.execute(
            select(func.count(models.Payment.id))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(*filters)
        )
    ).scalar_one() or 0

    rows = (
        await session.execute(
            select(models.Payment, models.Store, models.Account)
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .join(models.Account, models.Account.id == models.Store.account_id)
            .where(*filters)
            # By `id`, not `created_at`: ids are monotonic and created_at is not
            # guaranteed distinct, so a tie would let a row appear on two pages.
            .order_by(models.Payment.id.desc())
            .limit(per_page)
            .offset(offset)
        )
    ).all()

    return AdminPaymentListOut(
        data=[
            AdminPaymentRowOut(
                id=payment.public_id,
                status=payment.status,
                amount_cents=payment.amount_cents,
                currency=payment.currency,
                reference_id=payment.reference_id,
                account_id=account.id,
                account_email=account.email,
                store_public_id=store.public_id,
                store_name=store.name,
                created_at=payment.created_at,
                expires_at=payment.expires_at,
                paid_at=payment.paid_at,
                reversed_at=payment.reversed_at,
                detection_closed_at=payment.detection_closed_at,
            )
            for payment, store, account in rows
        ],
        pagination=Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ),
    )


# --------------------------------------------------------------------------- #
# Payment detail + dispute resolution
# --------------------------------------------------------------------------- #
# Everything above could *show* a problem. These routes are what let an operator
# *fix* one, which is the difference between a console that reports an incident
# and a console that ends it. Before them, a merchant on the line saying "my
# customer paid and it still says pending" had no platform-side remedy at all:
# the reconciler runs on its own schedule, ABA's session cannot be re-queried by
# hand, a settlement the rail confirmed but we mis-recorded could not be
# recorded, and a webhook the merchant's endpoint refused sat failed forever.


class AdminPaymentDeliveryOut(BaseModel):
    id: int
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_response_status: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    updated_at: datetime


class AdminPaymentDetailOut(BaseModel):
    id: str
    status: str
    amount_cents: int
    currency: str
    reference_id: str | None
    bill_number: str
    idempotency_key: str | None
    metadata: dict | None
    account_id: int
    account_email: str | None
    store_public_id: str
    store_name: str
    created_at: datetime
    expires_at: datetime
    scanned_at: datetime | None
    paid_at: datetime | None
    approved_at: datetime | None
    reversed_at: datetime | None
    reversal_reason: str | None
    # Null means we are still watching this payment. The distinction between "we
    # looked and found nothing" and "we never looked" is the one that settles an
    # argument with a customer holding a receipt.
    detection_closed_at: datetime | None
    # The rail's own words, kept whole. `bakong_ref` is ABA's transaction id and
    # is the string support actually asks the customer for; `gateway_status_raw`
    # holds a hosted session's handle, which is the only thing that lets the
    # reconciler ask ABA about this payment at all. The QR is included because a
    # dispute sometimes ends by comparing the customer's screenshot to it.
    bakong_ref: str | None
    gateway_status_raw: dict | None
    qr_md5: str | None
    qr_string: str
    attempt_history: list | None
    # A reissue retires one code and mints another, so the pair is the trail of
    # "which code was the customer actually holding".
    reissued_from: str | None
    superseded_by: str | None
    deliveries: list[AdminPaymentDeliveryOut]


class PaymentReasonIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Mandatory, and long enough to be a sentence fragment at least. This is the
    # only record of *why* money was credited without the rail confirming it, and
    # "ok" answers nothing a month later.
    reason: str = Field(min_length=3, max_length=255)


class PaymentReconcileOut(BaseModel):
    id: str
    # Our status after the attempt — `paid` here is a settlement we now hold.
    status: str
    # What the rail answered this time: PAID / PENDING / FAILED / UNKNOWN. Kept
    # apart from `status` because "the rail said pending" and "we are pending" are
    # different claims, and only the second is ours to make.
    rail_status: str
    source: str | None
    matched_amount: float | None
    transitioned_to_paid: bool
    signals: list[str]
    error: str | None


class PaymentMarkPaidOut(BaseModel):
    id: str
    status: str
    paid_at: datetime | None


class PaymentRedeliverOut(BaseModel):
    id: str
    redelivered: int
    delivery_ids: list[int]


async def _load_admin_payment(
    session: AsyncSession, public_id: str
) -> tuple[models.Payment, models.Store, models.Account]:
    row = (
        await session.execute(
            select(models.Payment, models.Store, models.Account)
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .join(models.Account, models.Account.id == models.Store.account_id)
            .where(models.Payment.public_id == public_id)
        )
    ).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="payment_not_found")
    return row


async def _payment_delivery_rows(
    session: AsyncSession, payment_id: int
) -> list[tuple[models.EventDelivery, models.Event]]:
    rows = await session.execute(
        select(models.EventDelivery, models.Event)
        .join(models.Event, models.Event.id == models.EventDelivery.event_id)
        .where(models.Event.payment_id == payment_id)
        .order_by(models.EventDelivery.id.desc())
    )
    return list(rows.all())


@router.get("/payments/{public_id}", response_model=AdminPaymentDetailOut)
async def get_admin_payment(
    public_id: str,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """One payment in full, including what the rail said and what we tried.

    The list route answers "what is wrong"; this answers "why". A dispute cannot
    be resolved from a status string: the operator needs the rail's transaction
    id to match against the customer's receipt, the raw gateway payload to see
    what ABA last reported, and the delivery rows to tell "the payment never
    settled" apart from "it settled and we failed to tell the merchant".
    """
    payment, store, account = await _load_admin_payment(session, public_id)

    parent_public_id: str | None = None
    if payment.reissued_from_id is not None:
        parent_public_id = (
            await session.execute(
                select(models.Payment.public_id).where(
                    models.Payment.id == payment.reissued_from_id
                )
            )
        ).scalar_one_or_none()

    successor_public_id = (
        await session.execute(
            select(models.Payment.public_id)
            .where(models.Payment.reissued_from_id == payment.id)
            .order_by(models.Payment.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    deliveries = await _payment_delivery_rows(session, payment.id)

    return AdminPaymentDetailOut(
        id=payment.public_id,
        status=payment.status,
        amount_cents=payment.amount_cents,
        currency=payment.currency,
        reference_id=payment.reference_id,
        bill_number=payment.bill_number,
        idempotency_key=payment.idempotency_key,
        metadata=payment.metadata_,
        account_id=account.id,
        account_email=account.email,
        store_public_id=store.public_id,
        store_name=store.name,
        created_at=payment.created_at,
        expires_at=payment.expires_at,
        scanned_at=payment.scanned_at,
        paid_at=payment.paid_at,
        approved_at=payment.approved_at,
        reversed_at=payment.reversed_at,
        reversal_reason=payment.reversal_reason,
        detection_closed_at=payment.detection_closed_at,
        bakong_ref=payment.bakong_ref,
        gateway_status_raw=payment.gateway_status_raw,
        qr_md5=payment.qr_md5,
        qr_string=payment.qr_string,
        attempt_history=payment.attempt_history,
        reissued_from=parent_public_id,
        superseded_by=successor_public_id,
        deliveries=[
            AdminPaymentDeliveryOut(
                id=delivery.id,
                event_id=delivery.event_id,
                event_type=event.type,
                status=delivery.status,
                attempts=delivery.attempts,
                last_response_status=delivery.last_response_status,
                last_error=delivery.last_error,
                next_attempt_at=delivery.next_attempt_at,
                updated_at=delivery.updated_at,
            )
            for delivery, event in deliveries
        ],
    )


@router.post("/payments/{public_id}/reconcile", response_model=PaymentReconcileOut)
async def reconcile_admin_payment(
    public_id: str,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Re-ask the rail about one payment, now, on an operator's command.

    The sweeper already does this on a schedule, so this covers what a schedule
    cannot: a merchant on the line with a customer holding a receipt, or a
    payment whose detection window has already closed and which the sweeper has
    therefore stopped watching. It is a read of the rail plus the very same
    `mark_paid` path a poll takes, so it can credit money only when the rail
    says the money moved — it cannot invent a settlement, and it is not a
    substitute for `mark-paid`.
    """
    payment, store, account = await _load_admin_payment(session, public_id)
    if payment.status == models.PAYMENT_PAID:
        # Nothing to reconcile: the settlement is already recorded. Re-asking
        # would spend an ABA call on a question we hold the answer to.
        return PaymentReconcileOut(
            id=payment.public_id,
            status=payment.status,
            rail_status="PAID",
            source=None,
            matched_amount=None,
            transitioned_to_paid=False,
            signals=["already_paid"],
            error=None,
        )

    result = await reconcile_payment(
        payment, bakong_client=get_bakong_client(), session=session
    )
    audit.record(
        session,
        actor=ctx.account,
        action="admin.payment_reconciled",
        target_type="Payment",
        target_id=payment.id,
        details={
            "rail_status": result.status,
            "source": result.source,
            "transitioned_to_paid": result.transitioned_to_paid,
            "error": result.error,
            "account_id": account.id,
            "store_id": store.id,
        },
    )
    await session.commit()
    return PaymentReconcileOut(
        id=payment.public_id,
        status=payment.status,
        rail_status=result.status,
        source=result.source,
        matched_amount=result.matched_amount,
        transitioned_to_paid=result.transitioned_to_paid,
        signals=result.signals,
        error=result.error,
    )


@router.post("/payments/{public_id}/mark-paid", response_model=PaymentMarkPaidOut)
async def mark_admin_payment_paid(
    public_id: str,
    body: PaymentReasonIn,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Record a settlement the platform could not detect for itself.

    The most dangerous button in the console: it credits money with no rail
    confirmation. So it demands a reason, refuses any payment already terminal,
    and leaves an audit row naming the operator and quoting them. It is still
    needed — ABA's hosted status can be unreachable for a payment a customer's
    receipt proves, and a merchant must not be told their sale is unrecoverable
    because our poller had a bad afternoon.

    It settles through `mark_paid`, the same function the reconciler uses, so the
    ledger entry, the plan invoice, the successor retirement and the
    `payment.completed` webhook all happen exactly as they would for a detected
    payment. A manual settlement that skipped any of those would be worse than no
    settlement at all.
    """
    payment, store, account = await _load_admin_payment(session, public_id)
    if payment.status == models.PAYMENT_PAID:
        raise HTTPException(status_code=409, detail="payment_already_paid")
    if payment.status in (models.PAYMENT_FAILED, models.PAYMENT_REVERSED):
        # Both are terminal and both mean the same thing here: the rail's record
        # contradicts this credit. A refunded sale cannot be un-refunded by a
        # button, and a failed one needs a fresh QR, not a status edit.
        raise HTTPException(status_code=409, detail=f"payment_is_{payment.status}")

    reason = body.reason.strip()
    # `mark_paid` writes `gateway_status_raw` wholesale, and for a hosted payment
    # that column holds the ABA session handle. Replacing it would erase the
    # evidence a later reconciliation needs to agree with this decision, so the
    # manual override is merged in instead.
    raw = (
        dict(payment.gateway_status_raw)
        if isinstance(payment.gateway_status_raw, dict)
        else {}
    )
    raw["manual_mark_paid"] = {
        "by": ctx.account.email,
        "reason": reason,
        "at": datetime.now(UTC).isoformat(),
    }

    settled = await mark_paid(
        session,
        payment.public_id,
        bakong_ref=payment.bakong_ref,
        gateway_raw=raw,
    )
    if settled is None:
        # Lost a race to a terminal state between the load above and here.
        raise HTTPException(status_code=409, detail="payment_not_markable")

    audit.record(
        session,
        actor=ctx.account,
        action="admin.payment_mark_paid",
        target_type="Payment",
        target_id=payment.id,
        details={
            "reason": reason,
            "amount_cents": payment.amount_cents,
            "account_id": account.id,
            "store_id": store.id,
        },
    )
    await session.commit()
    return PaymentMarkPaidOut(
        id=payment.public_id, status=payment.status, paid_at=payment.paid_at
    )


@router.post("/payments/{public_id}/redeliver", response_model=PaymentRedeliverOut)
async def redeliver_admin_payment_events(
    public_id: str,
    include_successes: bool = False,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Queue this payment's webhook deliveries again.

    A delivery that exhausted `webhook_max_attempts` is terminal, and a merchant
    who fixes their endpoint should not have to wait for the next sale to find
    out whether the fix worked.

    By default only deliveries that have **not** already succeeded are reset —
    failed, retrying, or still queued. Re-sending a `payment.completed` that the
    merchant already processed can double-process the sale on their side, so
    re-sending a success is a deliberate choice: the caller asks for it with
    `include_successes=true`. The operator's console makes that an explicit
    opt-in rather than the default.

    Delivery is picked up by the sender's own scan on its next pass, so this
    writes state and returns; there is no queue to enqueue into. If every
    delivery already succeeded and successes were not included, nothing changes
    and nothing is recorded.
    """
    payment, store, account = await _load_admin_payment(session, public_id)
    rows = await _payment_delivery_rows(session, payment.id)
    if not rows:
        raise HTTPException(status_code=404, detail="no_deliveries_for_payment")

    targets = [
        row
        for row in rows
        if include_successes or row[0].status != models.DELIVERY_SUCCESS
    ]

    now = datetime.now(UTC)
    delivery_ids: list[int] = []
    for delivery, _event in targets:
        delivery.status = models.DELIVERY_RETRYING
        # The attempt budget is reset too: this is a fresh, deliberate send, not
        # the tail of the one that already exhausted itself.
        delivery.attempts = 0
        delivery.next_attempt_at = now
        delivery.last_error = None
        delivery_ids.append(delivery.id)

    if delivery_ids:
        audit.record(
            session,
            actor=ctx.account,
            action="admin.payment_redelivered",
            target_type="Payment",
            target_id=payment.id,
            details={
                "deliveries": len(delivery_ids),
                "delivery_ids": delivery_ids,
                "include_successes": include_successes,
                "account_id": account.id,
            },
        )
        await session.commit()
    return PaymentRedeliverOut(
        id=payment.public_id, redelivered=len(delivery_ids), delivery_ids=delivery_ids
    )


# --------------------------------------------------------------------------- #
# Webhook deliveries
# --------------------------------------------------------------------------- #


class AdminDeliveryRowOut(BaseModel):
    id: int
    event_id: str
    event_type: str
    status: str
    attempts: int
    last_response_status: int | None
    last_error: str | None
    next_attempt_at: datetime | None
    account_id: int
    account_email: str | None
    endpoint_id: int
    endpoint_url: str
    created_at: datetime
    updated_at: datetime


class AdminDeliveryListOut(BaseModel):
    data: list[AdminDeliveryRowOut]
    pagination: Pagination


@router.get("/deliveries", response_model=AdminDeliveryListOut)
async def list_all_deliveries(
    status: str | None = None,
    endpoint_id: int | None = None,
    account_id: int | None = None,
    since_hours: int | None = None,
    page: int = 1,
    per_page: int = 25,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Every webhook delivery attempt on the platform, newest first.

    The merchant-facing route (`GET /v1/webhooks/{id}/deliveries`) answers "did
    *my* endpoint receive it", and only for an endpoint the caller owns. Nobody
    could answer "is the webhook rail delivering at all", which is the question
    that matters when three merchants report silence at once and each of them
    reasonably suspects their own integration.

    `status` filters on `pending`/`retrying`/`success`/`failed`; a pile of
    `retrying` rows with `next_attempt_at` far in the past is the signature of a
    stalled sender. `last_error` is carried through because the transport-level
    reason — DNS, TLS, a refused connection — is what tells an operator whether
    the destination or our sender is at fault.

    `since_hours` bounds the feed to rows touched in the last N hours, so the
    overview's "Webhooks failed (24h)" card can link to exactly the rows it
    counted rather than every failure the platform has ever seen.
    """
    page, per_page, offset = _clamp_paging(page, per_page)

    filters = []
    if status:
        filters.append(models.EventDelivery.status == status)
    if endpoint_id is not None:
        filters.append(models.EventDelivery.endpoint_id == endpoint_id)
    if account_id is not None:
        filters.append(models.WebhookEndpoint.account_id == account_id)
    if since_hours is not None and since_hours > 0:
        filters.append(
            models.EventDelivery.updated_at >= datetime.now(UTC) - timedelta(hours=since_hours)
        )

    total_rows = (
        await session.execute(
            select(func.count(models.EventDelivery.id))
            .join(
                models.WebhookEndpoint,
                models.WebhookEndpoint.id == models.EventDelivery.endpoint_id,
            )
            .where(*filters)
        )
    ).scalar_one() or 0

    rows = (
        await session.execute(
            select(
                models.EventDelivery, models.Event, models.WebhookEndpoint, models.Account
            )
            .join(models.Event, models.Event.id == models.EventDelivery.event_id)
            .join(
                models.WebhookEndpoint,
                models.WebhookEndpoint.id == models.EventDelivery.endpoint_id,
            )
            .join(models.Account, models.Account.id == models.WebhookEndpoint.account_id)
            .where(*filters)
            .order_by(models.EventDelivery.id.desc())
            .limit(per_page)
            .offset(offset)
        )
    ).all()

    return AdminDeliveryListOut(
        data=[
            AdminDeliveryRowOut(
                id=delivery.id,
                event_id=delivery.event_id,
                event_type=event.type,
                status=delivery.status,
                attempts=delivery.attempts,
                last_response_status=delivery.last_response_status,
                last_error=delivery.last_error,
                next_attempt_at=delivery.next_attempt_at,
                account_id=account.id,
                account_email=account.email,
                endpoint_id=endpoint.id,
                endpoint_url=endpoint.url,
                created_at=delivery.created_at,
                updated_at=delivery.updated_at,
            )
            for delivery, event, endpoint, account in rows
        ],
        pagination=Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ),
    )


class AdminDeliveryRetryOut(BaseModel):
    id: int
    status: str
    attempts: int
    next_attempt_at: datetime | None
    retried: bool


@router.post("/deliveries/{delivery_id}/retry", response_model=AdminDeliveryRetryOut)
async def retry_delivery(
    delivery_id: int,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Send one webhook delivery again, now.

    Row-level rather than payment-level, because the operator's question is usually
    narrower than "send this merchant everything": one endpoint was down for ten
    minutes, or one event never arrived, and the rest of the account is fine. It is
    also the honest tool after the failure-rate alert fires — the list shows which
    deliveries failed, and this is the button on that row.

    Resets the attempt budget as well as the clock: a delivery that exhausted
    `webhook_max_attempts` is terminal, and the merchant who just fixed their
    endpoint should get a real attempt rather than one that is refused as spent.

    Quiet on a repeat, like every other operator action here: a delivery already due
    now with a fresh budget has nothing to change, so it writes no second audit row.
    """
    delivery = await session.get(models.EventDelivery, delivery_id)
    if delivery is None:
        raise HTTPException(status_code=404, detail="delivery_not_found")

    now = datetime.now(UTC)
    already_due = (
        delivery.status == models.DELIVERY_RETRYING
        and delivery.attempts == 0
        and delivery.next_attempt_at is not None
        and delivery.next_attempt_at <= now
    )
    if already_due:
        return AdminDeliveryRetryOut(
            id=delivery.id,
            status=delivery.status,
            attempts=delivery.attempts,
            next_attempt_at=delivery.next_attempt_at,
            retried=False,
        )

    previous = delivery.status
    delivery.status = models.DELIVERY_RETRYING
    delivery.attempts = 0
    delivery.next_attempt_at = now
    delivery.last_error = None
    delivery.updated_at = now

    audit.record(
        session,
        actor=ctx.account,
        action="admin.delivery_retried",
        target_type="EventDelivery",
        target_id=delivery.id,
        details={
            "event_id": delivery.event_id,
            "endpoint_id": delivery.endpoint_id,
            "from_status": previous,
        },
    )
    await session.commit()
    await session.refresh(delivery)
    return AdminDeliveryRetryOut(
        id=delivery.id,
        status=delivery.status,
        attempts=delivery.attempts,
        next_attempt_at=delivery.next_attempt_at,
        retried=True,
    )


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #
# The stat cards count what the platform *has*. "Needs attention" counts what is
# *stuck*, which is a different question and was previously answerable only by
# reading the payments list and the deliveries list by eye. Every number here is a
# thing an operator can act on, and each one carries the filter that shows the rows
# behind it — a count with no way to reach the rows is a number, not a signal.


@router.get("/overview")
async def admin_overview(
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(UTC)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    accounts_total = (
        await session.execute(select(func.count(models.Account.id)))
    ).scalar_one() or 0
    stores_total = (
        await session.execute(select(func.count(models.Store.id)))
    ).scalar_one() or 0
    stores_active = (
        await session.execute(
            select(func.count(models.Store.id)).where(
                models.Store.status == models.STORE_ACTIVE
            )
        )
    ).scalar_one() or 0
    payments_paid = (
        await session.execute(
            select(func.count(models.Payment.id)).where(
                models.Payment.status == models.PAYMENT_PAID
            )
        )
    ).scalar_one() or 0
    payments_paid_this_month = (
        await session.execute(
            select(func.count(models.Payment.id)).where(
                models.Payment.status == models.PAYMENT_PAID,
                models.Payment.paid_at >= month_start,
            )
        )
    ).scalar_one() or 0
    mrr_cents = (
        await session.execute(
            select(func.coalesce(func.sum(models.Plan.monthly_fee_cents), 0))
            .join(
                models.PlanSubscription,
                models.PlanSubscription.plan_id == models.Plan.id,
            )
            .where(models.PlanSubscription.status == "active")
        )
    ).scalar_one()

    # Merchant volume excludes the platform's own internal stores. Without the join an
    # operator reading "Paid today" would see the plan fees merchants paid *us* reported
    # as though they were merchant GMV; that money is broken out below instead.
    paid_today_count = (
        await session.execute(
            select(func.count(models.Payment.id))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(False),
                models.Payment.status == models.PAYMENT_PAID,
                models.Payment.paid_at >= today_start,
            )
        )
    ).scalar_one() or 0
    paid_today_cents = (
        await session.execute(
            select(func.coalesce(func.sum(models.Payment.amount_cents), 0))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(False),
                models.Payment.status == models.PAYMENT_PAID,
                models.Payment.paid_at >= today_start,
            )
        )
    ).scalar_one()
    # The platform's own revenue, from internal stores only — plan fees collected into
    # "ChmabaPay HQ". Reported separately rather than summed into merchant volume, which
    # is the whole reason internal stores exist as a category.
    platform_revenue_today_cents = (
        await session.execute(
            select(func.coalesce(func.sum(models.Payment.amount_cents), 0))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(True),
                models.Payment.status == models.PAYMENT_PAID,
                models.Payment.paid_at >= today_start,
            )
        )
    ).scalar_one()
    platform_revenue_this_month_cents = (
        await session.execute(
            select(func.coalesce(func.sum(models.Payment.amount_cents), 0))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(True),
                models.Payment.status == models.PAYMENT_PAID,
                models.Payment.paid_at >= month_start,
            )
        )
    ).scalar_one()

    attention = await _needs_attention(session, now=now)
    ops = await _worker_signals()
    # The stale-queue verdict is a Redis read, so it joins the list here rather than in
    # `_needs_attention`, which is a database question. It is still one list for the
    # console: "needs attention" should not depend on which store answered.
    if ops["stale_queues"] is not None:
        stale = ops["stale_queues"]
        attention.append(
            {
                "key": "stale_worker_queues",
                "label": "Worker queues not draining",
                "detail": (
                    "No drain in the last "
                    f"{DEFAULT_MAX_AGE_SECONDS:.0f}s"
                    + (f" on {', '.join(stale)}" if stale else "")
                    + ". Detection and webhooks stop while the API keeps answering."
                ),
                "count": len(stale),
                "severity": "critical",
                # A critical item with no destination is the one alert an operator
                # cannot act on. The queue names are in `detail` above; the link
                # lands on the deliveries feed, where a stalled sender shows up as
                # retrying rows that are overdue.
                "href": "/deliveries",
            }
        )

    return {
        "accounts_total": accounts_total,
        "stores_total": stores_total,
        "stores_active": stores_active,
        "payments_paid_total": payments_paid,
        "payments_paid_this_month": payments_paid_this_month,
        "mrr_cents": int(mrr_cents or 0),
        "paid_today_count": paid_today_count,
        "paid_today_cents": int(paid_today_cents or 0),
        "platform_revenue_today_cents": int(platform_revenue_today_cents or 0),
        "platform_revenue_this_month_cents": int(
            platform_revenue_this_month_cents or 0
        ),
        "needs_attention": attention,
        "ops": ops,
    }


async def _needs_attention(
    session: AsyncSession, *, now: datetime
) -> list[dict[str, Any]]:
    """What is stuck, with the filter that shows the rows behind each count.

    `severity` is the console's business, not the query's: `critical` means money or
    detection is affected right now, `warn` means a merchant is waiting on a human.
    """
    pending_past_expiry = (
        await session.execute(
            select(func.count(models.Payment.id)).where(
                models.Payment.status == models.PAYMENT_PENDING,
                models.Payment.expires_at < now,
            )
        )
    ).scalar_one() or 0

    # Stopped watching and never paid. Excludes refunds: a reversed payment also has
    # its detection closed, and the money did arrive — it came back.
    detection_closed_unpaid = (
        await session.execute(
            select(func.count(models.Payment.id)).where(
                models.Payment.detection_closed_at.is_not(None),
                models.Payment.status.not_in(
                    [models.PAYMENT_PAID, models.PAYMENT_REVERSED]
                ),
            )
        )
    ).scalar_one() or 0

    deliveries_failed_24h = (
        await session.execute(
            select(func.count(models.EventDelivery.id)).where(
                models.EventDelivery.status == models.DELIVERY_FAILED,
                models.EventDelivery.updated_at >= now - timedelta(hours=24),
            )
        )
    ).scalar_one() or 0

    return [
        {
            "key": "pending_past_expiry",
            "label": "Pending past expiry",
            "detail": (
                "The code's window closed but the payment is still pending. Either "
                "the customer paid and we did not notice, or the expiry sweep missed it."
            ),
            "count": pending_past_expiry,
            "severity": "critical",
            "href": "/payments?attention=pending_past_expiry",
        },
        {
            "key": "detection_closed_unpaid",
            "label": "Stopped watching, never paid",
            "detail": (
                "We closed detection on these and the money never arrived. This is "
                "the row behind \"my customer paid and it still says pending\"."
            ),
            "count": detection_closed_unpaid,
            "severity": "warn",
            "href": "/payments?attention=detection_closed_unpaid",
        },
        {
            "key": "deliveries_failed_24h",
            "label": "Webhooks failed (24h)",
            "detail": (
                "Merchant endpoints that gave up in the last day. A merchant whose "
                "endpoint is failing sees a payment that never reaches their system."
            ),
            "count": deliveries_failed_24h,
            "severity": "warn",
            # The window is part of the filter: without it the link showed every
            # failed delivery the platform had ever recorded, so the rows did not
            # add up to the number the operator clicked.
            "href": "/deliveries?status=failed&since_hours=24",
        },
    ]


async def _worker_signals() -> dict[str, Any]:
    """Queue depth and worker heartbeat age, when the deployment can report them.

    Both live in Redis and only mean anything under `WORKER_TRANSPORT=redis`. With the
    in-process transport the API *is* the worker, so there is no cross-process stamp to
    read and no shared backlog to measure; reporting zeros there would turn "not
    measurable" into "all clear", which is the one wrong answer. `watched: false` says
    so instead.

    Failure to reach Redis is reported as an error string rather than raised: an
    operator opening the console because something is wrong should still get the
    payment counters.
    """
    settings = get_settings()
    signals: dict[str, Any] = {
        "transport": settings.worker_transport,
        "watched": False,
        "heartbeat_ages": None,
        "stale_queues": None,
        "queue_depth": None,
        "error": None,
    }
    if (settings.worker_transport or "").strip().lower() != "redis":
        return signals

    queues = list(worker_registry())
    try:
        ages = await queue_heartbeat_ages(queues=queues, url=settings.redis_url)
        signals["heartbeat_ages"] = ages
        signals["stale_queues"] = [
            queue
            for queue, age in ages.items()
            if age is None or age > DEFAULT_MAX_AGE_SECONDS
        ]
    except Exception as exc:  # noqa: BLE001 - reported to the operator, not raised
        signals["error"] = f"heartbeats unavailable: {exc}"

    # A separate read because it is a separate store question: the stamps come from
    # `GET`, the backlog from the transport's own counters.
    transport = None
    try:
        transport = build_transport(settings)
        metrics = await transport.metrics()
        signals["queue_depth"] = {
            queue: int(row.get(JobStatus.PENDING, 0))
            for queue, row in metrics.items()
        }
        signals["watched"] = signals["error"] is None
    except Exception as exc:  # noqa: BLE001 - reported to the operator, not raised
        signals["error"] = f"queue depth unavailable: {exc}"
    finally:
        if transport is not None:
            close = getattr(transport, "aclose", None)
            if close is not None:
                await close()

    return signals


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #


def _audit_row(entry: models.AuditLog, actor_email: str | None) -> dict[str, Any]:
    return {
        "id": entry.id,
        "action": entry.action,
        "target_type": entry.target_type,
        "target_id": entry.target_id,
        "actor_account_id": entry.actor_account_id,
        "actor_email": actor_email,
        "details": entry.details,
        "created_at": entry.created_at,
    }


@router.get("/audit-logs")
async def list_audit_logs(
    action: str | None = None,
    target_type: str | None = None,
    actor_account_id: int | None = None,
    page: int = 1,
    per_page: int = 50,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Every privileged mutation, newest first, with the account that made it.

    Operator-only, and deliberately not scoped to one account: the trail spans the
    platform, which is the whole point of keeping it. `actor_email` is resolved
    here so an operator can read a row without a second lookup.
    """
    page, per_page, offset = _clamp_paging(page, per_page)

    filters = []
    if action:
        filters.append(models.AuditLog.action == action)
    if target_type:
        filters.append(models.AuditLog.target_type == target_type)
    if actor_account_id is not None:
        filters.append(models.AuditLog.actor_account_id == actor_account_id)

    total_rows = (
        await session.execute(
            select(func.count(models.AuditLog.id)).where(*filters)
        )
    ).scalar_one() or 0

    rows = (
        await session.execute(
            select(models.AuditLog, models.Account.email)
            # Outer join: an action taken by an account that no longer exists is
            # exactly the kind of row an audit trail must not drop.
            .outerjoin(
                models.Account, models.Account.id == models.AuditLog.actor_account_id
            )
            .where(*filters)
            .order_by(models.AuditLog.created_at.desc(), models.AuditLog.id.desc())
            .limit(per_page)
            .offset(offset)
        )
    ).all()

    return {
        "data": [_audit_row(entry, email) for entry, email in rows],
        "pagination": Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ).model_dump(),
    }


# --------------------------------------------------------------------------- #
# Plan-fee collection — the store ChmabaPay itself is paid into
# --------------------------------------------------------------------------- #
# The platform earns subscription fees, so it needs somewhere to receive them. That
# used to be configuration only: `CHMABAPAY_HQ_PAYWAY_LINK` in `deploy/.env` plus a
# sign-in to seed the store, which meant switching billing on required a shell session
# and a restart. These two routes make it a field in the console instead.


class HqLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_link: str = Field(min_length=6, max_length=500)
    # Optional: derived from the link when omitted, which is the normal case. An
    # operator pasting their own PayWay link should not have to dig the merchant
    # account id out of it.
    merchant_account_id: str | None = Field(default=None, max_length=120)
    merchant_name: str | None = Field(default=None, max_length=120)
    # Optional, and recorded when given. This write changes where *all* plan-fee
    # revenue is collected, so a one-line explanation is worth keeping next to the
    # merchant account id it moved.
    reason: str | None = Field(default=None, max_length=500)


class HqStoreOut(BaseModel):
    configured: bool
    # Why the store being reported is the one in use. "env" specifically means
    # `CHMABAPAY_HQ_STORE_ID` is pinning a *different* store than the console saved,
    # which an operator needs to be able to see.
    source: str
    store_public_id: str | None = None
    store_status: str | None = None
    raw_link: str | None = None
    merchant_account_id: str | None = None
    merchant_name: str | None = None


# The PayWay link host, and the shape of a bare slug it resolves to. Checked because
# this field decides where the platform's *own* revenue lands: pasting an ABA account
# number, a Bakong QR or a random URL here would otherwise be accepted, and the mistake
# would only surface later as an invoice whose QR does not mint.
_PAYWAY_LINK_HOST = "payway.com.kh"
_BARE_SLUG = re.compile(r"[A-Za-z0-9._-]{4,64}")


def _looks_like_payway_link(raw: str) -> bool:
    s = raw.strip()
    if not s:
        return False
    if "/" in s:
        return _PAYWAY_LINK_HOST in s.lower()
    return _BARE_SLUG.fullmatch(s) is not None


async def _hq_store_out(session: AsyncSession) -> HqStoreOut:
    resolution = await billing_svc.resolve_hq_store(session)
    store = resolution.store
    if store is None:
        return HqStoreOut(configured=False, source=resolution.source)
    link = await store_svc.load_link(session, store.id)
    return HqStoreOut(
        configured=True,
        source=resolution.source,
        store_public_id=store.public_id,
        store_status=store.status,
        raw_link=link.raw_link if link else None,
        merchant_account_id=link.merchant_account_id if link else None,
        merchant_name=link.merchant_name if link else None,
    )


@router.get("/hq-store", response_model=HqStoreOut)
async def get_hq_store(
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Where subscription fees are collected, and which setting decided it."""
    return await _hq_store_out(session)


async def _hq_store_for_write(
    session: AsyncSession, admin_account: models.Account
) -> models.Store:
    """The HQ store to write to: the one already resolved, else a new one.

    Falling back to creating a store under the calling admin means an operator can
    switch billing on without any environment variable — the console is the only place
    they need to touch. The name is the marker `resolve_hq_store` looks for.
    """
    resolution = await billing_svc.resolve_hq_store(session)
    if resolution.store is not None:
        return resolution.store

    store = models.Store(
        public_id=gen_public_id("st_"),
        account_id=admin_account.id,
        created_via="admin",
        name=billing_svc.HQ_STORE_NAME,
        status=models.STORE_DRAFT,
        city="Phnom Penh",
    )
    session.add(store)
    await session.flush()
    audit.record(
        session,
        actor=admin_account,
        action="hq_store.created",
        target_type="Store",
        target_id=store.id,
        details={"name": store.name},
    )
    return store


@router.put("/hq-store/link", response_model=HqStoreOut)
async def set_hq_store_link(
    body: HqLinkIn,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Point plan-fee collection at an ABA PayWay link.

    The console's equivalent of `CHMABAPAY_HQ_PAYWAY_LINK`, and the supported way to
    switch billing on: no restart, no shell.

    The merchant account id is derived from the link when it is not supplied, which is
    what pasting your own PayWay link should require — an operator should not have to
    dig it out. The host is checked but not reachability: a probe here would put an
    outbound fetch to ABA behind a save button, and a link that is wrong shows up
    immediately as an invoice whose QR will not mint, which is a clearer signal than a
    timeout on this form.
    """
    raw_link = body.raw_link.strip()
    if not _looks_like_payway_link(raw_link):
        raise HTTPException(
            status_code=400,
            detail=(
                "invalid_payway_link: expected an ABA PayWay share link such as "
                "https://link.payway.com.kh/ABAPAYxxxxxxx"
            ),
        )
    merchant_account_id = (body.merchant_account_id or "").strip() or _extract_slug(
        raw_link
    )
    if not merchant_account_id:
        raise HTTPException(status_code=400, detail="invalid_payway_link")

    store = await _hq_store_for_write(session, ctx.account)
    # The store this link points at collects plan fees, which makes it the platform's
    # own store rather than a merchant tenant: internal stores are exempt from the
    # monthly quota and excluded from merchant GMV (see `models.Store.is_internal`).
    store.is_internal = True
    await store_svc.attach_link_to_store(
        session,
        store,
        LinkIn(
            raw_link=raw_link,
            merchant_account_id=merchant_account_id,
            merchant_name=(body.merchant_name or billing_svc.HQ_STORE_NAME).strip(),
        ),
        # Verified without a probe: this is the platform's own link, checked for host
        # and slug shape just above, and an outbound fetch to ABA behind a save button
        # would turn their downtime into ours. See the route docstring.
        verification=models.LINK_VERIFIED,
    )
    store.status = models.STORE_ACTIVE
    details: dict[str, Any] = {
        "merchant_account_id": merchant_account_id,
        "raw_link": raw_link,
        "is_internal": True,
    }
    if body.reason:
        details["reason"] = body.reason
    audit.record(
        session,
        actor=ctx.account,
        action="hq_store.link_set",
        target_type="Store",
        target_id=store.id,
        details=details,
    )
    await session.commit()
    await session.refresh(store)
    return await _hq_store_out(session)

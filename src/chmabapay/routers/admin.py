"""Admin management router with hybrid auth + admin gate."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..auth import KeyContext, resolve_key_context
from ..db import get_session
from .auth import get_current_session_account

router = APIRouter(prefix="/v1/admin", tags=["admin"])

# Pricing-page feature bullets, edited per plan in the console.
FEATURES_MAX = 12
FEATURE_MAX_LEN = 80


@dataclass
class HybridAuthContext:
    account: models.Account
    key_ctx: KeyContext | None
    is_session: bool


async def get_hybrid_admin_context(
    authorization: str | None = Header(default=None),
    session_account: models.Account | None = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
) -> HybridAuthContext:
    if authorization and authorization.startswith("Bearer ck_"):
        key_ctx = await resolve_key_context(session, authorization)
        ctx = HybridAuthContext(
            account=key_ctx.account, key_ctx=key_ctx, is_session=False
        )
    elif session_account is not None:
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
        "account_type": account.account_type,
        "account_type_explicitly_set": account.account_type_explicitly_set,
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
    account_type: str
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
                models.PlanSubscription.status.in_(["trial", "active"]),
            )
        )
    ).all()
    return {aid: (code, name, status) for aid, code, name, status in rows}


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
                account_type=account.account_type,
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
        "stores": [
            {
                "id": store.public_id,
                "name": store.name,
                "external_id": store.external_id,
                "status": store.status,
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
    csv_export_enabled: bool
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
            csv_export_enabled=plan.csv_export_enabled,
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
    csv_export_enabled: bool | None = None
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
    csv_export_enabled: bool = True
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
    invoice: models.PlanInvoice, account_email: str | None = None
) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "account_id": invoice.account_id,
        "account_email": account_email,
        "period_month": invoice.period_month,
        "status": invoice.status,
        "base_fee_cents": invoice.base_fee_cents,
        "usage_payments_count": invoice.usage_payments_count,
        "overage_fee_cents": invoice.overage_fee_cents,
        "total_due_cents": invoice.total_due_cents,
        "paid_at": invoice.paid_at,
        "created_at": invoice.created_at,
    }


@router.get("/invoices")
async def list_all_invoices(
    period_month: str | None = None,
    status: str | None = None,
    page: int = 1,
    per_page: int = 25,
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    """Plan invoices across every account."""
    page, per_page, offset = _clamp_paging(page, per_page)

    filters = []
    if period_month:
        filters.append(models.PlanInvoice.period_month == period_month)
    if status:
        filters.append(models.PlanInvoice.status == status)

    total_rows = (
        await session.execute(
            select(func.count(models.PlanInvoice.id)).where(*filters)
        )
    ).scalar_one() or 0

    rows = (
        await session.execute(
            select(models.PlanInvoice, models.Account.email)
            .join(models.Account, models.Account.id == models.PlanInvoice.account_id)
            .where(*filters)
            .order_by(
                models.PlanInvoice.period_month.desc(), models.PlanInvoice.id.desc()
            )
            .limit(per_page)
            .offset(offset)
        )
    ).all()

    return {
        "data": [_invoice_row(inv, email) for inv, email in rows],
        "pagination": Pagination(
            page=page,
            per_page=per_page,
            total_rows=total_rows,
            total_pages=(total_rows + per_page - 1) // per_page,
        ).model_dump(),
    }


# --------------------------------------------------------------------------- #
# Overview
# --------------------------------------------------------------------------- #


@router.get("/overview")
async def admin_overview(
    ctx: HybridAuthContext = Depends(get_hybrid_admin_context),
    session: AsyncSession = Depends(get_session),
):
    month_start = datetime.now(UTC).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

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

    return {
        "accounts_total": accounts_total,
        "stores_total": stores_total,
        "stores_active": stores_active,
        "payments_paid_total": payments_paid,
        "payments_paid_this_month": payments_paid_this_month,
        "mrr_cents": int(mrr_cents or 0),
    }


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

"""Billing and subscription management router."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..db import get_session
from ..services import payments as svc
from .auth import get_current_session_account

router = APIRouter(prefix="/v1/billing", tags=["billing"])


def _money_str(cents: int) -> str:
    return f"${cents / 100:.2f}"


class PlanOut(BaseModel):
    name: str
    code: str
    monthly_fee_cents: int
    monthly_fee_formatted: str
    base_payments_included: int
    max_stores: int | None
    max_keys_per_account: int
    max_webhooks_per_account: int
    csv_export_enabled: bool
    priority_support: bool
    is_public: bool
    # Pricing-page copy, owned by the admin console.
    tagline: str | None
    features: list[str] | None
    is_featured: bool

    @classmethod
    def from_model(cls, plan: models.Plan) -> PlanOut:
        return cls(
            name=plan.name,
            code=plan.code,
            monthly_fee_cents=plan.monthly_fee_cents,
            monthly_fee_formatted=_money_str(plan.monthly_fee_cents),
            base_payments_included=plan.base_payments_included,
            max_stores=plan.max_stores,
            max_keys_per_account=plan.max_keys_per_account,
            max_webhooks_per_account=plan.max_webhooks_per_account,
            csv_export_enabled=plan.csv_export_enabled,
            priority_support=plan.priority_support,
            is_public=plan.is_public,
            tagline=plan.tagline,
            features=list(plan.features) if plan.features else None,
            is_featured=plan.is_featured,
        )


class ChangePlanIn(BaseModel):
    plan_code: str = Field(min_length=2, max_length=32)


class ChangePlanOut(BaseModel):
    subscription: dict[str, Any]
    account_type: str
    account_type_switched: bool


def _subscription_out(sub: models.PlanSubscription, plan: models.Plan) -> dict[str, Any]:
    return {
        "id": sub.id,
        "plan_id": sub.plan_id,
        "plan_code": plan.code,
        "plan_name": plan.name,
        "status": sub.status,
        "started_at": sub.started_at,
        "canceled_at": sub.canceled_at,
        "next_billing_at": sub.next_billing_at,
        "trial_ends_at": sub.trial_ends_at,
        "created_at": sub.created_at,
        "updated_at": sub.updated_at,
    }


async def _get_active_sub(
    session: AsyncSession, account_id: int
) -> tuple[models.PlanSubscription | None, models.Plan | None]:
    res = await session.execute(
        select(models.PlanSubscription, models.Plan)
        .join(models.Plan, models.Plan.id == models.PlanSubscription.plan_id)
        .where(
            models.PlanSubscription.account_id == account_id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    row = res.one_or_none()
    if row is None:
        return None, None
    return row


class SubscriptionOut(BaseModel):
    subscription: dict[str, Any] | None
    plan: PlanOut | None


@router.get("/subscription", response_model=SubscriptionOut)
async def get_subscription(
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    sub, plan = await _get_active_sub(session, account.id)
    if sub is None or plan is None:
        return SubscriptionOut(subscription=None, plan=None)
    return SubscriptionOut(
        subscription=_subscription_out(sub, plan),
        plan=PlanOut.from_model(plan),
    )


@router.get("/plans", response_model=list[PlanOut])
async def list_plans(
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(models.Plan)
        .where(
            models.Plan.is_public,
            models.Plan.is_active,
        )
        # cheapest first, so the pricing grid reads left to right
        .order_by(models.Plan.monthly_fee_cents, models.Plan.id)
    )
    plans = list(res.scalars().all())
    return [PlanOut.from_model(p) for p in plans]


@router.post("/change-plan", response_model=ChangePlanOut)
async def change_plan(
    body: ChangePlanIn,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(models.Plan).where(models.Plan.code == body.plan_code)
    )
    new_plan = res.scalar_one_or_none()
    if new_plan is None:
        raise HTTPException(status_code=404, detail="plan_not_found")
    if not new_plan.is_public or not new_plan.is_active:
        raise HTTPException(status_code=400, detail="plan_not_available")

    old_sub, old_plan = await _get_active_sub(session, account.id)
    old_code = old_plan.code if old_plan else None

    if old_sub is not None:
        old_sub.status = "canceled"
        old_sub.canceled_at = datetime.now(UTC)
        old_sub.updated_at = datetime.now(UTC)

    now = datetime.now(UTC)
    # Trials are not offered, so a plan change is active the moment it happens.
    new_status = "active"
    trial_ends = None
    next_billing = now + timedelta(days=30)

    new_sub = models.PlanSubscription(
        account_id=account.id,
        plan_id=new_plan.id,
        status=new_status,
        started_at=now,
        next_billing_at=next_billing,
        trial_ends_at=trial_ends,
    )
    session.add(new_sub)
    await session.flush()

    auto_switched = False

    audit.record(
        session,
        actor=account,
        action="plan.changed",
        target_type="Account",
        target_id=account.id,
        details={
            "from_plan": old_code,
            "to_plan": new_plan.code,
            "auto_switched_account_type": auto_switched,
        },
    )
    await session.commit()
    await session.refresh(new_sub)
    await session.refresh(account)

    return ChangePlanOut(
        subscription=_subscription_out(new_sub, new_plan),
        account_type=account.account_type,
        account_type_switched=auto_switched,
    )


async def _get_hq_store(session: AsyncSession) -> models.Store:
    hq_store_id_raw = os.getenv("CHMABAPAY_HQ_STORE_ID")
    if hq_store_id_raw and hq_store_id_raw.strip():
        hq_store_id = hq_store_id_raw.strip()
        stmt = select(models.Store)
        try:
            store_id_int = int(hq_store_id)
            stmt = stmt.where(
                (models.Store.id == store_id_int) | (models.Store.public_id == hq_store_id)
            )
        except ValueError:
            stmt = stmt.where(models.Store.public_id == hq_store_id)
        res = await session.execute(stmt)
        store = res.scalar_one_or_none()
        if store is not None:
            return store

    res = await session.execute(
        select(models.Account).where(models.Account.is_platform_admin.is_(True)).limit(1)
    )
    admin_account = res.scalar_one_or_none()
    if admin_account is not None:
        res = await session.execute(
            select(models.Store).where(
                models.Store.account_id == admin_account.id,
                models.Store.status == models.STORE_ACTIVE,
            )
        )
        stores = list(res.scalars().all())
        if stores:
            return stores[0]

    raise HTTPException(
        status_code=500,
        detail="platform_hq_store_not_configured: ask admin to set CHMABAPAY_HQ_STORE_ID env var.",
    )


def _serialize_invoice(invoice: models.PlanInvoice) -> dict[str, Any]:
    return {
        "id": invoice.id,
        "period_month": invoice.period_month,
        "status": invoice.status,
        "base_fee_cents": invoice.base_fee_cents,
        "base_fee_formatted": _money_str(invoice.base_fee_cents),
        "usage_payments_count": invoice.usage_payments_count,
        "overage_payments_count": invoice.overage_payments_count,
        "overage_fee_cents": invoice.overage_fee_cents,
        "overage_fee_formatted": _money_str(invoice.overage_fee_cents),
        "total_due_cents": invoice.total_due_cents,
        "total_due_formatted": _money_str(invoice.total_due_cents),
        "paid_at": invoice.paid_at,
        "issued_at": getattr(invoice, "issued_at", None),
        "chmabapay_payment_id_ref": (
            str(invoice.chmabapay_payment_id) if invoice.chmabapay_payment_id is not None else None
        ),
    }


@router.get("/invoices")
async def list_invoices(
    period_month: str | None = None,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(models.PlanInvoice)
        .where(models.PlanInvoice.account_id == account.id)
        .order_by(models.PlanInvoice.period_month.desc())
    )
    if period_month:
        stmt = stmt.where(models.PlanInvoice.period_month == period_month)
    res = await session.execute(stmt)
    invoices = list(res.scalars().all())
    return {
        "data": [_serialize_invoice(inv) for inv in invoices],
        "meta": {"filter_period_month": period_month or "all"},
    }


@router.get("/invoices/{invoice_id:int}/khqr", status_code=201)
async def get_invoice_khqr(
    invoice_id: int,
    request: Request,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    res = await session.execute(
        select(models.PlanInvoice).where(
            models.PlanInvoice.id == invoice_id,
            models.PlanInvoice.account_id == account.id,
        )
    )
    invoice = res.scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404, detail="invoice_not_found")

    if invoice.status == "paid":
        raise HTTPException(status_code=400, detail="invoice_already_paid")

    hq_store = await _get_hq_store(session)
    reference_id = f"INV-{invoice.id}-{invoice.period_month}"
    metadata = {"source": "billing_invoice", "invoice_id": invoice.id}

    payment, created = await svc.create_payment(
        session,
        store=hq_store,
        amount_cents=invoice.total_due_cents,
        reference_id=reference_id,
        metadata=metadata,
        idempotency_key=None,
    )

    invoice.chmabapay_payment_id = payment.id
    if invoice.status == "draft":
        invoice.status = "issued"
    if hasattr(invoice, "issued_at") and getattr(invoice, "issued_at", None) is None:
        invoice.issued_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(invoice)

    if request.url.hostname:
        port_part = ""
        if request.url.port not in (80, 443, None):
            port_part = f":{request.url.port}"
        checkout_url = f"{request.url.scheme}://{request.url.hostname}{port_part}/pay/{payment.public_id}"
    else:
        checkout_url = f"/pay/{payment.public_id}"

    return {
        "payment_id": payment.public_id,
        "qr_string": payment.qr_string,
        "checkout_url": checkout_url,
        "expires_at": payment.expires_at,
        "amount_cents": invoice.total_due_cents,
        "amount_formatted": _money_str(invoice.total_due_cents),
    }

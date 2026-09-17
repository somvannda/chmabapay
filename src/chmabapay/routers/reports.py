"""Payment reports router with CSV and JSON exports."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..auth import AuthContext, get_current_auth_context
from ..db import get_session
from ..schemas import money_to_str

router = APIRouter(prefix="/v1/reports", tags=["reports"])


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


async def _ensure_csv_allowed(
    session: AsyncSession, account: models.Account
) -> None:
    plan = await _get_active_plan(session, account.id)
    if plan is None or not plan.csv_export_enabled:
        raise HTTPException(
            status_code=403,
            detail="CSV exports are not available on the Free plan. Upgrade to Starter to unlock.",
        )


def _parse_iso_date_start(date_str: str) -> datetime:
    try:
        d = datetime.fromisoformat(date_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid 'from' date: {date_str}") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _parse_iso_date_end(date_str: str) -> datetime:
    try:
        d = datetime.fromisoformat(date_str)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"Invalid 'to' date: {date_str}") from e
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.replace(hour=23, minute=59, second=59, microsecond=999999)


async def _build_filter_stmt(
    stmt: Any,
    account: models.Account,
    ctx: AuthContext,
    params_dict: dict[str, Any],
    session: AsyncSession,
) -> Any:
    from_dt = params_dict.get("from_dt")
    to_dt = params_dict.get("to_dt")
    store_public_id = params_dict.get("store_public_id")
    merchant_external_id = params_dict.get("merchant_external_id")
    statuses_list = params_dict.get("statuses_list")

    stmt = stmt.join(models.Store, models.Store.id == models.Payment.store_id)
    stmt = stmt.where(models.Store.account_id == account.id)

    if from_dt is not None:
        stmt = stmt.where(models.Payment.created_at >= from_dt)

    if to_dt is not None:
        stmt = stmt.where(models.Payment.created_at <= to_dt)

    if store_public_id is not None:
        store_res = await session.execute(
            select(models.Store).where(
                models.Store.public_id == store_public_id,
                models.Store.account_id == account.id,
            )
        )
        target_store = store_res.scalar_one_or_none()
        if target_store is None:
            raise HTTPException(status_code=404, detail="store_not_found")
        stmt = stmt.where(models.Store.public_id == store_public_id)

    if merchant_external_id is not None:
        stmt = stmt.where(models.Store.external_id == merchant_external_id)

    if statuses_list:
        stmt = stmt.where(models.Payment.status.in_(statuses_list))

    return stmt


def _row_to_dict(
    payment: models.Payment,
    store_public_id: str,
    store_name: str,
    store_external_id: str | None = None,
) -> dict[str, Any]:
    qr_md5_trunc8 = payment.qr_md5[:8] if payment.qr_md5 else ""
    return {
        "public_id": payment.public_id,
        "status": payment.status,
        "amount": money_to_str(payment.amount_cents),
        "currency": payment.currency,
        "reference_id": payment.reference_id or "",
        "store_public_id": store_public_id,
        "store_name": store_name,
        "external_id": store_external_id or "",
        "qr_md5_trunc8": qr_md5_trunc8,
        "paid_at": payment.paid_at.isoformat() if payment.paid_at else "",
        "created_at": payment.created_at.isoformat() if payment.created_at else "",
        "approved_at": payment.approved_at.isoformat() if payment.approved_at else "",
    }


CSV_HEADER = [
    "public_id",
    "status",
    "amount",
    "currency",
    "reference_id",
    "store_public_id",
    "store_name",
    "external_id",
    "qr_md5_trunc8",
    "paid_at",
    "created_at",
    "approved_at",
]


@router.get("/payments.csv")
async def export_payments_csv(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    store_id: str | None = Query(default=None, alias="store_id"),
    merchant: str | None = Query(default=None, alias="merchant"),
    statuses: str | None = Query(default=None, alias="statuses"),
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    await _ensure_csv_allowed(session, ctx.account)

    from_dt = _parse_iso_date_start(from_date) if from_date else None
    to_dt = _parse_iso_date_end(to_date) if to_date else None
    statuses_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else []

    params_dict = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "store_public_id": store_id,
        "merchant_external_id": merchant,
        "statuses_list": statuses_list,
    }

    stmt = select(
        models.Payment,
        models.Store.public_id,
        models.Store.name,
        models.Store.external_id,
    )
    stmt = await _build_filter_stmt(stmt, ctx.account, ctx, params_dict, session)
    stmt = stmt.order_by(models.Payment.created_at.desc())

    result = await session.execute(stmt)
    rows = result.all()

    def _generate():
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(CSV_HEADER)
        yield buf.getvalue()
        buf.seek(0)
        buf.truncate()

        for row in rows:
            payment, store_pub, store_nm, store_external = row
            d = _row_to_dict(payment, store_pub, store_nm, store_external)
            writer.writerow([d[k] for k in CSV_HEADER])
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate()

    return StreamingResponse(
        _generate(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'inline; filename="payments_report.csv"'
        },
    )


@router.get("/payments.json")
async def export_payments_json(
    from_date: str | None = Query(default=None, alias="from"),
    to_date: str | None = Query(default=None, alias="to"),
    store_id: str | None = Query(default=None, alias="store_id"),
    merchant: str | None = Query(default=None, alias="merchant"),
    statuses: str | None = Query(default=None, alias="statuses"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    from_dt = _parse_iso_date_start(from_date) if from_date else None
    to_dt = _parse_iso_date_end(to_date) if to_date else None
    statuses_list = [s.strip() for s in statuses.split(",") if s.strip()] if statuses else []

    params_dict = {
        "from_dt": from_dt,
        "to_dt": to_dt,
        "store_public_id": store_id,
        "merchant_external_id": merchant,
        "statuses_list": statuses_list,
    }

    count_stmt = select(func.count(models.Payment.id))
    count_stmt = await _build_filter_stmt(count_stmt, ctx.account, ctx, params_dict, session)
    total_res = await session.execute(count_stmt)
    total_matching_rows = total_res.scalar_one() or 0

    sum_stmt = select(func.coalesce(func.sum(models.Payment.amount_cents), 0)).where(
        models.Payment.status == "paid"
    )
    sum_params = dict(params_dict)
    sum_params["statuses_list"] = []
    sum_stmt = await _build_filter_stmt(sum_stmt, ctx.account, ctx, sum_params, session)
    sum_res = await session.execute(sum_stmt)
    total_matching_paid_amount_cents = sum_res.scalar_one() or 0

    paid_count_stmt = select(func.count(models.Payment.id)).where(
        models.Payment.status == "paid"
    )
    paid_count_params = dict(params_dict)
    paid_count_params["statuses_list"] = []
    paid_count_stmt = await _build_filter_stmt(paid_count_stmt, ctx.account, ctx, paid_count_params, session)
    paid_count_res = await session.execute(paid_count_stmt)
    total_matching_paid_count = paid_count_res.scalar_one() or 0

    total_pages = (total_matching_rows + per_page - 1) // per_page if total_matching_rows > 0 else 0

    stmt = select(
        models.Payment,
        models.Store.public_id,
        models.Store.name,
        models.Store.external_id,
    )
    stmt = await _build_filter_stmt(stmt, ctx.account, ctx, params_dict, session)
    stmt = stmt.order_by(models.Payment.created_at.desc())
    stmt = stmt.limit(per_page).offset((page - 1) * per_page)

    result = await session.execute(stmt)
    rows = result.all()

    data = []
    for row in rows:
        payment, store_pub, store_nm, store_external = row
        data.append(_row_to_dict(payment, store_pub, store_nm, store_external))

    filters_applied = {
        "from": from_date,
        "to": to_date,
        "store_id": store_id,
        "merchant": merchant,
        "statuses": statuses_list if statuses_list else None,
    }

    return {
        "data": data,
        "summary": {
            "total_matching_rows": total_matching_rows,
            "total_matching_paid_count": total_matching_paid_count,
            "total_matching_paid_amount_cents": total_matching_paid_amount_cents,
            "total_matching_paid_amount_formatted": money_to_str(total_matching_paid_amount_cents),
            "filters_applied": filters_applied,
        },
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_pages": total_pages,
        },
    }

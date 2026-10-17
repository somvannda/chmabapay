"""Public, unauthenticated aggregate endpoints for the marketing site.

Only two kinds of number are allowed out of here, and both are aggregates over
things a visitor could already see for themselves on the pricing page: how many
businesses use the platform, and how much they have processed through it. There
is deliberately **no** revenue figure, MRR, ops signal or per-account detail —
those live behind `routers/admin.py`. An endpoint the internet can call must be
safe to call, so it exposes only what we are willing to put on a billboard.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..config import get_settings
from ..db import get_session

router = APIRouter(prefix="/api/v1/public", tags=["public"])

# How long a CDN / browser may serve a cached answer. The counts move slowly and
# the marketing page is the only caller, so a minute of staleness buys a lot of
# shelter from an endpoint that runs four aggregate queries per hit.
_STATS_CACHE_SECONDS = 60


@router.get("/stats")
async def public_stats(
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Platform totals for the public homepage.

    Every figure counts *merchant* activity only. The platform's own collection
    store (`Store.is_internal`) is excluded from the store count, the transaction
    count and the volume — the money merchants pay us in plan fees is not money
    the platform processed *for* merchants, and summing the two would overstate
    the headline customers read.

    Volume is returned **per currency** rather than as one number. USD and KHR
    settle in different units, so a single summed total would be a meaningless
    addition; the page formats each on its own.
    """
    active_customers = (
        await session.execute(
            select(func.count(models.Account.id)).where(
                models.Account.status == models.ACCOUNT_ACTIVE
            )
        )
    ).scalar_one() or 0

    active_stores = (
        await session.execute(
            select(func.count(models.Store.id)).where(
                models.Store.is_internal.is_(False),
                models.Store.status == models.STORE_ACTIVE,
            )
        )
    ).scalar_one() or 0

    transactions_processed = (
        await session.execute(
            select(func.count(models.Payment.id))
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(False),
                models.Payment.status == models.PAYMENT_PAID,
            )
        )
    ).scalar_one() or 0

    volume_rows = (
        await session.execute(
            select(
                models.Payment.currency,
                func.coalesce(func.sum(models.Payment.amount_cents), 0),
            )
            .join(models.Store, models.Store.id == models.Payment.store_id)
            .where(
                models.Store.is_internal.is_(False),
                models.Payment.status == models.PAYMENT_PAID,
            )
            .group_by(models.Payment.currency)
        )
    ).all()
    # Normalised so a caller always finds the keys it renders, even before the
    # first KHR payment exists. `None` currency (legacy rows) reads as USD, which
    # is the column default and what those rows were written as.
    volumes = {"USD": 0, "KHR": 0}
    for currency, amount in volume_rows:
        volumes[(currency or "USD").upper()] = int(amount or 0)

    response.headers["Cache-Control"] = f"public, max-age={_STATS_CACHE_SECONDS}"

    return {
        "active_customers": int(active_customers),
        "active_stores": int(active_stores),
        "transactions_processed": int(transactions_processed),
        # Minor units (cents / sen), matching `Payment.amount_cents`.
        "volumes": volumes,
        "generated_at": datetime.now(UTC).isoformat(),
        "app_name": get_settings().app_name,
    }

"""Store (merchant) provisioning API.

Any authenticated principal (session user or API key) may manage stores. A POS
platform (e.g. chmaba POS) calls these endpoints to provision a merchant store the
moment one of its end-users supplies their ABA PayWay link.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models, schemas
from ..auth import AuthContext, get_current_auth_context
from ..config import get_settings
from ..db import get_session
from ..services import stores as svc
from ..services import telegram

router = APIRouter(prefix="/v1/stores", tags=["stores"])


async def _require_store_manage(
    ctx: AuthContext = Depends(get_current_auth_context),
) -> AuthContext:
    """Any authenticated principal (session user or any valid API key) may manage
    stores in the single-account model — no `can_manage_stores` flag required."""
    return ctx


class StoreListResponse(BaseModel):
    data: list[schemas.StoreOut]


async def _with_link(session: AsyncSession, store: models.Store) -> schemas.StoreOut:
    link = await svc.load_link(session, store.id)
    return schemas.StoreOut.from_model(store, link)


async def _enforce_max_stores(session: AsyncSession, account: models.Account) -> None:
    res = await session.execute(
        select(models.Plan.max_stores)
        .join(
            models.PlanSubscription,
            models.PlanSubscription.plan_id == models.Plan.id,
        )
        .where(
            models.PlanSubscription.account_id == account.id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
    )
    max_stores = res.scalar_one_or_none()
    if max_stores is None:
        return
    res = await session.execute(
        select(func.count(models.Store.id)).where(
            models.Store.account_id == account.id
        )
    )
    current_count = res.scalar_one() or 0
    if current_count >= max_stores:
        raise HTTPException(
            status_code=400,
            detail=f"Max stores ({max_stores}) reached for your plan. Upgrade to add more.",
        )


@router.post("", status_code=201, response_model=schemas.StoreOut)
async def create_store(
    body: schemas.StoreCreate,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    await _enforce_max_stores(session, ctx.account)
    store = await svc.create_store(session, ctx.account, body)
    return await _with_link(session, store)


@router.get("", response_model=StoreListResponse)
async def list_stores(
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    stores = await svc.list_stores(session, ctx.account)
    return StoreListResponse(
        data=[await _with_link(session, s) for s in stores]
    )


@router.get("/{public_id}", response_model=schemas.StoreOut)
async def get_store(
    public_id: str,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    store = await svc.get_store(session, ctx.account, public_id)
    return await _with_link(session, store)


@router.put("/{public_id}/link", response_model=schemas.StoreOut)
async def set_link(
    public_id: str,
    body: schemas.LinkIn,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    """Attach (or replace) the store's money destination. Store becomes `active`."""
    store = await svc.attach_link(session, ctx.account, public_id, body)
    return await _with_link(session, store)


@router.put("/{public_id}", response_model=schemas.StoreOut)
async def update_store_put(
    public_id: str,
    body: schemas.StorePatch,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    store = await svc.update_store(session, ctx.account, public_id, body)
    return await _with_link(session, store)


@router.patch("/{public_id}", response_model=schemas.StoreOut)
async def update_store_patch(
    public_id: str,
    body: schemas.StorePatch,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    store = await svc.update_store(session, ctx.account, public_id, body)
    return await _with_link(session, store)


@router.post("/{public_id}/disable", response_model=schemas.StoreOut)
async def disable_store(
    public_id: str,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    store = await svc.disable_store(session, ctx.account, public_id)
    return await _with_link(session, store)


class TelegramTestResponse(BaseModel):
    ok: bool = True
    chat_id: str | None


@router.post("/{public_id}/telegram/test", response_model=TelegramTestResponse)
async def telegram_test(
    public_id: str,
    ctx: AuthContext = Depends(_require_store_manage),
    session: AsyncSession = Depends(get_session),
):
    """Send a real test message to the store's configured Telegram chat.

    This is a merchant's proof that alerts reach them, so it must not report a
    success it cannot back. It delivers a message and answers `ok` only once
    Telegram has accepted it; every way of failing is a distinct error rather than
    a log line. It previously logged "Would send Telegram test msg" and returned
    `ok: true` unconditionally, so the merchant was told alerts worked while
    nothing was ever sent.
    """
    store = await svc.get_store(session, ctx.account, public_id)
    if not store.telegram_chat_id:
        raise HTTPException(status_code=400, detail="telegram_chat_id_not_set")
    if not telegram.is_configured():
        raise HTTPException(status_code=503, detail="telegram_not_configured")

    text = (
        f"{get_settings().app_name}: test alert for {store.name}. "
        "Payment alerts for this store will arrive in this chat."
    )
    try:
        await telegram.send_message(store.telegram_chat_id, text)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=502, detail=f"telegram_send_failed: {exc}") from exc

    return TelegramTestResponse(ok=True, chat_id=store.telegram_chat_id)

"""API keys management router with hybrid session + Bearer auth.

Keys are workspace-scoped: one key authenticates every store in the account.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..auth import AuthContext, get_current_auth_context
from ..db import get_session
from ..security import hash_key, new_api_key

router = APIRouter(prefix="/v1/keys", tags=["keys"])


class KeyCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)


class KeyOut(BaseModel):
    id: int
    name: str | None
    key_prefix: str
    mode: str
    status: str
    last_used_at: datetime | None
    created_at: datetime
    revoked_at: datetime | None
    raw_key: str | None = None

    @classmethod
    def from_model(cls, key: models.ApiKey, raw_key: str | None = None) -> KeyOut:
        return cls(
            id=key.id,
            name=key.name,
            key_prefix=key.key_prefix,
            mode=key.mode,
            status=key.status,
            last_used_at=key.last_used_at,
            created_at=key.created_at,
            revoked_at=key.revoked_at,
            raw_key=raw_key,
        )


def _key_list_query(ctx: AuthContext):
    return select(models.ApiKey).where(models.ApiKey.account_id == ctx.account.id)


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


async def _enforce_key_limits(
    session: AsyncSession,
    account: models.Account,
    plan: models.Plan | None,
) -> None:
    """Enforce the plan's account-wide active API-key limit."""
    if plan is None:
        return
    max_keys = getattr(plan, "max_keys_per_account", None)
    if max_keys is None:
        return
    res = await session.execute(
        select(func.count(models.ApiKey.id)).where(
            models.ApiKey.account_id == account.id,
            models.ApiKey.status == models.ACCOUNT_ACTIVE,
        )
    )
    count = res.scalar_one() or 0
    if count >= max_keys:
        raise HTTPException(
            status_code=400,
            detail=f"Max API keys ({max_keys}) reached for your plan. Revoke old keys or upgrade.",
        )


@router.get("", response_model=list[KeyOut])
async def list_keys(
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(ctx).order_by(models.ApiKey.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [KeyOut.from_model(k) for k in rows]


@router.post("", status_code=201, response_model=KeyOut)
async def create_key(
    body: KeyCreate,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    plan = await _get_active_plan(session, ctx.account.id)
    await _enforce_key_limits(session, ctx.account, plan)
    _prefix, raw = new_api_key()
    key = models.ApiKey(
        account_id=ctx.account.id,
        scope=models.KEY_ACCOUNT_SCOPE,
        key_prefix=_prefix,
        name=body.name.strip(),
        key_hash=hash_key(raw),
        mode="live",
    )
    session.add(key)
    await session.flush()
    # Creating a key hands out a credential, so the trail records who asked for
    # it. The raw key is not stored anywhere, including here.
    audit.record(
        session,
        actor=ctx.account,
        action="key.created",
        target_type="ApiKey",
        target_id=key.id,
        details={"name": key.name, "key_prefix": key.key_prefix, "mode": key.mode},
    )
    await session.commit()
    await session.refresh(key)
    return KeyOut.from_model(key, raw_key=raw)


@router.post("/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(
    key_id: int,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(ctx).where(models.ApiKey.id == key_id)
    key = (await session.execute(stmt)).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="key_not_found")
    if key.status != models.ACCOUNT_ACTIVE:
        return KeyOut.from_model(key)
    key.status = "revoked"
    key.revoked_at = datetime.now(UTC)
    key.updated_at = datetime.now(UTC)
    audit.record(
        session,
        actor=ctx.account,
        action="key.revoked",
        target_type="ApiKey",
        target_id=key.id,
        details={"name": key.name, "key_prefix": key.key_prefix},
    )
    await session.commit()
    await session.refresh(key)
    return KeyOut.from_model(key)


@router.post("/{key_id}/rotate", response_model=KeyOut)
async def rotate_key(
    key_id: int,
    ctx: AuthContext = Depends(get_current_auth_context),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(ctx).where(models.ApiKey.id == key_id)
    key = (await session.execute(stmt)).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="key_not_found")
    key.status = "suspended"
    key.revoked_at = datetime.now(UTC)
    new_prefix, new_raw = new_api_key()
    new_key = models.ApiKey(
        account_id=key.account_id,
        scope=models.KEY_ACCOUNT_SCOPE,
        key_prefix=new_prefix,
        name=key.name,
        key_hash=hash_key(new_raw),
        mode="live",
        status=models.ACCOUNT_ACTIVE,
    )
    session.add(new_key)
    await session.flush()
    # Targeted at the replacement, since that is the credential now in use; the
    # id it supersedes is in the details so the lineage is readable.
    audit.record(
        session,
        actor=ctx.account,
        action="key.rotated",
        target_type="ApiKey",
        target_id=new_key.id,
        details={
            "replaces_id": key.id,
            "name": new_key.name,
            "key_prefix": new_key.key_prefix,
        },
    )
    await session.commit()
    await session.refresh(new_key)
    return KeyOut.from_model(new_key, raw_key=new_raw)

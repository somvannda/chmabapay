"""API keys management router.

Session-only. Keys are workspace-scoped — one key authenticates every store in the
account — and minting one is the moment a merchant starts using the service
programmatically, which is what makes this the honest chokepoint for consent.

**A key must not be able to manage keys.** These routes accepted an API key until
2026-09-23. That meant a leaked key could mint itself a replacement and outlive its own
revocation, and could revoke every other key on the account — locking the merchant out
of the automation the leaked key was stolen from. `/v1/me`, `/v1/account` and
`/v1/billing/*` were already session-only for the same class of reason; key management
now matches them, so no credential can extend or destroy itself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..config import get_settings
from ..db import get_session
from ..openapi import AUTH_ERRORS, SESSION_SECURITY
from ..security import hash_key, new_api_key
from .auth import get_current_session_account

router = APIRouter(
    prefix="/v1/keys",
    tags=["keys"],
    dependencies=SESSION_SECURITY,
    responses=AUTH_ERRORS,
)


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


def _key_list_query(account: models.Account):
    return select(models.ApiKey).where(models.ApiKey.account_id == account.id)


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


def _require_terms_accepted(account: models.Account) -> None:
    """Refuse a new integration credential until the merchant agreement is accepted.

    An API key is the point at which a merchant starts using the service
    programmatically, which makes it the honest chokepoint for consent: the
    dashboard gates itself for the same reason, but a UI gate is a convention, and
    without this an account could sign up, mint a key and never see the terms at
    all.

    Checked at *creation*, so an account that is already integrated is never cut
    off mid-flight. The comparison is against the published version rather than
    `terms_accepted_at`, so re-publishing the text asks for consent again instead
    of accepting agreement to a document this merchant never saw.

    Callers that legitimately act before consent — the platform's own operator
    tooling — do not come through here; they hold an admin session, not a key.
    """
    published = get_settings().terms_version
    if account.terms_accepted_version != published:
        raise HTTPException(status_code=403, detail="terms_not_accepted")


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


async def mint_key(
    session: AsyncSession,
    *,
    account: models.Account,
    name: str,
    actor: models.Account,
) -> tuple[models.ApiKey, str]:
    """Create a live key for `account` and return it with its raw value.

    Shared with the operator console, which mints on a merchant's behalf when the
    merchant cannot sign in. The console calls this rather than keeping its own copy, so
    the hash-at-rest rule, the account-wide scope, the display prefix and the audit row
    cannot drift between the two callers.

    The plan's key limit is deliberately **not** enforced here: it is a merchant-facing
    quota, and the caller decides. The merchant route enforces it; the operator route
    does not, because an operator restoring access to a locked-out merchant is not the
    merchant, and the limit would otherwise be a dead end.
    """
    _prefix, raw = new_api_key()
    key = models.ApiKey(
        account_id=account.id,
        scope=models.KEY_ACCOUNT_SCOPE,
        key_prefix=_prefix,
        name=name.strip(),
        key_hash=hash_key(raw),
        mode="live",
    )
    session.add(key)
    await session.flush()
    details: dict[str, Any] = {
        "name": key.name,
        "key_prefix": key.key_prefix,
        "mode": key.mode,
    }
    if actor.id != account.id:
        # One action, two actors. Without both ids in the row, a key minted for a
        # merchant by the platform is indistinguishable from one they created
        # themselves — which is the question asked first when a key leaks.
        details["account_id"] = account.id
        details["via"] = "admin_console"
    # Creating a key hands out a credential, so the trail records who asked for
    # it. The raw key is not stored anywhere, including here.
    audit.record(
        session,
        actor=actor,
        action="key.created",
        target_type="ApiKey",
        target_id=key.id,
        details=details,
    )
    await session.commit()
    await session.refresh(key)
    return key, raw


async def rotate_key_instance(
    session: AsyncSession,
    *,
    key: models.ApiKey,
    actor: models.Account,
) -> tuple[models.ApiKey, str]:
    """Replace `key` with a fresh credential, keeping its name and its lineage.

    The superseded key is left `suspended` rather than `revoked`, which is what tells a
    rotation and a revocation apart in the table; both stop working immediately.
    """
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
    details: dict[str, Any] = {
        "replaces_id": key.id,
        "name": new_key.name,
        "key_prefix": new_key.key_prefix,
    }
    if actor.id != key.account_id:
        details["account_id"] = key.account_id
        details["via"] = "admin_console"
    # Targeted at the replacement, since that is the credential now in use; the
    # id it supersedes is in the details so the lineage is readable.
    audit.record(
        session,
        actor=actor,
        action="key.rotated",
        target_type="ApiKey",
        target_id=new_key.id,
        details=details,
    )
    await session.commit()
    await session.refresh(new_key)
    return new_key, new_raw


@router.get("", response_model=list[KeyOut])
async def list_keys(
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(account).order_by(models.ApiKey.created_at.desc())
    rows = (await session.execute(stmt)).scalars().all()
    return [KeyOut.from_model(k) for k in rows]


@router.post("", status_code=201, response_model=KeyOut)
async def create_key(
    body: KeyCreate,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    _require_terms_accepted(account)
    plan = await _get_active_plan(session, account.id)
    await _enforce_key_limits(session, account, plan)
    key, raw = await mint_key(
        session, account=account, name=body.name, actor=account
    )
    return KeyOut.from_model(key, raw_key=raw)


@router.post("/{key_id}/revoke", response_model=KeyOut)
async def revoke_key(
    key_id: int,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(account).where(models.ApiKey.id == key_id)
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
        actor=account,
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
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    stmt = _key_list_query(account).where(models.ApiKey.id == key_id)
    key = (await session.execute(stmt)).scalar_one_or_none()
    if key is None:
        raise HTTPException(status_code=404, detail="key_not_found")
    new_key, raw = await rotate_key_instance(session, key=key, actor=account)
    return KeyOut.from_model(new_key, raw_key=raw)

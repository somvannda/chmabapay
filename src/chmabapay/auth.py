"""API-key authentication and session/API-key context handling.

Keys are workspace-scoped: one key authenticates every store in the account.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from . import models
from .db import get_session
from .routers.auth import get_current_session_account
from .security import hash_key


@dataclass
class KeyContext:
    api_key: models.ApiKey
    account: models.Account


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=401, detail="unauthorized")


async def resolve_key_context(
    session: AsyncSession,
    authorization: str | None,
) -> KeyContext:
    if not authorization or not authorization.startswith("Bearer "):
        raise _unauthorized()
    raw = authorization[len("Bearer "):].strip()
    if not raw:
        raise _unauthorized()

    res = await session.execute(
        select(models.ApiKey, models.Account)
        .join(models.Account, models.Account.id == models.ApiKey.account_id)
        .where(
            models.ApiKey.key_hash == hash_key(raw),
            models.ApiKey.status == models.ACCOUNT_ACTIVE,
        )
    )
    row = res.one_or_none()
    if row is None:
        raise _unauthorized()
    api_key, account = row

    if account.status != models.ACCOUNT_ACTIVE:
        raise HTTPException(status_code=403, detail="account_suspended")

    api_key.last_used_at = datetime.now(UTC)
    await session.commit()
    return KeyContext(api_key=api_key, account=account)


async def get_key_context(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> KeyContext:
    return await resolve_key_context(session, authorization)


@dataclass
class AuthContext:
    account: models.Account
    key_ctx: KeyContext | None = None
    is_session: bool = False
    api_key_mode: str | None = None

    @property
    def api_key(self) -> models.ApiKey | None:
        return self.key_ctx.api_key if self.key_ctx is not None else None


def _kc_to_ctx(kc: KeyContext) -> AuthContext:
    return AuthContext(
        account=kc.account,
        key_ctx=kc,
        is_session=False,
        api_key_mode=getattr(kc.api_key, "mode", None),
    )


def _session_to_ctx(account: models.Account) -> AuthContext:
    return AuthContext(
        account=account,
        key_ctx=None,
        is_session=True,
        api_key_mode=None,
    )


async def get_current_auth_context(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthContext:
    if authorization and authorization.startswith("Bearer "):
        raw = authorization[len("Bearer "):].strip()
        if raw.startswith("ck_") or raw.startswith("st_") or raw.startswith("sk_"):
            kc = await resolve_key_context(session, authorization)
            return _kc_to_ctx(kc)
    account = await get_current_session_account(request, session)
    return _session_to_ctx(account)


HybridAuthContext = AuthContext


async def get_hybrid_context(
    request: Request,
    authorization: str | None = Header(default=None),
    session_account: object | None = None,
    db_session: AsyncSession = Depends(get_session),
) -> AuthContext:
    return await get_current_auth_context(request, authorization, db_session)

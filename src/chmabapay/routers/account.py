"""Account profile endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..config import get_settings
from ..db import get_session
from .auth import get_current_session_account, session_auth_method

router = APIRouter(prefix="/v1", tags=["account"])


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
        "created_at": account.created_at,
        "updated_at": account.updated_at,
        # Acceptance of the merchant agreement. `terms_required_version` is the
        # text published right now, so a client can tell "accepted" from
        # "accepted something we have since replaced" without hardcoding it.
        "terms_accepted_at": account.terms_accepted_at,
        "terms_accepted_version": account.terms_accepted_version,
        "terms_required_version": get_settings().terms_version,
    }


class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    email: str | None = Field(default=None, max_length=255)
    owner_name: str | None = Field(default=None, max_length=120)
    owner_phone: str | None = Field(default=None, max_length=40)
    account_type: Literal["individual", "business"] | None = None


class TermsAcceptance(BaseModel):
    """The version the caller was shown.

    Required, and checked, on purpose: recording whichever version the *server*
    happens to be publishing would silently attribute a stale page's acceptance
    to the new text. Refusing the mismatch is the whole point of sending it.
    """

    version: str = Field(min_length=1, max_length=32)


@router.get("/me")
async def get_me(
    request: Request,
    account: models.Account = Depends(get_current_session_account),
):
    # auth_method lets a client that must not accept an SSO session (the platform
    # console) tell a password sign-in apart from a Google one.
    return {**_account_profile(account), "auth_method": session_auth_method(request)}


async def _apply_account_patch(
    body: AccountPatch,
    account: models.Account,
    session: AsyncSession,
):
    changed_fields: list[str] = []
    if body.name is not None and body.name != account.name:
        account.name = body.name
        changed_fields.append("name")
    if body.email is not None and body.email != account.email:
        existing = await session.execute(
            select(models.Account).where(models.Account.email == body.email)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=400, detail="email_already_taken")
        account.email = body.email
        changed_fields.append("email")
    if body.account_type is not None and body.account_type != account.account_type:
        account.account_type = body.account_type
        changed_fields.append("account_type")
    if body.account_type is not None and not account.account_type_explicitly_set:
        account.account_type_explicitly_set = True
        changed_fields.append("account_type_explicitly_set")
    if changed_fields:
        account.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(account)
    return _account_profile(account)


@router.patch("/me")
async def patch_me(
    body: AccountPatch,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    return await _apply_account_patch(body, account, session)


@router.patch("/account")
async def patch_account(
    body: AccountPatch,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    return await _apply_account_patch(body, account, session)


@router.post("/me/terms")
async def accept_terms(
    body: TermsAcceptance,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """Record that this merchant accepted the published merchant agreement.

    There is no verification product behind this and no review queue — the
    roadmap is explicit that merchant due diligence is the operator's own duty,
    not a platform feature. What the platform must hold is the *evidence*, which
    is the one part only the platform can hold.

    Re-accepting the same version is a no-op and writes no audit row: a merchant
    who reloads the page has not done anything new, and an audit trail padded
    with duplicate consents is harder to read, not more complete.
    """
    published = get_settings().terms_version
    if body.version != published:
        raise HTTPException(status_code=409, detail="terms_version_superseded")

    if account.terms_accepted_version == published:
        return _account_profile(account)

    account.terms_accepted_version = published
    account.terms_accepted_at = datetime.now(UTC)
    audit.record(
        session,
        actor=account,
        action="account.terms_accepted",
        target_type="Account",
        target_id=account.id,
        details={"version": published},
    )
    await session.commit()
    await session.refresh(account)
    return _account_profile(account)

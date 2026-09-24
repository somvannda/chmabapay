"""Account profile endpoints."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit, models
from ..config import get_settings
from ..db import get_session
from ..openapi import AUTH_ERRORS, SESSION_SECURITY
from ..security import MAX_PASSWORD_BYTES, hash_password, verify_password
from .auth import get_current_session_account, session_auth_method

router = APIRouter(
    prefix="/v1",
    tags=["account"],
    # Session-only: these are the routes a signed-in person drives from the dashboard,
    # and an API key must not be able to change the account's own credentials.
    dependencies=SESSION_SECURITY,
    responses=AUTH_ERRORS,
)


def _account_profile(account: models.Account) -> dict[str, Any]:
    return {
        "id": account.id,
        "email": account.email,
        "name": account.name,
        "status": account.status,
        "whitelabel_enabled": account.whitelabel_enabled,
        "is_platform_admin": account.is_platform_admin,
        # Whether a password exists, never the password. The Security tab needs it to
        # decide between showing a change form and explaining why the account has no
        # password to change.
        "has_password": account.password_hash is not None,
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
    """Fields a merchant may change about their own account.

    Only fields that `_apply_account_patch` actually writes belong here: an
    accepted-but-ignored field is worse than a missing one, because the caller
    gets a 200 and believes the change happened. (`owner_name`/`owner_phone`
    used to sit here for that reason — they are **store** columns, not account
    columns, and nothing ever wrote them.)

    `email` moved out for the same reason, pointed the other way: it *was* written,
    with nothing proving the person asking owned the address. Changing where an
    account's notifications and sign-in magic land is now a verified operation —
    see `POST /me/email` — and `extra="forbid"` makes this schema say so out loud
    instead of quietly ignoring the field.
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)


class EmailChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=255)
    # Required, and checked, for an account that has a password: without it a stolen
    # session is enough to move the account's address to the attacker's inbox, which
    # is how an account gets taken over rather than merely read.
    current_password: str | None = Field(default=None, max_length=200)


class PasswordChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class EraseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Typed confirmation, like the console's manual settlement: the account's own
    # address, echoed back. A button that erases an account on one click is a button
    # a mis-click can erase an account with.
    confirm_email: str = Field(min_length=3, max_length=255)
    # Required when the account has a password, for the same reason as above.
    current_password: str | None = Field(default=None, max_length=200)


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


ERASED_EMAIL_DOMAIN = "chmabapay.invalid"


def _require_ownership(account: models.Account, supplied: str | None) -> None:
    """Prove the caller owns the account, not merely the browser session.

    An account with no password has no second factor to ask for, so the session is
    the only evidence available and the caller is allowed through on it — which is
    exactly why an email change on such an account is refused instead of trusted.
    """
    if account.password_hash is None:
        return
    if not verify_password(supplied or "", account.password_hash):
        raise HTTPException(status_code=401, detail="invalid_password")


@router.post("/me/email")
async def change_email(
    body: EmailChangeIn,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """Move the account's address, with proof that the caller owns the account.

    This used to be a plain `PATCH /v1/me` field: any holder of the session cookie
    could point the account's email at an address of their choosing, which is an
    account takeover rather than a setting — notifications, receipts and any future
    sign-in link would all flow to the attacker.

    There is no email provider behind this platform, so we cannot confirm the new
    address or ask the old one to approve. What we can do is refuse to move it
    without the current password. An account that has no password cannot produce
    that proof, so it is refused and pointed at support rather than waved through.
    """
    if account.password_hash is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "email_change_requires_password: this account signs in with Google, so "
                "there is no password to confirm the change with and no other way for us "
                "to verify the new address. Contact support to move it."
            ),
        )
    _require_ownership(account, body.current_password)

    new_email = body.email.strip().lower()
    if new_email == account.email:
        return _account_profile(account)
    taken = await session.execute(
        select(models.Account).where(models.Account.email == new_email)
    )
    if taken.scalar_one_or_none() is not None:
        raise HTTPException(status_code=400, detail="email_already_taken")

    previous_domain = account.email.rsplit("@", 1)[-1]
    account.email = new_email
    account.updated_at = datetime.now(UTC)
    audit.record(
        session,
        actor=account,
        action="account.email_changed",
        target_type="Account",
        target_id=account.id,
        # Domains, not addresses. The trail has to answer "which provider does this
        # account's mail go to" — that is the signal an operator investigating a
        # takeover needs — while writing the previous address into a log would
        # quietly outlive an erasure request.
        details={
            "domain": new_email.rsplit("@", 1)[-1],
            "previous_domain": previous_domain,
        },
    )
    await session.commit()
    await session.refresh(account)
    return _account_profile(account)


@router.post("/me/password")
async def change_password(
    body: PasswordChangeIn,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """Rotate the account's password.

    Only for accounts that already have one. A Google-created account has no password
    to rotate, and letting a browser session *create* one would mint a long-lived
    credential out of nothing but a cookie — a capability this route deliberately
    does not hand out.
    """
    if account.password_hash is None:
        raise HTTPException(status_code=409, detail="no_password_set")
    _require_ownership(account, body.current_password)
    if verify_password(body.new_password, account.password_hash):
        raise HTTPException(status_code=400, detail="password_unchanged")
    # The schema bounds the password in *characters*, but bcrypt hashes at most 72
    # *bytes* and raises on anything longer. A 73-character ASCII password, or any
    # password whose UTF-8 form passes 72 bytes — 25 Khmer characters is enough —
    # reached `hash_password` and came back as an unhandled 500. Checked in bytes for
    # that reason, and answered with a code rather than a validator message.
    if len(body.new_password.encode("utf-8")) > MAX_PASSWORD_BYTES:
        raise HTTPException(status_code=400, detail="password_too_long")

    account.password_hash = hash_password(body.new_password)
    account.updated_at = datetime.now(UTC)
    audit.record(
        session,
        actor=account,
        action="account.password_changed",
        target_type="Account",
        target_id=account.id,
    )
    await session.commit()
    return {"ok": True}


@router.delete("/me")
async def erase_me(
    body: EraseIn,
    account: models.Account = Depends(get_current_session_account),
    session: AsyncSession = Depends(get_session),
):
    """Close the account and anonymise it.

    What the privacy policy promises: delete or anonymise the account's data on
    request, except where records must be kept for a legal, tax or accounting
    obligation. So the account is anonymised and every credential that could still
    move money is revoked, while the payments themselves stay — they are the
    accounting record of money that really moved, and deleting them would destroy
    the merchant's own books as well as ours.

    Irreversible, and the caller loses access the moment it commits, so it takes a
    typed confirmation and the password. The session ends because the account is
    suspended, which `get_current_session_account` refuses.
    """
    if account.is_platform_admin:
        # The console's own account. Erasing it by accident would leave nobody able
        # to operate the platform, and there is no recovery path that does not
        # involve a database session.
        raise HTTPException(
            status_code=409,
            detail=(
                "platform_admin_cannot_self_delete: this account operates the console. "
                "Grant another platform admin first."
            ),
        )
    if body.confirm_email.strip().lower() != account.email.lower():
        raise HTTPException(status_code=400, detail="confirm_email_does_not_match")
    _require_ownership(account, body.current_password)

    now = datetime.now(UTC)

    stores = list(
        (
            await session.execute(
                select(models.Store).where(models.Store.account_id == account.id)
            )
        )
        .scalars()
        .all()
    )
    for store in stores:
        store.status = models.STORE_DISABLED

    keys = list(
        (
            await session.execute(
                select(models.ApiKey).where(
                    models.ApiKey.account_id == account.id,
                    models.ApiKey.revoked_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for key in keys:
        key.revoked_at = now

    endpoints = list(
        (
            await session.execute(
                select(models.WebhookEndpoint).where(
                    models.WebhookEndpoint.account_id == account.id
                )
            )
        )
        .scalars()
        .all()
    )
    for endpoint in endpoints:
        # Stopped, not deleted: a delivery row pointing at a missing endpoint would
        # turn the sender's next pass into an error instead of a skip.
        endpoint.status = "disabled"

    audit.record(
        session,
        actor=account,
        action="account.erased",
        target_type="Account",
        target_id=account.id,
        details={
            "stores_disabled": len(stores),
            "keys_revoked": len(keys),
            "webhooks_disabled": len(endpoints),
        },
    )

    # Anonymised rather than deleted. The payments and events reference this row and
    # carry the accounting record, so the identity goes and the money trail stays.
    account.email = f"erased-{account.id}@{ERASED_EMAIL_DOMAIN}"
    account.name = "Erased account"
    account.google_sub = None
    account.password_hash = None
    account.whitelabel_enabled = False
    account.status = models.ACCOUNT_SUSPENDED
    account.updated_at = now
    await session.commit()
    return {"ok": True, "email": account.email}


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

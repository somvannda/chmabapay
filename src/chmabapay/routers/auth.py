"""Auth router: Google OAuth2 login + JWT session cookies + dev login."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import models
from ..config import get_settings
from ..db import get_session
from ..security import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
router_v1_alias = APIRouter(prefix="/api/v1/auth", tags=["auth"])
router_user_google = APIRouter(prefix="/user/google/auth", tags=["auth"])

SESSION_COOKIE = "chmabapay_session"


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (4 - len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def _sign_jwt(payload: dict[str, Any], secret: str) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    header_b64 = _b64url_encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b64 = _b64url_encode(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    sig_b64 = _b64url_encode(sig)
    return f"{header_b64}.{payload_b64}.{sig_b64}"


def _verify_jwt(token: str, secret: str) -> dict[str, Any] | None:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        header_b64, payload_b64, sig_b64 = parts
        signing_input = f"{header_b64}.{payload_b64}".encode()
        expected_sig = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)
        if not hmac.compare_digest(expected_sig, actual_sig):
            return None
        payload = json.loads(_b64url_decode(payload_b64))
        exp = payload.get("exp")
        if exp is not None and datetime.now(UTC).timestamp() > exp:
            return None
        return payload
    except Exception:
        return None


def _is_https_request(request: Request) -> bool:
    """Trust X-Forwarded-Proto from Cloudflare / reverse proxies, plus the direct
    scheme of the incoming request. Cloudflare always sets X-Forwarded-Proto=https
    when it terminates TLS on the public edge, even if the origin is plain HTTP."""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")
    if forwarded_proto.lower() == "https":
        return True
    scheme = request.url.scheme or ""
    return scheme.lower() == "https"


def _is_localhost(request: Request) -> bool:
    # If Cloudflare / any proxy forwarded us, treat it as non-localhost (use Secure cookies).
    if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
        return False
    host = request.url.hostname or ""
    return host in ("localhost", "127.0.0.1", "::1")


def _set_session_cookie(
    response: Response,
    token: str,
    request: Request,
    max_age: int,
) -> None:
    secure = _is_https_request(request) or (not _is_localhost(request))
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=max_age,
    )


def _clear_session_cookie(response: Response, request: Request | None = None) -> None:
    secure = False
    if request is not None:
        secure = _is_https_request(request) or (not _is_localhost(request))
    response.set_cookie(
        key=SESSION_COOKIE,
        value="",
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        expires=0,
    )


def _make_session_jwt(account: models.Account, amr: str = "password") -> str:
    """Mint a session token. `amr` records how the user authenticated — some surfaces
    (the platform console) refuse sessions that did not come from a password."""
    settings = get_settings()
    now = datetime.now(UTC)
    iat = int(now.timestamp())
    exp = iat + settings.jwt_ttl_seconds
    payload = {
        "sub": str(account.id),
        "email": account.email,
        "account_type": account.account_type,
        "account_type_explicitly_set": account.account_type_explicitly_set,
        "is_platform_admin": account.is_platform_admin,
        "amr": amr,
        "iat": iat,
        "exp": exp,
    }
    return _sign_jwt(payload, settings.jwt_secret_key)


def session_auth_method(request: Request) -> str:
    """How the current session was created: "password", "google" or "dev".

    Tokens minted before this claim existed report "unknown" so a caller that must not
    accept an SSO session fails closed instead of assuming password."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return "unknown"
    payload = _verify_jwt(token, get_settings().jwt_secret_key)
    if payload is None:
        return "unknown"
    amr = payload.get("amr")
    return amr if isinstance(amr, str) else "unknown"


async def _upsert_account(
    session: AsyncSession,
    *,
    email: str,
    name: str,
    google_sub: str | None = None,
) -> models.Account:
    res = await session.execute(
        select(models.Account).where(models.Account.email == email)
    )
    account = res.scalar_one_or_none()
    if account is None:
        account = models.Account(
            email=email,
            name=name or email,
            google_sub=google_sub,
            account_type="individual",
            account_type_explicitly_set=True,
            whitelabel_enabled=False,
            is_platform_admin=False,
        )
        session.add(account)
        await session.flush()
        await _ensure_free_subscription(session, account)
    else:
        changed = False
        if google_sub is not None and account.google_sub != google_sub:
            account.google_sub = google_sub
            changed = True
        if name and account.name != name:
            account.name = name
            changed = True
        if not account.account_type_explicitly_set:
            account.account_type_explicitly_set = True
            changed = True
        if changed:
            account.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(account)
    return account


async def _ensure_free_subscription(
    session: AsyncSession,
    account: models.Account,
) -> None:
    res = await session.execute(
        select(models.Plan).where(models.Plan.code == "free")
    )
    free = res.scalar_one_or_none()
    if free is None:
        return
    now = datetime.now(UTC)
    next_billing = now + timedelta(days=30)
    sub = models.PlanSubscription(
        account_id=account.id,
        plan_id=free.id,
        status="active",
        next_billing_at=next_billing,
        trial_ends_at=None,
    )
    session.add(sub)


def _admin_emails() -> set[str]:
    """CHMABAPAY_ADMIN_EMAILS as a lowercase set."""
    csv = (get_settings().chmabapay_admin_emails or "").strip().lower()
    return {e.strip() for e in csv.split(",") if e.strip()}


async def _maybe_promote_admin_and_seed_hq(
    session: AsyncSession,
    account: models.Account,
) -> None:
    """If a freshly created account's email is in CHMABAPAY_ADMIN_EMAILS, promote it
    to business + platform admin and seed the HQ store so self-pay billing works.
    Idempotent: safe to call on every Google login for every account."""
    settings = get_settings()
    admin_emails = _admin_emails()
    if not admin_emails:
        return
    if (account.email or "").lower() not in admin_emails:
        return
    changed = False
    if not account.is_platform_admin:
        account.is_platform_admin = True
        changed = True
    if not account.whitelabel_enabled:
        account.whitelabel_enabled = True
        changed = True
    if changed:
        account.updated_at = datetime.now(UTC)
        session.add(account)
    # HQ store seed (once)
    hq_id = settings.chmabapay_hq_store_id
    if hq_id:
        existing = (await session.execute(
            select(models.Store).where(
                (models.Store.account_id == account.id)
                & (models.Store.public_id == hq_id)
            )
        )).scalar_one_or_none()
        if existing is None:
            by_public = (await session.execute(
                select(models.Store).where(models.Store.public_id == hq_id)
            )).scalar_one_or_none()
            if by_public is None:
                store = models.Store(
                    account_id=account.id,
                    public_id=hq_id,
                    name="ChmabaPay HQ",
                    status=models.ACCOUNT_ACTIVE,
                    city="Phnom Penh",
                    owner_name="Platform",
                )
                session.add(store)
                await session.flush()
                hq_link = (settings.chmabapay_hq_payway_link or "").strip()
                if hq_link:
                    from ..services.payway_parser import _extract_slug

                    link = models.PaymentLink(
                        store_id=store.id,
                        link_type=models.LINK_ABA_PAYWAY,
                        raw_link=hq_link,
                        merchant_account_id=_extract_slug(hq_link),
                        merchant_name="ChmabaPay HQ",
                        currency="USD",
                        verification=models.LINK_VERIFIED,
                        status=models.ACCOUNT_ACTIVE,
                    )
                    session.add(link)
                    store.status = models.STORE_ACTIVE
    # Nudge a free-plan admin onto Pro so self-pay billing has a real tier.
    # An account keeps a row per plan change, so this must read the *current*
    # subscription — selecting all of them raised MultipleResultsFound and
    # 500'd the callback for anyone who had ever switched plans.
    plan_row = (await session.execute(
        select(models.PlanSubscription)
        .where(
            models.PlanSubscription.account_id == account.id,
            models.PlanSubscription.status.in_(["trial", "active"]),
        )
        .order_by(models.PlanSubscription.id.desc())
        .limit(1)
    )).scalar_one_or_none()
    if plan_row is not None:
        plan_obj = (await session.execute(
            select(models.Plan).where(models.Plan.id == plan_row.plan_id)
        )).scalar_one_or_none()
        if plan_obj and plan_obj.code == "free":
            better = (await session.execute(
                select(models.Plan).where(models.Plan.code == "pro")
            )).scalar_one_or_none()
            if better:
                plan_row.plan_id = better.id
                plan_row.status = "active"
    # The session dependency does not commit, so persist here or the promotion
    # and HQ store are discarded when the request ends.
    await session.commit()


async def get_current_session_account(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> models.Account:
    """FastAPI dependency: read JWT cookie, verify, load Account. Raises 401 on failure."""
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise HTTPException(status_code=401, detail="invalid_session")
    payload = _verify_jwt(token, settings.jwt_secret_key)
    if payload is None:
        raise HTTPException(status_code=401, detail="invalid_session")
    try:
        account_id = int(payload["sub"])
    except (KeyError, ValueError, TypeError):
        raise HTTPException(status_code=401, detail="invalid_session")
    res = await session.execute(
        select(models.Account).where(models.Account.id == account_id)
    )
    account = res.scalar_one_or_none()
    if account is None or account.status != models.ACCOUNT_ACTIVE:
        raise HTTPException(status_code=401, detail="invalid_session")
    return account


def _resolve_google_redirect_uri(request: Request) -> str:
    """Return the exact redirect_uri to send to Google for both /login (authorize) and
    /callback (token exchange). Google compares this byte-for-byte against the Authorized
    redirect URIs list in GCP Console, so we MUST be deterministic. Priority:

    1. Explicit GOOGLE_REDIRECT_URI env var (canonical, always wins when set).
    2. PUBLIC_ORIGIN env var + suffix /user/google/auth/callback (canonical user path
       matches chmabapay.json live GCP whitelist shape).
    3. X-Forwarded-* headers from Cloudflare so we infer the public URL the user sees:
       scheme=X-Forwarded-Proto, host=X-Forwarded-Host, suffix /user/google/auth/callback.
    4. Request URL directly (last-resort fallback: works when backend is the public origin).
    """
    settings = get_settings()
    if settings.google_redirect_uri:
        return settings.google_redirect_uri
    if settings.public_origin:
        base = settings.public_origin.rstrip("/")
        return f"{base}/user/google/auth/callback"
    forwarded_proto = (request.headers.get("x-forwarded-proto") or "").lower() or None
    forwarded_host = request.headers.get("x-forwarded-host") or None
    if forwarded_proto and forwarded_host:
        return f"{forwarded_proto}://{forwarded_host}/user/google/auth/callback"
    return f"{request.url.scheme}://{request.url.netloc}/user/google/auth/callback"


def _is_safe_next(next_path: str | None) -> bool:
    if next_path is None:
        return False
    if not next_path.startswith("/"):
        return False
    if next_path.startswith("//"):
        return False
    return True


def _encode_google_state(next_path: str | None) -> str:
    state_payload: dict[str, Any] = {"ts": int(datetime.now(UTC).timestamp())}
    if _is_safe_next(next_path):
        state_payload["next"] = next_path
    raw = json.dumps(state_payload, separators=(",", ":")).encode("utf-8")
    return _b64url_encode(raw)


def _decode_google_state(state_param: str | None) -> dict[str, Any]:
    if not state_param:
        return {}
    try:
        raw = _b64url_decode(state_param)
        payload = json.loads(raw)
        if isinstance(payload, dict):
            return payload
        return {}
    except Exception:
        return {}


@router.get("/google/login")
async def google_login(
    request: Request,
    next: str | None = Query(default=None),
) -> RedirectResponse:
    settings = get_settings()
    if not settings.google_client_id:
        raise HTTPException(status_code=400, detail="google_oauth_not_configured")
    redirect_uri = _resolve_google_redirect_uri(request)
    state = _encode_google_state(next)
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "access_type": "offline",
        "state": state,
    }
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return RedirectResponse(url)


@router.get("/google/callback")
async def google_callback(
    request: Request,
    code: str,
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(status_code=400, detail="google_oauth_not_configured")
    redirect_uri = _resolve_google_redirect_uri(request)
    async with httpx.AsyncClient() as client:
        token_res = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail="google_token_exchange_failed")
    token_data = token_res.json()
    id_token = token_data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="google_no_id_token")
    try:
        payload_b64 = id_token.split(".")[1]
        id_payload = json.loads(_b64url_decode(payload_b64))
    except Exception:
        raise HTTPException(status_code=400, detail="google_invalid_id_token")
    email = id_payload.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="google_no_email")
    google_sub = id_payload.get("sub")
    name = id_payload.get("name") or email
    account = await _upsert_account(session, email=email, name=name, google_sub=google_sub)
    await _maybe_promote_admin_and_seed_hq(session, account)
    await session.refresh(account)
    jwt = _make_session_jwt(account, "google")
    state_dict = _decode_google_state(state)
    next_path = state_dict.get("next")
    if _is_safe_next(next_path):
        redirect_url = next_path
    else:
        redirect_url = settings.post_login_redirect_url or "/"
    response = RedirectResponse(url=redirect_url)
    _set_session_cookie(response, jwt, request, settings.jwt_ttl_seconds)
    return response


@router.post("/signout")
async def signout(
    response: Response,
    request: Request,
) -> Any:
    content_type = (request.headers.get("content-type") or "").lower()
    is_form_submit = "application/x-www-form-urlencoded" in content_type
    _clear_session_cookie(response, request)
    if is_form_submit:
        return RedirectResponse(url="/", status_code=303)
    return {"status": "signed_out"}


class PasswordLoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=200)


class PasswordLoginOut(BaseModel):
    email: str
    name: str
    is_platform_admin: bool


@router.post("/login", response_model=PasswordLoginOut)
async def password_login(
    request: Request,
    response: Response,
    body: PasswordLoginIn,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Email + password sign-in, used by the platform admin console.

    Google OAuth is untouched; this only serves accounts that have a password set
    (see `python -m chmabapay.cli set-password`)."""
    settings = get_settings()
    email = body.email.strip().lower()
    res = await session.execute(
        select(models.Account).where(func.lower(models.Account.email) == email)
    )
    account = res.scalar_one_or_none()

    if account is not None and account.password_hash is None:
        # First sign-in for an admin whose password was supplied via env instead of
        # the CLI: adopt it once, then the stored hash is the only source.
        bootstrap = (settings.chmabapay_admin_password or "").strip()
        if bootstrap and email in _admin_emails():
            account.password_hash = hash_password(bootstrap)
            account.updated_at = datetime.now(UTC)
            session.add(account)
            await session.commit()
            await session.refresh(account)

    if account is None or not verify_password(body.password, account.password_hash):
        # One generic failure for unknown account / no password / wrong password so
        # the endpoint cannot be used to enumerate accounts.
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if account.status != models.ACCOUNT_ACTIVE:
        raise HTTPException(status_code=403, detail="account_suspended")

    await _maybe_promote_admin_and_seed_hq(session, account)
    await session.refresh(account)

    _set_session_cookie(
        response, _make_session_jwt(account), request, settings.jwt_ttl_seconds
    )
    return PasswordLoginOut(
        email=account.email,
        name=account.name,
        is_platform_admin=account.is_platform_admin,
    )


@router.get("/api/v1/auth/google/login")
async def google_login_alias_v1(request: Request) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url.replace(path="/user/google/auth/login")), status_code=307
    )


@router.get("/api/v1/auth/google/callback")
async def google_callback_alias_v1(
    request: Request,
    code: str,
    session: AsyncSession = Depends(get_session),
    scope: str | None = None,
    authuser: str | None = None,
    prompt: str | None = None,
) -> Response:
    return RedirectResponse(
        url=str(request.url.replace(path="/user/google/auth/callback")), status_code=307
    )


@router_v1_alias.get("/google/login")
async def v1_login_via_prefix(request: Request) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url.replace(path="/user/google/auth/login")), status_code=307
    )


@router_v1_alias.get("/google/callback")
async def v1_callback_via_prefix(
    request: Request,
) -> RedirectResponse:
    return RedirectResponse(
        url=str(request.url.replace(path="/user/google/auth/callback")), status_code=307
    )


@router_user_google.get("/login")
async def user_google_login(
    request: Request,
    next: str | None = Query(default=None),
) -> RedirectResponse:
    return await google_login(request, next=next)


@router_user_google.get("/callback")
async def user_google_callback(
    request: Request,
    code: str,
    state: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    scope: str | None = None,
    authuser: str | None = None,
    prompt: str | None = None,
) -> Response:
    return await google_callback(request, code, state, session)


settings = get_settings()
if settings.enable_dev_gateway:

    @router.get("/_dev/login")
    async def dev_login_get(
        request: Request,
        email: str = "sokha@example.com",
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        account = await _upsert_account(session, email=email, name=email.split("@")[0])
        # Same promotion the Google callback does, so CHMABAPAY_ADMIN_EMAILS
        # accounts can reach /v1/admin and the admin UI in dev too.
        await _maybe_promote_admin_and_seed_hq(session, account)
        await session.refresh(account)
        jwt = _make_session_jwt(account, "dev")
        response = RedirectResponse(url="/")
        _set_session_cookie(response, jwt, request, settings.jwt_ttl_seconds)
        return response

    @router.post("/_dev/login")
    async def dev_login_post(
        request: Request,
        email: str = Form(default="sokha@example.com"),
        session: AsyncSession = Depends(get_session),
    ) -> Response:
        account = await _upsert_account(session, email=email, name=email.split("@")[0])
        await _maybe_promote_admin_and_seed_hq(session, account)
        await session.refresh(account)
        jwt = _make_session_jwt(account, "dev")
        response = RedirectResponse(url="/", status_code=303)
        _set_session_cookie(response, jwt, request, settings.jwt_ttl_seconds)
        return response

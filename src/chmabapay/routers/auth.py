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

from .. import audit, models
from ..config import get_settings
from ..db import get_session
from ..ratelimit import client_ip
from ..security import hash_password, verify_password
from ..services.billing import HQ_STORE_NAME

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
    rename_existing: bool = True,
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
        if rename_existing and name and account.name != name:
            account.name = name
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


async def _ensure_hq_store(session: AsyncSession, account: models.Account) -> None:
    """Seed the platform's own store, which is where plan fees are collected.

    Two changes from the version this replaces, and both were required for self-pay
    billing to be possible at all in production:

    * It runs for **any** platform admin, not only an address listed in
      `CHMABAPAY_ADMIN_EMAILS`. An operator created by `cli grant-admin` is an admin
      whose address need not be in that list, and the old guard meant the HQ store
      was silently never created for them.
    * `CHMABAPAY_HQ_PAYWAY_LINK` alone is enough. The store id used to be required
      first, and the link was only read *after* a store had been created — so with
      the id unset (as it is in production) a configured link was never even looked
      at. A store id is genuinely optional: without one, the host is generated.

    The link is still required for the store to be usable, because a store with no
    payment link cannot take a payment. One is created anyway when only the id is
    configured, so the operator can attach the link by hand.

    Idempotent and never destructive: an existing store — by configured id, or by
    the name this function creates — is returned untouched, so a link edited in the
    dashboard is not clobbered by a stale environment variable on the next sign-in.
    """
    if not account.is_platform_admin:
        return

    settings = get_settings()
    configured_id = (settings.chmabapay_hq_store_id or "").strip()
    link_url = (settings.chmabapay_hq_payway_link or "").strip()
    if not configured_id and not link_url:
        return

    if configured_id:
        existing = (
            await session.execute(
                select(models.Store).where(models.Store.public_id == configured_id)
            )
        ).scalar_one_or_none()
    else:
        existing = (
            await session.execute(
                select(models.Store).where(
                    models.Store.account_id == account.id,
                    models.Store.name == HQ_STORE_NAME,
                )
            )
        ).scalar_one_or_none()
    if existing is not None:
        # The store being seeded (or already present) is the platform's own, so mark it
        # internal even when it was created before this flag existed. The caller commits.
        existing.is_internal = True
        return

    # Imported here rather than at module scope: `services.payments` pulls in the
    # worker transport, and this module is imported by `main` before the app exists.
    from ..services.payments import gen_public_id

    store = models.Store(
        account_id=account.id,
        public_id=configured_id or gen_public_id("st_"),
        name=HQ_STORE_NAME,
        is_internal=True,
        status=models.ACCOUNT_ACTIVE,
        city="Phnom Penh",
    )
    session.add(store)
    await session.flush()

    if link_url:
        from ..services.payway_parser import _extract_slug

        session.add(
            models.PaymentLink(
                store_id=store.id,
                link_type=models.LINK_ABA_PAYWAY,
                raw_link=link_url,
                merchant_account_id=_extract_slug(link_url),
                merchant_name=HQ_STORE_NAME,
                currency="USD",
                verification=models.LINK_VERIFIED,
                status=models.ACCOUNT_ACTIVE,
            )
        )
        store.status = models.STORE_ACTIVE


async def _maybe_promote_admin_and_seed_hq(
    session: AsyncSession,
    account: models.Account,
) -> None:
    """Promote an address in CHMABAPAY_ADMIN_EMAILS, then make sure the HQ store
    exists. The white-label entitlement goes with the promotion because the HQ store
    is the platform's own. Idempotent: safe to call on every login for every account.
    """
    admin_emails = _admin_emails()
    if admin_emails and (account.email or "").lower() in admin_emails:
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

    await _ensure_hq_store(session, account)

    if not account.is_platform_admin:
        # The session dependency does not commit, so persist the promotion (if any)
        # here rather than letting it be discarded when the request ends.
        await session.commit()
        return

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
    await session.commit()


# What a frozen (`restricted`) account may still reach. §7.4: reads, plus the routes that
# clear the debt. Named explicitly rather than inferred, because "the billing page" *is* the
# point of the state — a frozen merchant with no way to pay is a merchant who phones support.
#
# `POST /api/v1/billing/change-plan` is here in both of its uses: paying by settling the invoice,
# and choosing a smaller plan instead. Both are ways out (§7.5), and neither grants anything
# until it is paid — a paid tier lands in `pending` with an invoice.
RESTRICTED_ALLOWED_WRITES: frozenset[tuple[str, str]] = frozenset(
    {("POST", "/api/v1/billing/change-plan")}
)

# The reads a frozen account may *not* have, because they are not reads.
#
# `GET /api/v1/transactions/check-status/{id}` settles the payment it polls: its handler reaches
# `status_reconciler` → `services.payments.mark_paid`, which flips the payment, writes a ledger
# entry, fires a webhook and can settle a plan invoice. It is exempt from the read allowance on
# purpose, and refusing it costs the merchant nothing: settlement is W1's job, on a 5s/30s
# sweep, and it does not depend on the merchant's own poll. An in-flight code a customer is
# still holding therefore settles normally (§7.6) — what stops is the *merchant* driving writes.
RESTRICTED_REFUSED_READS: frozenset[str] = frozenset({"/api/v1/transactions/check-status"})


def _under(path: str, prefix: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def restricted_may_reach(request: Request) -> bool:
    """Whether a frozen account's session may make this request.

    The rule is deliberately coarse: **reads are open, writes are closed except the billing
    escape hatch.** Every route that does something reaches the gate through one of the two
    choke points, so a new route is covered the day it is added rather than the day someone
    remembers — `test_every_mutating_route_refuses_a_frozen_account` proves it by enumerating
    the app's own routes.
    """
    method = request.method.upper()
    if (method, request.url.path) in RESTRICTED_ALLOWED_WRITES:
        return True
    if method in ("GET", "HEAD", "OPTIONS"):
        return not any(
            _under(request.url.path, prefix) for prefix in RESTRICTED_REFUSED_READS
        )
    return False


async def get_current_session_account(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> models.Account:
    """FastAPI dependency: read JWT cookie, verify, load Account. Raises 401 on failure.

    A `restricted` account is *served*, not locked out — that is the whole reason the freeze
    uses its own status rather than `suspended` (§7.4). It gets here, and the read-only gate
    above decides per request what it may do. `suspended` keeps its 401: an account under
    review cannot sign in at all, and `suspended` outranks `restricted`.
    """
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
    if account is None or account.status not in (
        models.ACCOUNT_ACTIVE,
        models.ACCOUNT_RESTRICTED,
    ):
        raise HTTPException(status_code=401, detail="invalid_session")
    if account.status == models.ACCOUNT_RESTRICTED and not restricted_may_reach(request):
        raise HTTPException(status_code=403, detail="account_restricted")
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


def _audit_login(
    session: AsyncSession,
    account: models.Account | None,
    action: str,
    *,
    email: str,
    ip: str,
    reason: str | None,
    **extra: Any,
) -> None:
    """Stage one sign-in audit row in the caller's transaction.

    An unknown email has no account to attribute the attempt to, so the row carries
    a null actor and the attempted address in `details` — the *attempt* is the
    thing worth keeping, and dropping it would leave credential probing invisible
    precisely where it is most likely to be probing. `target_id` is 0 in that case,
    since the column is not nullable and there is no account to point at.

    The password is never read here. Nothing in this function touches it.
    """
    details: dict[str, Any] = {"method": "password", "email": email, "ip": ip}
    if reason is not None:
        details["reason"] = reason
    details.update(extra)
    audit.record(
        session,
        actor=account,
        action=action,
        target_type="Account" if account is not None else "Email",
        target_id=account.id if account is not None else 0,
        details=details,
    )


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
    (see `python -m chmabapay.cli set-password`).

    Two guards apply here that did not before. A per-email lockout, because the
    per-address limiter in front of `/auth/*` cannot bound guessing spread across
    addresses nor slow a targeted attack on one known email. And an audit row for
    every attempt, because a failed admin sign-in left no trace at all — the
    mutations were all recorded, but nothing said who had been trying to get in.
    """
    settings = get_settings()
    email = body.email.strip().lower()
    lockout = getattr(request.app.state, "login_lockout", None)
    ip = client_ip(request)

    if lockout is not None:
        # Checked before the password, so a locked window cannot be probed and the
        # answer is the same generic 401 as a wrong password — otherwise this
        # endpoint reports which emails are worth attacking.
        remaining = lockout.locked_for(email)
        if remaining > 0:
            _audit_login(
                session,
                None,
                "auth.login_blocked",
                email=email,
                ip=ip,
                reason="locked_out",
                locked_for_seconds=remaining,
            )
            await session.commit()
            raise HTTPException(
                status_code=429,
                detail="too_many_attempts",
                headers={"Retry-After": str(remaining)},
            )

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
        if lockout is not None:
            lockout.record_failure(email)
        # Committed, not merely staged: raising discards the session's transaction,
        # and an audit row that only survives a successful request is not an audit
        # row. This is why the failure path commits before it raises.
        _audit_login(
            session, account, "auth.login_failed", email=email, ip=ip,
            reason="invalid_credentials",
        )
        await session.commit()
        raise HTTPException(status_code=401, detail="invalid_credentials")
    if account.status not in (models.ACCOUNT_ACTIVE, models.ACCOUNT_RESTRICTED):
        # `restricted` is allowed through, and only `restricted`: a frozen merchant has to be
        # able to sign in to reach the billing page that clears the debt. `suspended` is an
        # operator's lockout and stays one — no session at all.
        _audit_login(
            session, account, "auth.login_failed", email=email, ip=ip,
            reason="account_suspended",
        )
        await session.commit()
        raise HTTPException(status_code=403, detail="account_suspended")

    await _maybe_promote_admin_and_seed_hq(session, account)
    await session.refresh(account)

    if lockout is not None:
        lockout.clear(email)
    _audit_login(
        session, account, "auth.login_succeeded", email=email, ip=ip, reason=None
    )
    # `_maybe_promote_admin_and_seed_hq` commits its own work; this commits the
    # audit row that belongs with it.
    await session.commit()

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
        account = await _upsert_account(
            session, email=email, name=email.split("@")[0], rename_existing=False
        )
        # Same promotion the Google callback does, so CHMABAPAY_ADMIN_EMAILS
        # accounts can reach /api/v1/admin and the admin UI in dev too.
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
        account = await _upsert_account(
            session, email=email, name=email.split("@")[0], rename_existing=False
        )
        await _maybe_promote_admin_and_seed_hq(session, account)
        await session.refresh(account)
        jwt = _make_session_jwt(account, "dev")
        response = RedirectResponse(url="/", status_code=303)
        _set_session_cookie(response, jwt, request, settings.jwt_ttl_seconds)
        return response

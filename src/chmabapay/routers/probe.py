"""Internal synthetic payment probe.

This route exists so an uptime monitor can keep its hourly check without writing
a payment into a merchant's store. It mints a QR against the platform's own
internal store and stops there: no `Payment` row, no expiry sweep, no
`payment.expired` webhook, and nothing a merchant can see.

Hidden from the public OpenAPI surface on purpose. It is an operational endpoint
for the platform admin, not part of the merchant API.
"""

from __future__ import annotations

import asyncio
import secrets
import socket
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import AuthContext, get_current_auth_context
from ..config import get_settings
from ..db import get_session
from ..schemas import money_to_str, normalize_currency
from ..services import payments as payments_svc

router = APIRouter(
    prefix="/api/v1/internal",
    tags=["internal"],
    include_in_schema=False,
)


async def _probe_auth(
    request: Request,
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> AuthContext | None:
    """Authorize with the scoped probe token, or a platform-admin key.

    The token is the recommended path for an external monitor: a platform-admin API
    key can create stores, read every account and run the console, which is far more
    access than an hourly health check needs. `None` means "the token matched" and
    there is no account context to check.
    """
    configured = (get_settings().internal_probe_token or "").strip()
    if configured and authorization and authorization.startswith("Bearer "):
        presented = authorization[len("Bearer ") :].strip()
        if presented and secrets.compare_digest(
            presented.encode("utf-8"), configured.encode("utf-8")
        ):
            return None
    return await get_current_auth_context(request, authorization, session)


class PaymentProbeIn(BaseModel):
    """Body for the probe.

    `store` and `merchant` are accepted but ignored. They are here so an existing
    monitor can be repointed by changing only its URL and key: the endpoint is
    deliberately incapable of touching a merchant store, even when the old body
    still names one.
    """

    amount: Decimal = Field(default=Decimal("0.10"), gt=0)
    currency: Literal["USD", "KHR"] | None = None
    hosted_qr: bool = Field(
        default=False,
        description=(
            "False builds the QR with our own encoder. True asks ABA for a hosted "
            "session, which proves the mint path but spends a live ABA checkout."
        ),
    )
    store: str | None = None
    merchant: str | None = None
    reference_id: str | None = None
    metadata: dict | None = None

    @field_validator("amount")
    @classmethod
    def _at_most_two_places(cls, value: Decimal) -> Decimal:
        scaled = value * 100
        if scaled != scaled.to_integral_value():
            raise ValueError("invalid_amount")
        return value


class PaymentProbeOut(BaseModel):
    status: str = "ok"
    probe: str = "internal"
    store: str
    currency: str
    amount: str
    amount_cents: int
    hosted: bool
    qr_md5: str | None
    expires_at: datetime


@router.post("/payment-probe", response_model=PaymentProbeOut)
async def payment_probe(
    body: PaymentProbeIn | None = None,
    ctx: AuthContext | None = Depends(_probe_auth),
    session: AsyncSession = Depends(get_session),
) -> PaymentProbeOut:
    """Mint an internal-store QR and return its fingerprint.

    The QR itself is not returned: this is a health check, not a payable link. A
    caller only needs to know that the store, link, auth and encoder all worked.
    """
    if ctx is not None and not ctx.account.is_platform_admin:
        # A merchant key must not be able to trigger platform-store work. This is
        # the same boundary the console uses, and it is what keeps a leaked
        # merchant key out of the internal store.
        raise HTTPException(status_code=403, detail="platform_admin_required")

    payload = body or PaymentProbeIn()
    amount_cents = int(payload.amount * 100)
    store, link, amount_cents, qr_md5, expires_at, hosted = (
        await payments_svc.probe_internal_qr(
            session,
            amount_cents=amount_cents,
            currency=payload.currency,
            hosted_qr=payload.hosted_qr,
        )
    )
    currency = normalize_currency(link.currency)
    return PaymentProbeOut(
        store=store.public_id,
        currency=currency,
        amount=money_to_str(amount_cents, currency, grouping=False),
        amount_cents=amount_cents,
        hosted=hosted,
        qr_md5=qr_md5,
        expires_at=expires_at,
    )


# --------------------------------------------------------------------------- #
# Egress probe
# --------------------------------------------------------------------------- #
# Why this exists: on 2026-11-04 this host's outbound DNS degraded and every
# lookup cost ~4s. `/health` answered 200 the whole time, and the internal
# payment probe never leaves the box, so no monitor watching either of them
# could see it. This endpoint performs a real resolve + TCP connect *from inside
# the application container*, which is the capability that was actually broken.
#
# It is read-only and touches two fixed, public hosts, so it cannot be turned
# into a general-purpose fetcher.
EGRESS_TARGETS: tuple[tuple[str, str, int], ...] = (
    ("telegram", "api.telegram.org", 443),
    ("aba", "link.payway.com.kh", 443),
)
EGRESS_TIMEOUT_SECONDS = 5.0


class EgressCheckOut(BaseModel):
    name: str
    host: str
    ok: bool
    detail: str
    ms: int


class EgressOut(BaseModel):
    status: Literal["ok", "degraded"]
    checks: list[EgressCheckOut]
    generated_at: datetime


async def _egress_check(
    name: str, host: str, port: int, timeout: float
) -> EgressCheckOut:
    """Resolve `host`, then connect to the address that came back.

    Both phases are timed and reported separately, because the two failures look
    identical from outside and need different fixes: a resolver that times out is
    `/etc/resolv.conf`, while a refused connection is egress or the remote host.
    """
    loop = asyncio.get_running_loop()
    started = time.monotonic()
    try:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), timeout=timeout
        )
    except TimeoutError:
        return EgressCheckOut(
            name=name,
            host=host,
            ok=False,
            detail=f"DNS timed out after {timeout:.1f}s",
            ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return EgressCheckOut(
            name=name,
            host=host,
            ok=False,
            detail=f"DNS failed: {type(exc).__name__}: {exc}",
            ms=int((time.monotonic() - started) * 1000),
        )

    dns_ms = int((time.monotonic() - started) * 1000)
    if not infos:
        return EgressCheckOut(
            name=name, host=host, ok=False, detail="DNS returned no address", ms=dns_ms
        )

    family, socktype, proto, _, address = infos[0]
    writer = None
    connect_started = time.monotonic()
    try:
        _, writer = await asyncio.wait_for(
            asyncio.open_connection(
                host=address[0],
                port=port,
                family=family,
                proto=proto,
                # The address is already resolved; asking again would pay the
                # lookup twice and hide which phase was slow.
            ),
            timeout=timeout,
        )
    except TimeoutError:
        return EgressCheckOut(
            name=name,
            host=host,
            ok=False,
            detail=f"DNS {dns_ms}ms, connect timed out after {timeout:.1f}s",
            ms=int((time.monotonic() - started) * 1000),
        )
    except OSError as exc:
        return EgressCheckOut(
            name=name,
            host=host,
            ok=False,
            detail=f"DNS {dns_ms}ms, connect failed: {type(exc).__name__}: {exc}",
            ms=int((time.monotonic() - started) * 1000),
        )
    finally:
        if writer is not None:
            writer.close()

    connect_ms = int((time.monotonic() - connect_started) * 1000)
    return EgressCheckOut(
        name=name,
        host=host,
        ok=True,
        detail=f"DNS {dns_ms}ms, connect {connect_ms}ms",
        ms=int((time.monotonic() - started) * 1000),
    )


@router.get("/egress", response_model=EgressOut, responses={503: {"model": EgressOut}})
async def egress_probe(
    response: Response,
    ctx: AuthContext | None = Depends(_probe_auth),
) -> EgressOut:
    """Prove the application can still resolve names and open outbound connections.

    200 means every target answered. 503 lists what did not, so a monitor can
    decide on the status code alone. The response is JSON in both cases because
    the detail is the part a human needs at 3am.
    """
    if ctx is not None and not ctx.account.is_platform_admin:
        raise HTTPException(status_code=403, detail="platform_admin_required")

    checks = await asyncio.gather(
        *(
            _egress_check(name, host, port, EGRESS_TIMEOUT_SECONDS)
            for name, host, port in EGRESS_TARGETS
        )
    )
    healthy = all(check.ok for check in checks)
    if not healthy:
        response.status_code = 503
    return EgressOut(
        status="ok" if healthy else "degraded",
        checks=list(checks),
        generated_at=datetime.now(UTC),
    )

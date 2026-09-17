"""Per-identity request limits (P1-1).

Three properties are deliberate:

* **Nothing here is distributed.** Counters live in this process, the same
  single-replica constraint the workers carry until P2-3. Two replicas would each
  admit the full limit. Stated, not implied.
* **The limits over money are keyed by API key**, so a caller cannot rotate them
  away. `POST /v1/payments` and the reissue path both mint an ABA session, and
  both need a key, so a per-key limit is what bounds outbound ABA traffic.
* **The unauthenticated surfaces are keyed by IP**, because there is no key to
  key on. `X-Forwarded-For` is caller-controlled when this service is reached
  directly, and that is tolerable here *because* it only moves a caller between
  IP buckets — never past a key-keyed one.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from cachetools import TTLCache
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import JSONResponse, Response

from .config import get_settings
from .security import hash_key

WINDOW_SECONDS = 60
_MAX_TRACKED_IDENTITIES = 50_000


@dataclass(frozen=True)
class Rule:
    """One limit: its name for the error body, its ceiling, and what it counts per."""

    name: str
    limit: int
    per: str  # "key" (falling back to IP) or "ip"


@dataclass
class _Window:
    started: float
    count: int


def rule_for(request: Request) -> Rule | None:
    """The rule governing this request, or None to wave it through.

    Ordered: the costly surfaces are matched before the generic `/v1/` bucket
    they would otherwise fall into.
    """
    settings = get_settings()
    path = request.url.path
    method = request.method.upper()

    # Health checks must never be throttled, and `/_dev` only exists on a
    # deployment that has already declared itself non-production.
    if method == "OPTIONS" or path == "/health" or path.startswith("/_dev"):
        return None

    # Both of these mint an ABA-issued QR, so they are the calls that cost money
    # to abuse.
    reissue = path.startswith("/v1/payments/") and path.rstrip("/").endswith("/reissue")
    if method == "POST" and (path.rstrip("/") == "/v1/payments" or reissue):
        return Rule(
            "payment_create", settings.rate_limit_payment_create_per_minute, "key"
        )

    # Unauthenticated by design — it is how a caller validates an ABA link before
    # signing up — yet every route here reaches out to ABA. No key to key on.
    if path.startswith("/v1/khqr"):
        return Rule("khqr", settings.rate_limit_khqr_per_minute, "ip")

    # Credential surfaces: the brute-force target.
    if path.startswith(("/auth", "/api/v1/auth", "/user/google/auth")):
        return Rule("auth", settings.rate_limit_auth_per_minute, "ip")

    # The public checkout page a payer opens from their phone.
    if path.startswith("/pay/"):
        return Rule("checkout", settings.rate_limit_checkout_per_minute, "ip")

    if path.startswith("/v1/"):
        return Rule("api", settings.rate_limit_api_per_minute, "key")

    return None


def identity_for(request: Request, rule: Rule) -> str:
    """Who this request counts against.

    An API key is hashed rather than kept: the raw credential must not end up in
    a cache, and its hash is already how the key is stored at rest.
    """
    if rule.per == "key":
        scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
        if scheme.lower() == "bearer" and token:
            return "key:" + hash_key(token)
    return "ip:" + client_ip(request)


def client_ip(request: Request) -> str:
    """The caller's address, as far as this deployment can honestly tell."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


class RateLimiter:
    """Fixed-window counters, one per (rule, identity)."""

    def __init__(self, maxsize: int = _MAX_TRACKED_IDENTITIES) -> None:
        # The TTL here only reclaims memory: a live window is never reassigned, so
        # its entry expires exactly one window after it began. Under memory
        # pressure an evicted counter forgives a caller — this fails *open*, which
        # is the right way round for a limiter that must not take the API down.
        self._windows: TTLCache[str, _Window] = TTLCache(
            maxsize=maxsize, ttl=WINDOW_SECONDS
        )

    def hit(self, rule: Rule, identity: str) -> tuple[bool, int, int]:
        """Count one request. Returns (allowed, remaining, retry_after_seconds)."""
        now = time.monotonic()
        key = f"{rule.name}:{identity}"
        window = self._windows.get(key)
        if window is None or now - window.started >= WINDOW_SECONDS:
            window = _Window(started=now, count=0)
            self._windows[key] = window

        window.count += 1
        elapsed = now - window.started
        return (
            window.count <= rule.limit,
            max(0, rule.limit - window.count),
            max(1, int(WINDOW_SECONDS - elapsed)),
        )

    def reset(self) -> None:
        """Forget every counter. Each test starts from a clean slate with this."""
        self._windows.clear()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Applies `rule_for` to every request, and answers 429 beyond a limit."""

    def __init__(self, app, limiter: RateLimiter) -> None:
        super().__init__(app)
        self.limiter = limiter

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if not get_settings().rate_limit_enabled:
            return await call_next(request)

        rule = rule_for(request)
        if rule is None:
            return await call_next(request)

        allowed, remaining, retry_after = self.limiter.hit(
            rule, identity_for(request, rule)
        )
        headers = {
            "X-RateLimit-Limit": str(rule.limit),
            "X-RateLimit-Remaining": str(remaining),
        }
        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "detail": f"rate_limited: {rule.name}",
                    "limit": rule.limit,
                    "window_seconds": WINDOW_SECONDS,
                    "retry_after": retry_after,
                },
                headers={**headers, "Retry-After": str(retry_after)},
            )

        response = await call_next(request)
        response.headers.update(headers)
        return response

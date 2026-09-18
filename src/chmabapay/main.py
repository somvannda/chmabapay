"""ChmabaPay FastAPI application — T5 lifecycle wiring + trace middleware (AC-19).

Only ONE `while True` loop exists in the whole src/ tree:
  QueueTransport.run_workers() in workers/base.py (NFR-2, AC-2 verified).
Old legacy while loops in webhooks.py (webhook_loop/expiry_loop/bakong_verify_loop)
were REPLACED by Worker.process(Job) wrappers + recurring fanout heartbeats.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from secrets import compare_digest

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from . import errors, observability
from .config import (
    assert_a_queue_will_be_drained,
    assert_session_secret_is_chosen,
    get_settings,
)
from .db import ensure_schema, seed_plans_if_needed
from .ratelimit import RateLimiter, RateLimitMiddleware
from .routers import (
    account,
    admin,
    auth,
    billing,
    checkout,
    keys,
    khqr,
    payments,
    reports,
    stores,
    transactions,
    webhooks,
)
from .workers import QueueTransport, set_global_transport
from .workers.runtime import build_transport, start_workers

log = logging.getLogger(__name__)


def cors_origins(settings) -> list[str]:
    """The browser origins allowed to call this API.

    An operator-supplied list wins. Otherwise: the canonical public origin, plus
    localhost when the deployment has declared itself a dev one through
    `ENABLE_DEV_GATEWAY` — the same flag that mounts `/_dev`. An empty result is
    valid and secure: no cross-origin browser caller at all, which is the truth
    for a deployment whose own frontends are reverse-proxied onto this origin.
    """
    configured = [
        origin.strip()
        for origin in (settings.cors_allowed_origins or "").split(",")
        if origin.strip()
    ]
    if configured:
        return configured

    origins: list[str] = []
    if settings.public_origin:
        origins.append(settings.public_origin.rstrip("/"))
    if settings.enable_dev_gateway:
        origins += [
            "http://localhost:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:3001",
        ]
    return origins


def create_app() -> FastAPI:
    settings = get_settings()

    transport: QueueTransport | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        nonlocal transport
        observability.configure_logging()
        # Before the database, before the workers, before anything is served: a
        # process about to sign sessions with a secret published in the repository
        # has nothing useful to do, and it must not look healthy while doing it.
        assert_session_secret_is_chosen(settings)
        # And a process with no reachable queue consumer has nothing useful to do
        # either: it would accept payments and drop every job meant to confirm them.
        assert_a_queue_will_be_drained(settings)
        # Schema changes are never applied here: a database behind the code must
        # fail loudly, not be silently patched into an unknown shape.
        await ensure_schema()
        await seed_plans_if_needed()

        if not settings.metrics_token:
            log.warning(
                "/metrics is served unauthenticated (set METRICS_TOKEN, or restrict "
                "the path at the proxy); it discloses platform-wide volumes"
            )

        errors.warn_if_unconfigured()

        stop = asyncio.Event()
        tasks: list[asyncio.Task] = []

        # A transport is built when this process will drain, and also when the queue
        # is shared — because then this process still has to *enqueue* (every route
        # that creates a payment does) and still has to report queue depth on
        # /metrics, even if the draining happens somewhere else. With `inprocess`
        # and workers disabled there is deliberately no transport, which is the
        # long-standing test and tooling behaviour: an enqueue with nowhere to go
        # drops the job rather than queueing it for a consumer that will never come.
        if settings.workers_enabled or (settings.worker_transport or "").strip().lower() == "redis":
            transport = build_transport(settings)
            # Refuse to boot on an unreachable queue. Every enqueue site swallows its
            # exceptions by design, so a wrong URL would otherwise present as payments
            # that are taken and never detected.
            await transport.ping()
            app.state.worker_transport = transport
            set_global_transport(transport)

        if settings.workers_enabled:
            tasks.extend(start_workers(transport, stop))
        else:
            log.info(
                "workers are disabled in this process; the drain loops must be running "
                "elsewhere against the same queue"
            )

        yield

        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)

    # Registered *before* CORS so that CORS ends up the outer layer. A 429 has to
    # arrive with Access-Control-Allow-Origin, or a browser caller sees an opaque
    # network failure instead of "you are being rate limited".
    limiter = RateLimiter()
    app.state.rate_limiter = limiter
    app.add_middleware(RateLimitMiddleware, limiter=limiter)

    app.add_middleware(
        CORSMiddleware,
        # No wildcard: it is replaced by `cors_origins`, which is explicit.
        # `allow_credentials` can only be true once the origin list is a real
        # allowlist — browsers reject `*` combined with credentials, which is
        # precisely why the wildcard had to go for a cookie-authenticated API.
        allow_origins=cors_origins(settings),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        # Headers stay a wildcard deliberately: the origin allowlist decides who
        # may call, and a narrow header list only breaks integrators whose client
        # sends one we did not think of.
        allow_headers=["*"],
        expose_headers=[
            "X-ChmabaPay-Trace",
            "X-ChmabaPay-Warning",
            "X-RateLimit-Limit",
            "X-RateLimit-Remaining",
            "Retry-After",
        ],
    )

    # ---------- AC-19: X-ChmabaPay-Trace UUID4 header (EVERY endpoint incl /health) ----------
    @app.middleware("http")
    async def trace_header_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        trace_id = request.headers.get("X-ChmabaPay-Trace") or str(uuid.uuid4())
        try:
            _ = uuid.UUID(trace_id)
        except ValueError:
            trace_id = str(uuid.uuid4())
        # Bound, not just echoed: this is the value every log record inside the
        # request carries, which is what makes the header useful.
        token = observability.bind_trace_id(trace_id)
        try:
            response = await call_next(request)
        except Exception as exc:
            # Captured here rather than in a handler for `Exception`, deliberately. Such
            # a handler is installed as ServerErrorMiddleware, which sits *outside* this
            # middleware — so by the time it ran, the trace id would already be unbound
            # and the report could not name it. The exception is re-raised so the
            # response is exactly what Starlette would have sent without us.
            template = getattr(request.scope.get("route"), "path", None) or "<unmatched>"
            await errors.report_exception(
                exc, where=f"api {request.method} {template}", context=request.url.path
            )
            raise
        finally:
            observability.release_trace_id(token)
        response.headers["X-ChmabaPay-Trace"] = trace_id
        return response

    # Registered last so it is the outermost layer: it has to see the status a
    # caller actually received, including the 429 the rate limiter short-circuits
    # and the 500 an unhandled exception turns into further in.
    @app.middleware("http")
    async def metrics_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        started = time.perf_counter()

        def record(status: str) -> None:
            route = request.scope.get("route")
            # The route *template*, never the raw path: a label per URL is
            # unbounded, and a payment id in a label is an outage of its own.
            template = getattr(route, "path", None) or "<unmatched>"
            observability.HTTP_REQUESTS.labels(
                method=request.method, route=template, status=status
            ).inc()
            observability.HTTP_DURATION.labels(
                method=request.method, route=template
            ).observe(time.perf_counter() - started)

        try:
            response = await call_next(request)
        except Exception:
            # Whatever escapes a handler ships as a 500, and that is the count
            # worth having; recording it only on the way out would drop exactly
            # the requests an operator is looking for.
            record("500")
            raise
        record(str(response.status_code))
        return response

    app.include_router(auth.router)
    app.include_router(auth.router_v1_alias)
    app.include_router(auth.router_user_google)
    app.include_router(payments.router)
    app.include_router(stores.router)
    app.include_router(checkout.router)
    app.include_router(transactions.router)
    app.include_router(khqr.router)
    app.include_router(account.router)
    app.include_router(keys.router)
    app.include_router(webhooks.router)
    app.include_router(billing.router)
    app.include_router(admin.router)
    app.include_router(reports.router)
    if settings.enable_dev_gateway:
        from .routers import dev

        app.include_router(dev.router)

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "app": settings.app_name}

    @app.get("/metrics", include_in_schema=False)
    async def metrics_endpoint(request: Request) -> Response:
        """Prometheus exposition. Excluded from the schema on purpose.

        Open by default so a scrape works out of the box; set `METRICS_TOKEN` to
        require `Authorization: Bearer <token>`, and restrict the path at the proxy
        either way — it discloses platform-wide volumes.
        """
        expected = get_settings().metrics_token
        if expected:
            scheme, _, presented = (request.headers.get("authorization") or "").partition(
                " "
            )
            if scheme.lower() != "bearer" or not compare_digest(presented, expected):
                raise HTTPException(status_code=401, detail="invalid_metrics_token")

        transport = getattr(app.state, "worker_transport", None)
        if transport is not None:
            # Queue figures are pulled at scrape time, so a stalled worker shows up
            # as a heartbeat age that stops advancing rather than a stale value.
            await observability.refresh_queue_metrics(transport, time.monotonic())

        body, content_type = observability.render_metrics()
        return Response(content=body, media_type=content_type)

    return app

app = create_app()

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "ChmabaPay"
    database_url: str = "sqlite+aiosqlite:///./chmabapay.db"

    # A fake Bakong rail mounted at /_dev where POST /_dev/payments/{id}/pay marks a
    # payment paid without money moving, and GET /auth/_dev/login mints a real session
    # cookie for any email you name. Neither is behind a credential, so this defaults
    # to OFF: an omitted variable must not produce the state that hands out sessions.
    # Local dev turns it back on explicitly (`.env`, `ENABLE_DEV_GATEWAY=true`).
    enable_dev_gateway: bool = False

    # In-process background loops (poll DB). Production swaps to Redis + workers.
    workers_enabled: bool = True

    # P2-3 scale-out. Which transport this process uses, and — independently —
    # whether it runs the drain loops.
    #
    # These are two switches, not one, because a process can need to *enqueue*
    # without wanting to *consume*: with workers in their own process the API still
    # has to post jobs and report queue depth, so its transport must be `redis` while
    # `workers_enabled` is false.
    #
    #   inprocess (default) — one process, one asyncio queue. The API enqueues and drains.
    #   redis               — a shared Redis Streams queue, so any number of processes
    #                         can enqueue and any number can drain.
    worker_transport: str = "inprocess"

    # Only read when `worker_transport` is redis. 56379, not the default 6379: this
    # machine already answers 6379 with a native Redis 3.0.504 for Windows, which has
    # no `HELLO` (Redis 6) and no streams at all (`XADD` is Redis 5). See the redis
    # service in docker-compose.yml. Inside compose the services use `redis:6379`.
    redis_url: str = "redis://localhost:56379/0"

    # How long a dedup key suppresses duplicates. Long on purpose: the key has to
    # outlive the job, so that a job finishing does not immediately allow the same
    # work to be queued again.
    queue_dedup_ttl_seconds: int = 3600

    # How long an entry may sit unacknowledged before another worker assumes its
    # consumer died and takes it over. Must exceed the slowest legitimate job;
    # webhook delivery is capped by `webhook_timeout_seconds`, so 60s is a wide margin.
    queue_claim_idle_seconds: float = 60.0
    # Verify at startup that the database is at the Alembic head revision, and
    # refuse to boot if it is not. Tests build their schema directly from the
    # models, so they turn this off.
    schema_check: bool = True
    expiry_interval_seconds: float = 5.0
    webhook_poll_interval_seconds: float = 1.0
    webhook_max_attempts: int = 8
    webhook_timeout_seconds: float = 5.0

    checkout_ttl_seconds: int = 300  # QR / payment lifetime

    # How long we keep reconciling a payment that has not settled.
    #
    # This is not the QR's lifetime — that is ABA's, and it is 180s. This is how
    # long we keep *looking*, and it must outlast the rail's willingness to accept
    # the payment or we can take money and record it nowhere.
    #
    # Measured 2026-09-17: a customer paid 9.5 minutes after ABA's 180s window had
    # closed, and ABA accepted it. So the acceptance window is at least 570s, and
    # its real ceiling is unknown — ABA does not publish it and the hosted status
    # endpoint does not report one. A default *below* that ceiling is a silent
    # data-loss bug: the sale exists in the merchant's ABA account and nowhere in
    # ours. Hence an hour, deliberately generous rather than tuned.
    #
    # Cost is one ABA HTTP call per unsettled payment per sweep (30s), capped at
    # `_ORPHAN_BATCH` per sweep — 2400 calls/hour across the platform. Raise the
    # batch before raising this.
    #
    # When a payment crosses this boundary still unsettled, W1 makes one final
    # check and records it; if that check cannot get an answer, it raises an
    # operator alert. That is the difference between "we stopped looking" and
    # "we lost track of it", and only one of those is acceptable.
    detection_window_seconds: int = 3600

    # P1-1 rate limiting: per-minute ceilings, one per path class. See
    # chmabapay/ratelimit.py for what each one counts against. Counters are
    # in-process, so this bounds a single replica until P2-3.
    # A load generator such as /_dev/integration-test has to run with this off:
    # 1272 cases in a minute is abuse-shaped by construction.
    rate_limit_enabled: bool = True
    rate_limit_auth_per_minute: int = 20
    rate_limit_checkout_per_minute: int = 120
    rate_limit_khqr_per_minute: int = 60
    rate_limit_api_per_minute: int = 600
    rate_limit_payment_create_per_minute: int = 60

    # P1-1 CORS. The wildcard is gone. Comma-separated origins; when empty this
    # falls back to `public_origin` plus, on a dev deployment, localhost. An
    # empty result means no cross-origin browser access at all, which is correct
    # for a deployment whose own frontends are reverse-proxied onto one origin.
    # Server-to-server callers — the normal POS integration — are unaffected:
    # CORS is a browser mechanism.
    cors_allowed_origins: str | None = None

    log_level: str = "info"

    # P1-3 observability. `/metrics` is served unauthenticated unless this is set,
    # in which case it requires `Authorization: Bearer <token>`; the startup warning
    # exists so the open default is never a surprise. Restrict it at the proxy
    # either way — it discloses platform-wide volumes.
    metrics_token: str | None = None

    # Alerts reuse the Telegram channel P0-5 built. Without a chat id there is no
    # page, only a log line, and the watcher says so at startup.
    ops_telegram_chat_id: str | None = None
    alert_interval_seconds: float = 30.0
    # A healthy drain loop asks for work every ~0.05s, so anything near a minute
    # means the loop is gone rather than busy.
    alert_worker_stall_seconds: float = 60.0
    alert_webhook_backlog: int = 50
    # A backlog says the queue is not draining; this says the rail is *working* and
    # failing. A merchant whose endpoint refuses every POST keeps its deliveries
    # moving — each one goes `retrying`, then `failed` — so the backlog never grows
    # and only the ratio moves. Callers below the sample floor are ignored so a
    # single failed delivery on a quiet night cannot page.
    alert_webhook_failure_rate: float = 0.1
    alert_webhook_failure_window_seconds: float = 900.0
    alert_webhook_failure_min_sample: int = 20
    # Error tracking (P1-3) shares that channel. First sighting of an exception is
    # sent immediately; repeats inside this window are counted and summarised with
    # the next report. Five minutes is long enough that a hot error cannot flood the
    # channel and short enough that an ongoing one is not forgotten.
    error_report_interval_seconds: float = 300.0

    # P1-4 compliance. The published version of the merchant agreement, recorded
    # against the account when it is accepted. Bump it when the text changes so
    # `terms_accepted_version` can distinguish an account that agreed to the
    # current terms from one that agreed to a superseded draft — re-acceptance is
    # then a query, not a guess.
    terms_version: str = "1"

    # Retention of raw gateway payloads. `payments.gateway_status_raw` holds the
    # ABA PayWay response verbatim, which includes the hosted-checkout session
    # token minted for that payment. Sessions live about a minute; keeping them
    # forever buys nothing and accumulates credentials. 90 days is long enough to
    # investigate a dispute and short enough that they do not pile up. Only this
    # column is purged — status, amounts and the QR survive for accounting.
    retention_gateway_raw_days: int = 90
    # Once a day. The sweep is idempotent, so a missed day costs nothing.
    retention_sweep_interval_seconds: float = 86400.0

    # Billing invoice issuance. Hourly rather than daily, because a subscription
    # becomes due at an arbitrary moment (a signup on the 14th is due on the 14th)
    # and the invoice should appear close to that moment rather than up to a day
    # later. Issuance is idempotent on (account_id, period_month), so the frequency
    # costs one indexed query per sweep and can never double-bill.
    billing_sweep_interval_seconds: float = 3600.0

    # Bakong Open API integration
    bakong_base_url: str = "https://api-bakong.nbc.gov.kh"
    bakong_api_token: str | None = None
    bakong_developer_email: str | None = None
    bakong_timeout_seconds: float = 10.0
    bakong_max_retries: int = 3

    # Bakong payment polling
    bakong_poll_interval_seconds: float = 2.0
    bakong_poll_max_attempts: int = 60

    # Merchant alerts via Telegram. A store holds the `telegram_chat_id`; this
    # token is what turns that id into a delivered message. Without it no alert
    # can be sent, so POST /v1/stores/{id}/telegram/test refuses (503) instead of
    # reporting a success that never happened.
    telegram_bot_token: str | None = None
    telegram_timeout_seconds: float = 5.0

    # JWT session auth. The default is deliberately *nothing*: a secret in this file
    # is in the repository and in the image, so it is public, and a public signing key
    # lets anyone mint a session for any account. `assert_session_secret_is_chosen`
    # below refuses to start on this default, which is why the secret has to arrive
    # from the environment (`.env` in development, the deployment's own env elsewhere).
    jwt_secret_key: str = ""
    jwt_ttl_seconds: int = 86400

    # Google OAuth (optional)
    google_client_id: str | None = None
    google_client_secret: str | None = None
    google_redirect_uri: str | None = None

    # Workers concurrency (NFR-6 semaphore caps per Worker domain)
    worker_w1_concurrency: int = 20   # Payment detection
    worker_w2_concurrency: int = 30   # Webhook send
    worker_w3_concurrency: int = 1    # Billing (M2)
    worker_w4_concurrency: int = 1    # Expiry sweeper

    # W4 expiry batch size
    worker_w4_batch_limit: int = 500

    # W1 recurring fallback DB poll for missed jobs (safety-net; enqueue-on-write preferred)
    worker_w1_fallback_poll_seconds: float = 30.0

    # Billing self-pay HQ store: used by /v1/billing/invoices/{id}/khqr to target
    # ChmabaPay's own internal store when it dog-foods create_payment() to collect plan fees.
    # Accepts either Store.id (int-as-string) or Store.public_id (string). Fallback: first
    # is_platform_admin account's first active Store. If none -> 500 platform_hq_store_not_configured.
    chmabapay_hq_store_id: str | None = None

    # The ABA PayWay share link for the HQ store's own settlement destination.
    # Required for self-pay billing invoices to produce a routable KHQR; if unset,
    # the HQ store is created without a payment link and must be configured via
    # the dashboard before an invoice can be paid.
    # Example: "https://link.payway.com.kh/ABAPAYpe518710Y"
    chmabapay_hq_payway_link: str | None = None

    # Comma-separated list of email addresses that automatically become platform admin + business
    # on their FIRST Google OAuth sign-in. Also seeds the HQ store used for billing self-pay.
    # Example: "ceo@chmaba.com,ops@chmaba.com"  (case-insensitive match against Account.email)
    chmabapay_admin_emails: str | None = None

    # Optional bootstrap password for the addresses above. Used only while an account
    # has no stored password: the first password sign-in hashes this value into the
    # account. Once a password is set (via the CLI), this is ignored.
    chmabapay_admin_password: str | None = None

    # Where to send users after Google OAuth callback (and after dev login) once the session
    # cookie is set. Relative paths (starting "/") are served by the same origin; absolute URLs
    # can redirect across ports when the landing Next is proxied via :3001 but backend is on :8000.
    # Defaults to "/" which on :8000 just shows FastAPI root (useless for user). Set to
    # "http://localhost:3001" or your landing origin so users land on the website after login.
    post_login_redirect_url: str = "/"

    # Public landing/base origin that Google OAuth `redirect_uri` should use if you want the
    # callback to hit the landing first (then transparently proxied to backend via Next rewrites).
    # When None, the raw GOOGLE_REDIRECT_URI from env is used verbatim (targets backend directly).
    public_origin: str | None = None


# The old placeholder default. Still refused even though it is no longer the default:
# environment files written before this changed are still lying around, and a placeholder
# everyone knows is exactly as forgeable as a blank one.
DEV_JWT_SECRET_KEY = "generated-dev-secret-change-in-prod"


class InsecureSessionSecretError(RuntimeError):
    """Refused to start: sessions would be signed with a secret we published."""


def assert_session_secret_is_chosen(settings: Settings) -> None:
    """Refuse to serve when the session secret was never actually chosen.

    Two ways to arrive there, and both look like a working deployment:

    - an empty value, which is the shipped default and what a `.env` copied from
      `.env.example` holds until that line is filled in;
    - the old placeholder default, which an environment file written before this
      changed may still carry. A placeholder everyone knows is exactly as forgeable as
      a blank one, which is why both are refused rather than only the obvious one.

    Raising rather than warning is the point. A warning is one line in the log of a
    container that otherwise comes up healthy, which is exactly the failure this
    needs to prevent — and the compose file's `:?` guard does not cover a deployment
    that never ran through compose.
    """
    secret = (settings.jwt_secret_key or "").strip()
    if secret and secret != DEV_JWT_SECRET_KEY:
        return

    raise InsecureSessionSecretError(
        "JWT_SECRET_KEY is "
        + ("the built-in development placeholder" if secret else "empty")
        + ", which is published in this repository — sessions signed with it can be "
        "forged by anyone who has read the source. Generate a real one:\n"
        '  python -c "import secrets; print(secrets.token_urlsafe(48))"\n'
        "then set JWT_SECRET_KEY in the deployment's environment (see .env.example)."
    )


def assert_a_queue_will_be_drained(settings: Settings) -> None:
    """Refuse to serve when nothing would ever drain this process's queue.

    `WORKERS_ENABLED=false` means "the drain loops are running elsewhere", which is
    only coherent if the queue is shared. Pair it with the default in-process
    transport and the process holds a private queue that no other process can even
    see: every job it enqueues is dropped, and the drop is silent by design because
    enqueue callers swallow their exceptions. The visible symptom would be payments
    that are taken and never detected.

    Reachable by one forgotten variable — turning workers off and forgetting to switch
    the transport — which is exactly the kind of omission that should fail at boot.
    """
    if settings.workers_enabled:
        return
    if (settings.worker_transport or "").strip().lower() == "redis":
        return

    raise RuntimeError(
        "WORKERS_ENABLED is false and WORKER_TRANSPORT is not 'redis': nothing would "
        "drain this process's queue, so every job it enqueues — payment detection "
        "included — would be dropped silently. Either set WORKER_TRANSPORT=redis and "
        "run the worker process, or set WORKERS_ENABLED=true."
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()

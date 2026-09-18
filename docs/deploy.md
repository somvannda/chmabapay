# Deploying ChmabaPay

The development stack is `docker-compose.yml` at the repository root. This document
is what an operator needs: what ships, how to start it, what healthy looks like, and
which decisions in the configuration are deliberate rather than incidental.

**The production stack is different, and it is §12.** Sections 1–11 describe the
local stack — it publishes stray host ports for `psql` and `curl`, ships a Redis it
does not need, and serves plain HTTP. None of that is what runs on the VPS.

Status: the cold-start and routing behaviour below was executed end-to-end on
Docker Desktop (Windows) and is recorded as observed output, not intent. See §11,
"Verified, and not", for the boundaries of that claim — and §12 for what the
production edge was verified against and what it was not.

---

## 1. What ships

| Service   | Image                 | Listens | Role |
|-----------|-----------------------|---------|------|
| `db`      | `postgres:16-alpine`  | 5432    | Application database. Published on **55432** on the host. |
| `redis`   | `redis:7-alpine`      | 6379    | Queue backend. Published on **56379** on the host. |
| `migrate` | built from `Dockerfile` | —    | `alembic upgrade head`, runs once, must exit 0. |
| `api`     | built from `Dockerfile` | 8000  | FastAPI. Owns both auth modes and, by default, the background workers. |
| `worker`  | built from `Dockerfile` | —     | The background workers alone. Behind the `workers` profile — see §10. |
| `landing` | built from `web/Dockerfile` (`APP=landing`) | 3001 | The marketing site **and** the merchant portal. |
| `admin`   | built from `web/Dockerfile` (`APP=admin`)   | 3002 | The platform console. |
| `proxy`   | `nginx:1.27-alpine`   | 8080→80 | The only public door. |

Three deployables by default: one API, one website that carries both marketing and the
merchant dashboard, one console. `web/user`, a superseded duplicate of the landing
workspace's dashboard, has been **deleted** — nothing built it, no proxy routed to it, and
it was the last consumer of the retired account-type concept. `worker` is a fourth process
you can opt into; the default stack keeps the workers inside the API.

**Both published ports are deliberately off their defaults.** This machine already runs
Postgres on 5432 and a **Redis 3.0.504 for Windows** on 6379. The Redis collision is the
worse of the two because it does not fail: the container's mapping is accepted, the
service reports healthy, and the host asking for `localhost:6379` is answered by the
3.0.504 — which has no streams at all (`XADD` arrived in Redis 5) and no `HELLO` (Redis
6). A `REDIS_URL` on the default port fails with `unknown command 'HELLO'` rather than
with anything mentioning a version.

## 2. Prerequisites

- Docker with Compose v2.
- A `.env` in the repository root, or an `--env-file` pointed at one, supplying at
  minimum `JWT_SECRET_KEY`. Compose refuses to start without it:

  > `set JWT_SECRET_KEY (see .env.example) - the built-in default is a published dev placeholder`

  That refusal is intentional, and it is now the *second* line of defence. The
  application's built-in default is `generated-dev-secret-change-in-prod`, which is in
  this repository — sessions signed with it can be forged by anyone who has read the
  source. So `assert_session_secret_is_chosen` refuses to complete startup on that
  value or on an empty one, and the process exits rather than serving:

  ```
  chmabapay.config.InsecureSessionSecretError: JWT_SECRET_KEY is the built-in
  development placeholder, which is published in this repository — ...
  ERROR:    Application startup failed. Exiting.
  ```

  Two guards because they cover different omissions: the compose `:?` catches an
  operator who forgot to supply the variable *through compose*, and the code check
  catches one who supplied the placeholder explicitly, or who never went through
  compose at all. Generate a value with
  `python -c "import secrets; print(secrets.token_urlsafe(48))"`.

> **`.env` is a development file.** It holds `ENABLE_DEV_GATEWAY=true` for the local
> `uv run uvicorn` loop and `PUBLIC_ORIGIN=http://localhost:3001`. Compose reads it
> for interpolation, so the localhost origin does flow into the stack. For a real
> deployment use a separate file — `docker compose --env-file .env.production up -d`
> — rather than editing `.env`.

## 3. First run

```bash
docker compose up -d --build
```

Order is enforced by `depends_on`, not by luck:

```
db (healthy) ─┐
redis (healthy)├─> migrate (exit 0) ─> api (healthy) ─> landing, admin ─> proxy
```

`api` waits on `migrate` with `condition: service_completed_successfully`. That
matters because the application verifies at startup that the database is at the
Alembic head and **refuses to boot** if it is not (P0-1) — it never migrates itself.
Without the gate the API would start first and crash-loop in a way that reads like
an image problem.

First build pulls base images and compiles both Next apps; expect a few minutes.
Later builds reuse the `pnpm` store and the dependency layer.

## 4. What healthy looks like

```bash
docker compose ps
```

```
chmabapay-admin-1   | admin   | Up (healthy)
chmabapay-api-1     | api     | Up (healthy)
chmabapay-db-1      | db      | Up (healthy)
chmabapay-landing-1 | landing | Up (healthy)
chmabapay-proxy-1   | proxy   | Up (healthy)
chmabapay-redis-1   | redis   | Up (healthy)
```

`migrate` is absent from that list because it exits. That is success, not a crash:
`docker compose ps -a` shows it as `Exited (0)`.

Two healthcheck details are load-bearing and should not be "improved" without
thought:

- **`api` checks the shallow `/health`, not the database.** A database blip must not
  make an otherwise-working API look unhealthy and get it restarted, which would
  take down every request in flight to fix a problem the API did not cause.
- **`proxy` checks its own `/nginx-health`, which never touches an upstream.** A
  restarting app must not make the edge look unhealthy.

## 5. Operator alerting

A metric nobody looks at is not an alert. Two variables decide whether a person is
told that something is wrong, and **neither has a safe default** — leave both unset
and the platform is healthy-looking and silent.

| Variable | What it is | If unset |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | The bot that carries the message, from @BotFather | `send_message` raises `telegram_bot_token is not configured` |
| `OPS_TELEGRAM_CHAT_ID` | Which chat the operator alerts land in | `send` logs `ALERT NOT SENT (OPS_TELEGRAM_CHAT_ID unset)` |

Compose already forwards both to `api` and `worker` (docker-compose.yml, `api-env`),
so deploying means putting them in the env file — nothing else. The application logs
this at startup when they are missing:

```
alert delivery is not configured (set OPS_TELEGRAM_CHAT_ID and
TELEGRAM_BOT_TOKEN); conditions will be logged, not sent
```

That line is the honest statement of coverage: without it, every alert is a log line
on a machine nobody is reading.

### What pages, and how often

`ALERT_INTERVAL_SECONDS=30` evaluates the conditions. Each is reported on its
**edge** — once when it starts firing, once when it clears — so a worker that is down
for an hour sends one message, not one a minute.

| Condition | Raised when |
|---|---|
| `worker never started — <queue>` | No dequeue has *ever* happened on that queue. Checked against the queues that should exist, not the ones that happen to be reporting, because a loop that never started is invisible to the latter. |
| `worker stalled — <queue>` | No dequeue for `ALERT_WORKER_STALL_SECONDS` (60). A healthy loop asks every ~0.05s, so this means the loop is gone, not busy. |
| `webhook backlog` | More than `ALERT_WEBHOOK_BACKLOG` (50) deliveries waiting — merchants are not being told about payments. |

Two more arrive through the same notifier but are one-off rather than conditions, so
they go out immediately instead of on the timer: a **suspected double charge** (the
same store and reference paid twice) and a **payment whose fate could not be
determined** at the end of the detection window. Both are in
`docs/production-readiness.md` P0-6.

### The chat has to speak first

A Telegram bot cannot open a conversation. If `OPS_TELEGRAM_CHAT_ID` names a chat
that has never messaged the bot, Telegram answers **HTTP 200** with
`{"ok": false, "description": "Forbidden: bot can't initiate conversation with a
user"}` — so the status code is not the verdict, and `send_message` checks `ok`
rather than `status`. The result is `alert delivery failed (...)`, logged, and no
message. Send `/start` to the bot from the account that owns the chat id before
relying on any of this.

Get the id from @userinfobot, or from `getUpdates` on the bot. It is a **numeric
chat id, not a username.**

### One bot, two audiences

`TELEGRAM_BOT_TOKEN` is read by two callers, so a single bot serves both:

- **operator alerts** — `OPS_TELEGRAM_CHAT_ID`, above;
- **merchant alerts** — each store holds its own `telegram_chat_id`, and
  `POST /v1/stores/{id}/telegram/test` sends a real message to it. Without a token
  that endpoint answers `503 telegram_not_configured` rather than reporting a
  success that never happened.

The bot may also be shared with another deployment, since the token identifies the
bot and the chat id decides the destination. That is not a problem, but it does mean
one project's test message can land in the other's chat.

### The token is a live credential

It grants the ability to send as that bot to any chat it is in, so it belongs in
`.env` (gitignored) and nowhere else — **not** in `docker-compose.yml`, which is
tracked. Anything that can read the token can impersonate the bot.

If several processes drain the queues, each runs its own watcher and a condition
pages on whichever process notices the edge first. It is **not** deduplicated — see
§10.

## 6. Routing

`http://localhost:8080` is the website. The console answers to the `admin.localhost`
host. API paths are routed by `deploy/nginx/api-locations.inc`; everything else
belongs to the app on that vhost.

| Request | Answer | Served by |
|---|---|---|
| `/` | 200 | landing |
| `/api/docs` | 200 | landing (its own page) |
| `/health` | 200 | api |
| `/openapi.json` | 200 | api |
| `/v1/...` without a key | 401 | api |
| `/auth/google/login` | 307 → accounts.google.com | api |
| `Host: admin.localhost` `/` | 200 | admin |
| `/metrics` | **404** | refused at the edge |
| `/docs`, `/redoc` | **404** | refused at the edge |
| `/_dev/login` | **404** | refused at the edge |
| `/auth/_dev/login` | **404** | refused at the edge |

### Why the dev rail is refused twice

`/_dev/*` is a fake Bakong rail where `POST /_dev/payments/{id}/pay` marks a payment
paid without money moving. `/auth/_dev/login` is worse than that: it mints a real
`chmabapay_session` for whatever `email` you name, with no credential.

Both are refused at the application (`ENABLE_DEV_GATEWAY` off) and at the edge
(`return 404`). The edge refusal exists because the flag had already been turned on
by accident once:

- compose interpolated `ENABLE_DEV_GATEWAY` from `.env`, and `.env` sets it `true`
  for local development, so a plain `docker compose up` ran the rail live;
- the edge refused only `/_dev/*` and relied on there being no `location` for it —
  but the session-minting route lives under the **`auth`** router, so `/auth/_dev/login`
  matched the ordinary `/auth/` rule and was proxied through. It answered
  `307` with a valid session cookie.

Two changes make that specific failure impossible: compose now hardcodes
`ENABLE_DEV_GATEWAY: "false"` rather than interpolating it, and
`deploy/nginx/api-locations.inc` refuses `/_dev/` and `/auth/_dev/` explicitly.
Absence of a `location` is not a refusal; `return 404` is.

### Why `/metrics` is not published

It exposes platform-wide request volumes by route, payment outcomes, and queue
depths. Prometheus reaches `api:8000` on the compose network directly. Publishing it
at the edge would disclose that to anyone who asked.

## 7. The two frontend images

One Dockerfile, two images, differing only by `APP`. They must build from the
repository root because the apps are workspaces in one pnpm monorepo and share
`web/shared`.

Two things about that build are easy to break and produce misleading errors:

- **`NODE_ENV=production` is set before `pnpm install`, so the install passes
  `--prod=false` explicitly.** pnpm 9 reads `NODE_ENV` and silently skips
  devDependencies. `next` is a real dependency and survives, so the install looks
  fine — but `typescript` is a devDependency, and without it Next cannot load
  tsconfig `paths`. Every `@/` import then fails with `Can't resolve '@/...'` over
  files that are sitting right there, while `tailwindcss` fails separately on the
  PostCSS step. Two symptoms, one cause, no mention of the missing package.

- **`NEXT_PUBLIC_API_URL` is a build ARG, not a runtime variable, and the Dockerfile
  declares it.** Next evaluates `rewrites()` during `next build` and writes the
  resolved destinations into `routes-manifest.json`, so a value supplied only at run
  time is read too late. And `docker build --build-arg X=` silently *ignores* `X`
  unless the Dockerfile consumes it with `ARG X` — pass it, get a warning nobody
  reads, and the app falls back to `http://127.0.0.1:8000`.

Both of these produced the same visible symptom (`500` from every rewritten path)
for different reasons, so `docker compose logs landing` is the place to look rather
than the browser.

## 8. TLS

Two edges, and it matters which one you are reading about.

**Locally** (`deploy/nginx/nginx.conf`), there is no TLS at all. Cloudflare is not in
front of it, and the edge answers plain HTTP on the compose port.

**In production** (`deploy/nginx/nginx.prod.conf`, §12), the origin terminates TLS
itself with a Cloudflare Origin CA certificate, and Cloudflare reaches it on 8443.
The SSL mode should be **Full (strict)** so that certificate is actually verified;
what has been established by observation is only that it is **not `Flexible`**, since
Flexible speaks plain HTTP to the origin and this origin accepts TLS only on 8443.
The mode itself is a dashboard setting nobody has read back — see the end of §12. An
earlier version of this section said Cloudflare "reaches this origin over plain HTTP",
which is what Flexible would look like — that mode is not in use, and the sentence
was wrong.

What is the same on both edges is that neither may claim a scheme it cannot see. The
`map` blocks preserve an inbound `X-Forwarded-Proto`/`X-Forwarded-Host` and fall back
to `$scheme`/`$host` only when there is none:

```
map $http_x_forwarded_proto $forwarded_proto {
    default $http_x_forwarded_proto;
    ""      $scheme;
}
```

Do not replace that with a hardcoded `https`. The value decides whether the session
cookie is marked `Secure` for non-localhost hosts, so overwriting it changes whether
cookies are sent — invisibly, and not at the moment you made the change but the next
time somebody is mysteriously logged out.

### The client address behind Cloudflare

`nginx.prod.conf` reads the caller's address from `CF-Connecting-IP`, and only when
the peer is one of Cloudflare's published ranges, then forwards a **single** address:

```
proxy_set_header X-Forwarded-For $remote_addr;   # not $proxy_add_x_forwarded_for
```

Both halves are load-bearing, and the reason is in the application rather than the
edge. `src/chmabapay/ratelimit.py` buckets an unauthenticated caller by the *first*
entry of `X-Forwarded-For`. `$proxy_add_x_forwarded_for` **appends** to whatever the
caller sent, so a caller who sends `X-Forwarded-For: 1.2.3.4` puts `1.2.3.4` first
and lands in a fresh bucket on every request — a rate-limit bypass on `/pay/*` and
`/v1/khqr/*`. Verified: with the config above, a caller-supplied
`X-Forwarded-For: 1.2.3.4` is discarded and the real peer address is what the
application receives.

The ranges are pinned in the file because `set_real_ip_from` takes networks, not a
URL. They change; re-check `cloudflare.com/ips-v4` and `/ips-v6` when anything about
client addresses looks wrong. A stale range degrades to the peer address — honest,
but every Cloudflare edge then shares one bucket.

Restricting 8443 to those ranges in the host firewall is still worth doing, because
it keeps traffic on Cloudflare's WAF, DDoS protection and cache. It is not what makes
the address trustworthy: an untrusted peer's `CF-Connecting-IP` is ignored regardless.
Confirmed by observation — a direct request carrying `CF-Connecting-IP: 203.0.113.9`
reached the application as the peer's own address, and the same header from a trusted
peer arrived as `203.0.113.9`.

## 9. Redeploying

```bash
docker compose up -d --build          # rebuild and recreate what changed
docker compose exec proxy nginx -s reload   # only if you edited deploy/nginx/*
```

The nginx config is bind-mounted. Compose does not recreate `proxy` when only the
config file changes, so a config edit needs the reload (or a restart).

Recreating an *app* container also needs nothing — but only because each upstream
carries `resolve` against Docker's DNS, which the edge re-checks every
`valid=10s`. **Do not remove those directives.** Expect a short `502` window right
after a recreate rather than an instant switch: nginx keeps the old address until
the TTL lapses (~16s observed).

Without `resolve`, the failure is total and easy to misread. nginx resolves an
upstream name **once** at config load and caches the address for the life of the
worker, so a recreated container's new IP is never picked up and *every* request
through the edge fails:

```
connect() failed (113: Host is unreachable) while connecting to upstream
  upstream: "http://172.18.0.4:8000/health"
```

That is a redeploy taking the whole site down until somebody happens to restart
nginx — precisely the thing a deploy is supposed to be safe against.

An earlier version of this section asserted the opposite, that recreating an app
container needed nothing, because `--force-recreate landing` was handed the same
IP back and appeared to confirm it. Luck that looks like confirmation is the
worst kind. Re-verified properly after the fix: `--force-recreate api` → `502`
immediately, `200` after ~16s, with no nginx reload.

## 10. Running the drains in their own process

By default the API owns the background workers, and the queue is an in-process
`asyncio` structure — which means the whole stack has **no external dependency whose
failure stops payment detection**.

That is a real property, so giving it up is a choice rather than a surprise. To move
the drains into a process of their own (P2-3):

```bash
# Linux/macOS
API_WORKERS_ENABLED=false WORKER_TRANSPORT=redis docker compose --profile workers up -d

# PowerShell
$env:API_WORKERS_ENABLED='false'; $env:WORKER_TRANSPORT='redis'
docker compose --profile workers up -d
```

Both variables are needed and the application enforces that:

* `WORKER_TRANSPORT=redis` on the API as well, because it still has to **enqueue** — and
  still has to report queue depth on `/metrics` — even though it no longer drains.
* `API_WORKERS_ENABLED=false` so the API does not also consume.

**Forget the second one and nothing breaks** — the API drains the shared queue
alongside the worker, which is exactly how you add API replicas (a consumer group
divides the work, it does not duplicate it). **Forget the first one and the API refuses
to start**:

```
RuntimeError: WORKERS_ENABLED is false and WORKER_TRANSPORT is not 'redis': nothing
would drain this process's queue, so every job it enqueues — payment detection
included — would be dropped silently.
```

That combination is a private queue no other process can see, so every job is dropped
in a swallowed exception. Refusing to boot beats discovering it from a merchant.

### What the worker's healthcheck checks

The API is healthchecked over HTTP. The worker has no HTTP server — one would exist
only to answer a healthcheck — so `docker compose` runs
`python -m chmabapay.healthcheck`, which reads the wall-clock heartbeat each drain loop
publishes to Redis.

That catches the condition nothing else can: a worker that is **up but not draining**.
A crashed process is restarted; a loop wedged on a Redis call sits there looking healthy
while the queues fill behind it, and the alert watcher inside the same process cannot
report it either, because it shares the event loop that stopped making progress.

### Scaling

`docker compose up -d --scale api=3` on its own gives three APIs each with their own
private in-process queue, which is worse than one. Replicas only make sense with
`WORKER_TRANSPORT=redis`, where a consumer group divides the work between them.

Two consequences worth knowing before scaling:

* Every draining process runs its own alert watcher, so each condition still pages —
  but on whichever process notices its edge first. It is not deduplicated.
* The alert decision is made from **local** monotonic heartbeats, which is why the
  watcher starts with the drains rather than beside the API. `/metrics` reports
  heartbeat ages from the shared stamps instead, so a stalled queue is still visible to
  a scrape from a process that never drains.

## 11. Verified, and not

Verified by execution on this machine:

- cold start from no containers, no volumes, no cached images — all six services
  reached `healthy`, `migrate` exited 0;
- the full routing table in §6, including all four refusals returning 404 and
  `/auth/google/login` returning a correctly built `accounts.google.com` redirect;
- `/auth/_dev/login` returns 404 with **no** `Set-Cookie`;
- `docker compose config --quiet` exits 0; `nginx -t` passes;
- the reverting of the dev-login leak was confirmed by reproducing it first (307 plus
  a valid `chmabapay_session`) and re-testing after the fix.

Verified for the split topology in §10, by running it:

- `docker compose --profile workers up -d --build` brings up eight containers, with
  both `api` and `worker` `healthy` — the worker's healthcheck is the one that proves
  all five queues are draining;
- Redis holds the five `chmabapay:queue:*` streams, their `:counters` hashes, a
  `chmabapay:hb:*` stamp per queue, and the dedup keys the worker's own scheduler
  created — so enqueue, drain and cross-process dedup all really ran;
- the API logs `workers are disabled in this process`, and `/metrics` scraped from the
  API reports queue depth for all five queues plus a live heartbeat age, both read from
  the shared Redis — i.e. the non-draining process can still see the queue;
- the dangerous pairing is refused: `WORKERS_ENABLED=false` with the in-process
  transport exits 3 with `Application startup failed`.

Alert delivery (§5), verified against the real Telegram API from the application's
own code path:

- with both variables set, `TelegramNotifier.configured` is `True`,
  `TelegramNotifier.send` returned `True`, and `alert_discrete` delivered a message.
  `True` is not a silent no-op: `send_message` raises unless Telegram answers
  `{"ok": true}`, and `ok: false` is exactly what an unstarted chat returns;
- **the recipient confirmed the messages arrived on their device** (2026-09-17). So
  this is not just "Telegram accepted them" — the `/start` step in §5 was the missing
  half, and the path from `alert_discrete` to a human is now observed end to end.

Not verified:

- a completed Google OAuth round trip. The redirect is built correctly and points at
  the configured client, but the callback needs a browser and a real consent screen.
- a real payment — **verified since** (2026-09-17). Two real 0.10 USD payments
  settled against ABA on this stack, the second delivering a signature-verified
  `payment.completed`. It needs a real ABA PayWay link and a wallet; it does **not**
  need Bakong Open API credentials, which an earlier version of this list claimed.
  See `docs/production-readiness.md` P0-2.
- anything behind Cloudflare. This stack was exercised over plain HTTP on localhost.
- **alert delivery from inside a container.** The message above was sent from the
  application's code path on the host process, not through `api` or `worker` in
  compose. Compose forwards both variables, but that hop has not been observed.
- **an alert condition actually firing.** No stalled worker, webhook backlog, double
  charge or unresolved detection window has occurred since the watcher was built, so
  the timer, the edge tracking and the `RESOLVED` message are asserted in tests, not
  observed in production.
- **more than one draining process at once against a real Redis.** The consumer-group
  split is asserted in `tests/test_queue_redis.py` — including that two readers never
  receive the same job, and that an unacknowledged job is recovered by another worker —
  but those run against `fakeredis` locally and a real `redis:7` service in CI, not
  against a scaled-out deployment.

## 12. Production on the VPS

Everything above describes the local stack. This is what runs on the server.

```bash
cp deploy/.env.example deploy/.env      # then fill in every blank
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env up -d --build
```

From the repository root. Three files carry it: `deploy/docker-compose.prod.yml`,
`deploy/nginx/nginx.prod.conf`, and `deploy/.env` (gitignored — it holds the JWT
signing key and the database password).

**On this VPS it lives at `/opt/chmabapay`** — verified on the host, not assumed:

```
/opt/chmabapos     the POS stack (compose project `deploy`, owns host 80/443)
/opt/chmabapay     this stack (compose project `chmabapay-prod`, owns host 8443)
```

Worth stating because the POS project's own `docs/deploy.md` refers to
`/srv/chmaba`, and `/srv` is **empty** on this machine. A documented path that
nobody verified is how a backup script, a cron entry or a deploy document ends up
pointing at nothing.

The host is small: **1.9 GB RAM, shared with the live POS stack.** That is why every
service in the production file carries `mem_limit: 512m`. The limits are ceilings,
not reservations — they change the failure mode from "the OOM killer picks a victim,
possibly a POS container" to "the greedy ChmabaPay container is restarted". The POS
stack is deliberately left uncapped.

### A tenant on a VPS that already runs the POS stack

The chmaba POS project is live on this machine and its edge owns host 80 and 443.
ChmabaPay is a second, independent compose project that takes **one port: 8443**.

| | chmaba (POS) | chmabapay |
|---|---|---|
| host ports | 80, 443 | **8443 only** |
| compose project | its own | `chmabapay-prod` |
| database | its own `db` | its own `db`, **not published** |
| Redis | none | none |

The project is named `chmabapay-prod` and not `chmabapay`, and that is load-bearing
rather than cosmetic. The repository-root `docker-compose.yml` already claims
`name: chmabapay`, and a compose project name is what namespaces volumes — so under
the same name, `pgdata` in both files would resolve to the **same** volume
`chmabapay_pgdata`, along with the same container names. The development stack uses
exactly that volume. On the VPS the collision would be invisible until
somebody ran the development stack there once, at which point production would
silently adopt that database — its accounts, its stores, and a test webhook endpoint
pointing at a host that does not exist. A fresh production database has to be a
property of the file, not of which stack happened to run first.

Two compose projects are isolated by default — separate bridge networks, namespaced
containers and volumes — so the only thing they can contend for is a published host
port, and there is exactly one of those. Nothing in the POS project's compose file,
nginx config or certificates is touched. An earlier plan had the two sharing one host
nginx, which would have made ChmabaPay's launch a new single point of failure for a
live revenue-generating service; that plan is gone.

### Why there is no Redis and no worker container

`deploy/docker-compose.prod.yml` runs the API with `WORKER_TRANSPORT=inprocess` and
`WORKERS_ENABLED=true`, so it drains its own queues. `redis_url` is only read when the
transport is `redis`, which is why the file has no Redis service at all.

That is the point: this stack has **no external dependency whose failure stops payment
detection**. Detection is not held in the queue — W1 re-derives what to poll from
non-terminal rows in the database — so a restart resumes rather than losing work. What
an in-process queue costs is throughput, not durability, and at launch there is no
throughput problem to solve.

The split topology (§10) is still available. It needs a `redis` service and the
`worker` service added to the production file, and both switches flipped. Add it when
a webhook fan-out waiting on a slow merchant endpoint is actually competing with the
API, not before.

### The origin port, and the Cloudflare rule that makes it work

Cloudflare proxies to this origin on **8443**. Two things have to be true in the
Cloudflare dashboard:

1. **An Origin Rule with a destination-port override.** By default Cloudflare connects
   to the origin on the port the visitor used — 443 — which on this host belongs to the
   POS project's edge. The rule matches `pay.chmaba.com` and `admin-pay.chmaba.com` and
   rewrites the destination port to 8443. Destination-port override is available on
   the **Free** plan (10 rules), so this needs no upgrade.

   This is not a theoretical misconfiguration. Before the rule existed, and with
   nothing of ChmabaPay deployed, `https://pay.chmaba.com` answered **200** with
   `<title>Chmaba | Cloud POS for growing stores</title>` — the POS site. The POS
   nginx has no `default_server` on 443, so nginx served an unrecognised `Host` from
   its first `listen 443` block, which is `chmaba.com`. An unknown hostname pointed at
   that server gets the POS app. After the rule exists but before this stack is up,
   the symptom changes to a 522 from Cloudflare while the POS project is perfectly
   healthy — which is a confusing way to learn that 8443 has nothing behind it yet.
2. **SSL/TLS mode Full (strict)**, so the Origin CA certificate is actually verified.
   `Full` would accept any certificate including a wrong one; `Flexible` would send
   plaintext to a port expecting TLS, and fail.

8443 is worth having been chosen carefully: it is on Cloudflare's list of HTTPS ports
that the proxy accepts. A port outside that list cannot be proxied without Spectrum,
which is an Enterprise product. One caveat worth knowing rather than discovering —
Cloudflare notes that caching is disabled by default for traffic on ports other than
80 and 443. Whether that affects a rule that only rewrites the **origin** port, while
the visitor's URL stays on 443, has **not** been verified here. It is not expected to
matter: this application serves payment pages, where caching HTML would be a liability
rather than a feature.

Also on the dashboard: `pay.chmaba.com` and `admin-pay.chmaba.com` must both be
**proxied** (orange cloud), and the origin's 8443 should be restricted by the host
firewall to Cloudflare's published ranges. See §8 for why that firewall rule is
defence in depth rather than the control that makes the client address trustworthy.

### Why the console is `admin-pay` and not `admin.pay`

The console was going to be `admin.pay.chmaba.com`. It cannot be, and the reason is
worth writing down because the name looks like a typo.

Cloudflare's Universal SSL certificate for this zone is:

```
subject=CN=chmaba.com
X509v3 Subject Alternative Name:
    DNS:chmaba.com, DNS:*.chmaba.com
```

A DNS wildcard matches **exactly one label**. `*.chmaba.com` therefore covers
`pay.chmaba.com` — and does **not** cover `admin.pay.chmaba.com`, which is two labels
deep. Measured against the real edge, before any of this was deployed:

```
pay.chmaba.com         →  HTTP 200, certificate SANs as above
admin.pay.chmaba.com   →  SSL alert number 40 (handshake_failure)
```

The failure is at the TLS layer, so there is no HTTP status at all — no 404, no 5xx,
nothing to grep for in an origin log. The connection simply does not complete. The
browser reports a generic privacy error.

Three ways out, and two of them are free:

| | Cost | Trade |
|---|---|---|
| `admin-pay.chmaba.com` — one label, covered by the wildcard | free | the name looks slightly odd |
| Serve the console at `pay.chmaba.com/admin` | free | one hostname, so no second SAN and no second DNS record; the console's URL is coupled to the site's |
| Keep `admin.pay.chmaba.com` and buy Advanced Certificate Manager | ~$10/month | the only way to hold a two-level subdomain on Cloudflare |

This deployment takes the first. If it is ever changed, **the certificate's SAN list
and the DNS record change with it** — a certificate covering only `pay.chmaba.com`
fails the handshake for the console, and vice versa, in the same wordless way.

### The certificate

A **Cloudflare Origin CA** certificate with **both** hostnames in its SAN, installed at:

```
deploy/certs/fullchain.pem
deploy/certs/privkey.pem
```

To obtain it — free on every plan, valid up to 15 years, trusted only by Cloudflare
(which is exactly right here, since Cloudflare is the only client and browsers never
see it):

1. Cloudflare dashboard → pick the `chmaba.com` zone → **SSL/TLS → Origin Server**.
2. On the **Origin Certificates** tab, **Create Certificate**.
3. Keep *"Generate private key and CSR with Cloudflare"*. Private key type RSA or ECC
   — either works; nginx does not care.
4. **List both hostnames**, `pay.chmaba.com` and `admin-pay.chmaba.com`. The zone apex
   and a `*.chmaba.com` wildcard are pre-filled — remove what you do not need. A
   certificate that covers only `pay.chmaba.com` will fail the handshake for the
   console, and the error names the certificate rather than the missing name.
5. Certificate Validity **15 years**.
6. Key Format **PEM** — the format nginx expects.
7. Copy **both** blocks into the two files above. **The private key is shown once.**
   Leave that page and it is gone; you would have to revoke and reissue.

Then upload them to the VPS. `deploy/certs/` is gitignored, so they travel separately
from the repository — `scp` them into the deployed checkout, and set the key
permissions so only the owner can read it:

```bash
install -d -m 0750 deploy/certs
install -m 0644 fullchain.pem deploy/certs/fullchain.pem
install -m 0600 privkey.pem   deploy/certs/privkey.pem
```

`deploy/certs/` is gitignored, and `*.pem`/`*.key` are ignored repository-wide, because
an Origin CA key is valid for up to 15 years and committing one is not something
history rewriting recovers from. nginx refuses to start without these two files —
observed, by mounting an empty directory in their place:

```
[emerg] 1#1: cannot load certificate "/etc/nginx/certs/fullchain.pem":
BIO_new_file() failed (SSL: ... No such file or directory ...)
nginx: configuration file /prodsrc/nginx.prod.conf test failed
```

That is the behaviour worth having: the failure is a named missing file at startup,
rather than a container that comes up healthy and serves a TLS error to every request.

### What production does not inherit

The production database is empty on first start, because `chmabapay-prod` is a
different project and therefore a different volume. That is the intended state, and
it is worth knowing what is *not* there: no accounts, no stores, no API keys, no
webhook endpoints, and no payments.

In particular, the development database has a webhook endpoint at
`http://host.docker.internal:9000/hook` (`webhook_endpoints.id = 1`, left over from
the signed-delivery verification). Nothing listens there. Production will not have
it, so nothing needs cleaning for launch. If you keep testing against ABA on the
development stack, disable it so the deliveries stop failing:

```sql
UPDATE webhook_endpoints SET status = 'disabled' WHERE url LIKE 'http://host.docker.internal%';
```

`disabled` rather than `DELETE`: the fan-out only targets `status = 'active'`
endpoints, so this stops the deliveries, and it keeps the two successful
`event_deliveries` rows that are the evidence the signed webhook actually landed.
Deleting the endpoint would take those with it.

### Verifying the edge before you trust it

The production edge config was validated on this machine, against stub upstreams:

- `nginx -t` on `deploy/nginx/nginx.prod.conf` — syntax ok, test successful;
- `docker compose -f deploy/docker-compose.prod.yml config --quiet` exits 0, and the
  required-variable guards fire: with `deploy/.env` empty, compose refuses with
  `required variable POSTGRES_PASSWORD is missing a value: set POSTGRES_PASSWORD in
  deploy/.env`;
- the resolved configuration publishes exactly one port, `8443 -> 443`, and mounts the
  config, the shared `api-locations.inc` and the certificate directory at the right
  container paths;
- the resolved project name is `chmabapay-prod`, with its own single `pgdata` volume —
  the check that catches the collision described above, and it did: the first version
  of this file declared `name: chmabapay` and would have shared the development
  database;
- host routing: `pay.chmaba.com` reached the website upstream,
  `admin-pay.chmaba.com` the console, and `/health` on the website host reached the
  API — so `api-locations.inc` is included and effective on both vhosts;
- an unrecognised `Host` returned **404** rather than being served the real site by a
  default block;
- `/nginx-health` on port 80 returned **200**, and every other path returned **301**.
  This is not incidental: the `return 301` is inside `location /` rather than at server
  level precisely so it cannot swallow the healthcheck, and the check fails loudly
  (nginx never reports healthy, the stack never starts) if that is ever "tidied up";
- a caller-supplied `X-Forwarded-For: 1.2.3.4` was **discarded** — the application
  received the peer address instead, closing the rate-limit bypass described in §8;
- a caller-supplied `CF-Connecting-IP` from an **untrusted** peer was **ignored**, and
  the same header from a trusted peer arrived as that value. Both halves of the
  realip behaviour were observed, using a throwaway config that trusted the local
  Docker network in place of Cloudflare's ranges;
- with an empty directory mounted where the certificate belongs, nginx refused to start
  and named the missing file. See "The certificate" above for the message.

**Deployed and verified on the real host** (2026-09-17). Everything below was run
against `163.245.204.122`, not localhost:

- the stack is at `/opt/chmabapay`, commit `92ae073`, alongside `/opt/chmabapos`;
- `db`, `api`, `landing`, `admin` and `proxy` all reached **healthy**; `migrate`
  exited **0**; the schema is at Alembic revision **`0006`** with 14 tables;
- `GET /health` answers `{"status":"ok","app":"ChmabaPay"}`;
- `landing` fetched `/v1/billing/plans` from `api` and got **200** — the frontend and
  API are genuinely wired, not merely both running;
- the origin on **8443** serves the Cloudflare Origin CA certificate for both
  hostnames, negotiates **TLS 1.3**, routes `pay.chmaba.com` to the website,
  `admin-pay.chmaba.com` to the console, `/health` to the API, and answers an
  unrecognised `Host` with **404**;
- `/nginx-health` on the proxy's port 80 returns **200** while every other path
  returns **301** — checked *inside* the container, because host port 80 belongs to
  the POS and asking for it from the host reaches the POS instead. That is the port
  isolation working as designed;
- the POS stack was not restarted: `deploy-front-1` and `deploy-api-1` up 7 days,
  `deploy-db-1` up 8 days and healthy throughout;
- memory: ChmabaPay uses ~390 MB across five containers, with ~850 MB still
  available on the 1.9 GB host.

**Redeployed to `0008`** (2026-09-18), from commit `65010c1` (the state above had
already moved on by one commit — the doc was stale, which is worth noting because
a deploy record is only useful if it is read before the next deploy). This one
carried the first **destructive** migrations: `0007` drops
`stores.owner_name/owner_phone/owner_email` and `0008` drops
`accounts.account_type/account_type_explicitly_set`. Both are column drops, so a
snapshot was taken first and checked before anything else ran:

```bash
docker compose -f deploy/docker-compose.prod.yml --env-file deploy/.env exec -T db \
  pg_dump -U chmaba -d chmabapay -Fc > /root/chmabapay-pre-0008.dump
# PGDMP magic present, and 14 TABLE DATA entries listed by pg_restore -l
```

Then `git pull --ff-only` and `up -d --build`. Verified afterwards, on the host:

- `alembic current` → **`0008 (head)`**; 14 public tables, so the drops removed
  columns and not tables;
- a query for the five dropped columns returns **nothing**;
- `migrate` exited **0** and `db`, `api`, `landing`, `admin` are healthy; `proxy`
  was not recreated, the nginx config being unchanged;
- the **POS stack is untouched**: `deploy-front-1` up 8 days, `deploy-api-1` up 8
  days, `deploy-db-1` up 9 days and healthy;
- `https://pay.chmaba.com/health` → `{"status":"ok","app":"ChmabaPay"}`; landing and
  `admin-pay` both **200**; `/metrics` and `/auth/_dev/login` still **404** from the
  public internet;
- the new error tracking is live and *armed*: `chmabapay.errors` resolves inside the
  container, `error_report_interval_seconds` reads `300.0`, an authenticated scrape
  exposes `chmabapay_errors_total`, and `OPS_TELEGRAM_CHAT_ID` is set — so an
  unhandled exception now reaches that chat rather than only the log.

> One footnote for next time: `METRICS_TOKEN` is set in production, so a scrape
> without the bearer token answers **401**, and `curl -f` treats an expected `404`
> as a failure. Both cost a minute here and neither is a defect.

**Verified through Cloudflare, on the public hostnames** (2026-09-17), after the
Origin Rule was deployed:

- `https://pay.chmaba.com` serves the website and `https://admin-pay.chmaba.com` the
  console, while `https://chmaba.com` still serves the POS — the three hostnames
  route independently;
- `/health` → `{"status":"ok","app":"ChmabaPay"}` and `/v1/billing/plans` → **200**,
  so API paths are routed at the edge;
- the four refusals hold in production: `/metrics`, `/docs`, `/redoc` and
  `/auth/_dev/login` all return **404** from the public internet. That last one is
  the important one — it is the route that mints a session for any email with no
  credential, and it is unreachable;
- `/openapi.json` returns 200, which is intended and matches the routing table;
- `/auth/google/login` returns **307** to `accounts.google.com` with
  `redirect_uri=https://pay.chmaba.com/auth/google/callback` — the exact string that
  must be registered in the GCP Console;
- the session cookie comes back `HttpOnly; Path=/; SameSite=lax; Secure`. The
  `Secure` flag is the evidence that the edge is forwarding `X-Forwarded-Proto`
  honestly; a hardcoded or absent proto would drop that flag silently;
- a browser receives Cloudflare's edge certificate (`*.chmaba.com`) over **TLS 1.3**,
  never the Origin CA certificate — which is what the Origin CA certificate is for.

**An earlier claim in this document was wrong, and this is the correction.** It said
that `pay.chmaba.com` returning 200 before the Origin Rule existed "suggests the
SSL/TLS mode may be Full rather than Full (strict)". That reasoning was invalid: the
POS origin certificate is `*.chmaba.com, admin.chmaba.com, chmaba.com,
www.chmaba.com` — a wildcard that covers `pay.chmaba.com` — so Full (strict) would
have accepted it too. The observation could not distinguish the two modes, and
nothing here can from outside. What *is* settled: the mode is not `Flexible`, because
Flexible speaks plain HTTP to the origin and this origin only accepts TLS on 8443.
Confirm the actual mode in the dashboard.

Every hostname Cloudflare proxies in this zone has a matching origin certificate —
`chmaba.com`, `www.chmaba.com` and `admin.chmaba.com` are covered by the POS
wildcard, and `pay.chmaba.com` and `admin-pay.chmaba.com` by this stack's — so
switching the zone to Full (strict) is safe for all of them. Re-check each one
immediately afterwards, and remember the setting is per-zone: it governs the POS too.

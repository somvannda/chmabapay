# Deploying ChmabaPay

The whole stack is `docker-compose.yml` at the repository root. This document is
what an operator needs: what ships, how to start it, what healthy looks like, and
which decisions in the configuration are deliberate rather than incidental.

Status: the cold-start and routing behaviour below was executed end-to-end on
Docker Desktop (Windows) and is recorded as observed output, not intent. See
"Verified, and not" at the end for the boundaries of that claim.

---

## 1. What ships

| Service   | Image                 | Listens | Role |
|-----------|-----------------------|---------|------|
| `db`      | `postgres:16-alpine`  | 5432    | Application database. Published on **55432** on the host. |
| `redis`   | `redis:7-alpine`      | 6379    | Queue backend. Published on **56379** on the host. |
| `migrate` | built from `Dockerfile` | —    | `alembic upgrade head`, runs once, must exit 0. |
| `api`     | built from `Dockerfile` | 8000  | FastAPI. Owns both auth modes and, by default, the background workers. |
| `worker`  | built from `Dockerfile` | —     | The background workers alone. Behind the `workers` profile — see §9. |
| `landing` | built from `web/Dockerfile` (`APP=landing`) | 3001 | The marketing site **and** the merchant portal. |
| `admin`   | built from `web/Dockerfile` (`APP=admin`)   | 3002 | The platform console. |
| `proxy`   | `nginx:1.27-alpine`   | 8080→80 | The only public door. |

Three deployables by default: one API, one website that carries both marketing and the
merchant dashboard, one console. `web/user` is a superseded duplicate of the landing
workspace's dashboard and is deliberately **not** built. `worker` is a fourth process
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

## 5. Routing

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

## 6. The two frontend images

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

## 7. TLS

Cloudflare terminates TLS in production and reaches this origin over plain HTTP with
the real `Host` header. The nginx `map` blocks preserve an inbound
`X-Forwarded-Proto`/`X-Forwarded-Host` and fall back to `$scheme`/`$host` when it is
absent.

They must not be replaced with a hardcoded `https`. Cloudflare sets the proto on the
way in; this hop is genuinely HTTP, so overwriting it would lie about how the request
arrived — and because the session cookie is marked `Secure` for non-localhost hosts,
that lie changes whether cookies are sent, invisibly, until someone is mysteriously
logged out.

Cloudflare's SSL mode must be **Full (strict)**.

## 8. Redeploying

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

## 9. Running the drains in their own process

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

## 10. Verified, and not

Verified by execution on this machine:

- cold start from no containers, no volumes, no cached images — all six services
  reached `healthy`, `migrate` exited 0;
- the full routing table in §5, including all four refusals returning 404 and
  `/auth/google/login` returning a correctly built `accounts.google.com` redirect;
- `/auth/_dev/login` returns 404 with **no** `Set-Cookie`;
- `docker compose config --quiet` exits 0; `nginx -t` passes;
- the reverting of the dev-login leak was confirmed by reproducing it first (307 plus
  a valid `chmabapay_session`) and re-testing after the fix.

Verified for the split topology in §9, by running it:

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

Not verified:

- a completed Google OAuth round trip. The redirect is built correctly and points at
  the configured client, but the callback needs a browser and a real consent screen.
- a real payment — **verified since** (2026-09-17). Two real 0.10 USD payments
  settled against ABA on this stack, the second delivering a signature-verified
  `payment.completed`. It needs a real ABA PayWay link and a wallet; it does **not**
  need Bakong Open API credentials, which an earlier version of this list claimed.
  See `docs/production-readiness.md` P0-2.
- anything behind Cloudflare. This stack was exercised over plain HTTP on localhost.
- **more than one draining process at once against a real Redis.** The consumer-group
  split is asserted in `tests/test_queue_redis.py` — including that two readers never
  receive the same job, and that an unacknowledged job is recovered by another worker —
  but those run against `fakeredis` locally and a real `redis:7` service in CI, not
  against a scaled-out deployment.

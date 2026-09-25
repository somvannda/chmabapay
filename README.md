# ChmabaPay

Plan for a Cambodian **Bakong KHQR payment platform** for developers — a clone of the
business model behind [cutluy.com](https://cutluy.com), built in Python.

## Model

### CutLuy baseline (confirmed from its own Terms & Privacy)

CutLuy is **not** a bank, PSP, or money transmitter. It is a *technical layer*:

- Each merchant connects their **own payment link** (their own Bakong/ABA merchant account).
- Money moves **directly from the payer to the merchant's bank account**. The platform never holds funds.
- The platform generates the KHQR, tracks status, and delivers signed webhooks.

CutLuy's operational shape is **manual and per-store**: you create a store by hand, enter its
payment link by hand, and CutLuy issues one API key **per store** plus a **per-store** webhook
endpoint/signing secret.

### Our tenant model (what we build instead)

We keep the direct-to-merchant money flow but lift the store/webhook model to a
**platform → sub-merchants** hierarchy (Stripe-Connect style):

- **Account / tenant** = a hosting platform (e.g. chmaba POS). One login, one **account-scoped
  API key**, and **one shared webhook endpoint + signing secret** covering *all* of its merchants.
- **Stores = sub-merchants** that the account **provisions automatically via our API** — the
  hosting platform's end-user just enters their own ABA Payway / Bakong link once; no manual
  store creation in our dashboard per merchant.
- Each sub-merchant's store carries **its own payment link**, so every payment we mint routes to
  **that store owner's own bank account**. Money is never pooled in the platform's account.
- Payment events go to the **account's single webhook** and are **tagged with the sub-merchant**,
  so the POS can attribute and fan out to the right store owner.

**Both customer types run on the same schema** — the only difference is store count and who
provisions stores: an individual merchant is simply an account with one store (CutLuy parity:
dashboard signup, their own link, their own key/webhook), while a POS/hosting provider is an
account with many API-provisioned stores (shared key + shared webhook/secret). See
`docs/architecture.md` §6.

```
 Customer's bank app ──scan KHQR──► Bakong switch ──credit──► THAT store owner's own bank account
                                        │                          (link belongs to the sub-merchant)
                              status feed (pending/scanned/paid/expired/failed)
                                        │
                     OUR PLATFORM (tenant model: account + auto-provisioned stores)
                                        │  one shared webhook + signing secret,
                                        │  each event tagged with the sub-merchant
                                        ▼
                  Hosting platform (chmaba POS) routes the event to the right store owner
```

```
Customer bank app  --scan KHQR-->  Bakong switch  --credit-->  Merchant's own bank account
                                        |
                                   status feed (pending / scanned / paid / expired / failed)
                                        |
                                    OUR PLATFORM  --webhooks-->  Developer's server
```

## Key implications

- No money custody => no PSP / money-transmitter licence is implied by the flow itself.
  (Your own lawful-business, KYC and contractual responsibilities still apply; see
  `docs/roadmap.md` — Compliance.)
- The moat is **not** QR generation or webhooks. It is **reliable, real-time confirmation
  of an incoming KHQR credit** against each merchant's account. `docs/detection.md` covers this.
- In the tenant model this multiplies: we report status for **many sub-merchant links**, and
  money lands in each one individually. Whether one set of platform credentials can check
  transactions for *all* connected merchant links (on-behalf-of) is the Phase 0 question
  (`docs/roadmap.md` Phase 0, `docs/detection.md` §7).
- Every unknown about your live Bakong credentials must be resolved first
  (Phase 0 spike) before building the money path.
- CutLuy's per-store manual flow, per-store API keys, and per-store webhooks are **replaced** by:
  account-level API key(s) + API-driven store provisioning + one shared webhook/signing secret,
  with events attributed per sub-merchant. See `docs/architecture.md` §6 and `docs/api.md`.

## Documents

| Doc | Contents |
| --- | --- |
| [`docs/architecture.md`](docs/architecture.md) | System overview, components, data flow |
| [`docs/data-model.md`](docs/data-model.md) | Postgres schema (entities, states, keys) |
| [`docs/api.md`](docs/api.md) | Public REST API spec (mirrors CutLuy's surface) |
| [`docs/detection.md`](docs/detection.md) | How we confirm a payment happened (the critical part) |
| [`docs/roadmap.md`](docs/roadmap.md) | Phased milestones, spike checklist, compliance notes |

## Stack (chosen)

Python backend: **FastAPI** + SQLAlchemy 2.0 (async) + PostgreSQL + Redis.
Async background workers (job queue) for polling/status handling and webhook delivery.
Frontend: **Next.js 14 App Router** in a pnpm workspace under `web/` — `landing` (marketing +
merchant dashboard, :3001) and `admin` (platform console, :3002), sharing design tokens
via the `@shared/*` path alias.

> Status: **built and running locally.** Backend plus the web apps are implemented; see
> "Phase 1 POC — run it locally" below and the per-app READMEs under `web/`.

---

## Running it locally

Status: **working vertical slice**. The real ABA PayWay path is implemented and was proven
against live money on 2026-09-17 — two settled payments, the second with a
signature-verified webhook (`docs/production-readiness.md` P0-2).

There are exactly two ways to run this, and **pick one; do not mix them.** The
application refuses to boot on a mix (see below), because the two mistakes it covers
both *succeed* at connecting and then read the wrong database.

### The Docker stack — the way to run it

Everything in containers, behind the nginx edge on **http://localhost:8080**; the console
is on the `admin.localhost` host. No host process is involved.

```bash
cp deploy/.env.local.example deploy/.env.local   # once; then fill in JWT_SECRET_KEY
docker compose --env-file deploy/.env.local up -d --build
```

`--env-file deploy/.env.local` is not optional — without it Compose reads the repository
root `.env`, which is the *host* file, and a `CHMABAPAY_RUNTIME` guard in the compose file
refuses to render. `deploy/.env.local` keeps the fake Bakong rail (`/_dev`) **on** so a
payment can be settled locally, and keeps the production Telegram and Resend credentials
**out**, so a local test cannot page the ops chat or email a real merchant.

`http://localhost:8080` is the website and the merchant dashboard; the API is also on
:8000 for `curl`. See `docs/deploy.md` §3 for the startup order and what healthy looks
like.

### On the host — for the test suite, and for one-off CLI work

No container in the path: SQLite, the in-process queue, and the API on :8000. This is what
`uv run pytest` uses. It is **not** the container's database, and pointing it at one is the
mix the guard refuses.

```bash
uv sync --all-groups
uv run python -m chmabapay.cli bootstrap          # seed tenant + stores + keys
uv run uvicorn chmabapay.main:app --port 8000     # the platform API
```

Its environment is the repository-root `.env`, which sets `CHMABAPAY_RUNTIME=local` and a
SQLite `DATABASE_URL`. Note that **an exported shell variable beats that file** — if a host
run seems to ignore `.env`, check `$env:DATABASE_URL` first. That is not hypothetical: the
development machine this was written on had `DATABASE_URL`, `REDIS_URL` and
`WORKER_TRANSPORT=redis` left exported from an earlier session, so every host process
started from that shell used the container's database regardless of the file.

### Why mixing is refused

`assert_runtime_matches_configuration` (`src/chmabapay/config.py`) refuses to start when
the configuration belongs to the other runtime. The two failures worth knowing:

- a container given `localhost:55432` resolves it to *itself* and answers from a second,
  empty database — every query succeeds, against the wrong rows;
- a host process given `localhost:55432` reaches the stack's published port and reads the
  same rows the containers do — so the host and the stack disagree about the same data,
  and neither one errors.

`deploy/.env.local` and the root `.env` are therefore not interchangeable, and the failure
is a boot refusal naming the variable and the file to use. `docs/deploy.md` §2 has the
full picture.

### The dev rail

Dev routes under `/_dev` are the fake Bakong rail — **never enable them outside
development**. `ENABLE_DEV_GATEWAY` defaults to off; the Docker stack turns it on from
`deploy/.env.local`, the host from `.env`, and `deploy/docker-compose.prod.yml` hardcodes
it off. Store provisioning API: `POST /v1/stores`, `PUT /v1/stores/{id}/link`.

### Running the tests

```bash
uv run pytest        # ~35 min on Windows: SQLite default, rebuilds the schema per test
```

Point it at a Postgres `chmabapay_test` database — the same thing CI does — and the
same suite takes about **2.5 minutes**:

```powershell
$env:CHMABAPAY_TEST_DATABASE_URL = "postgresql+asyncpg://postgres:postgres@localhost:5432/chmabapay_test"
uv run pytest
```

Two things that are easy to get wrong:

- **`uv run pytest`, never `python -m pytest`.** `test_migrations.py` shells out to
  `python -m alembic`, and `pythonpath = ["src"]` applies only to the pytest process, so
  the subprocess dies with `ModuleNotFoundError: No module named 'chmabapay'` and two
  migration tests fail for reasons unrelated to the code.
- **The test database name must contain `test`.** `tests/conftest.py` refuses anything
  else, so an ambient `DATABASE_URL` cannot aim its destructive fixtures at real data.

The speed difference is not the tests: SQLite on Windows spends ~13 s per test dropping
and recreating every table, which dwarfs the assertions. That is why CI runs Postgres.

Two `live` tests reach the real ABA PayWay rail and are excluded by default. They mint
genuine checkout sessions (no money moves) and need a real link:

```powershell
$env:CHMABAPAY_LIVE_PAYWAY_LINK = "https://link.payway.com.kh/<your-slug>"
uv run pytest -m live
```

**Run these before a release.** They had been silently broken since they were written —
not failing, because nothing executed them — and were only found while settling a real
payment. A test that never runs is not coverage.

### Implemented in the POC
- Tenant model: account (POS) → auto-provisioned sub-merchants → per-store payment links.
- Account-scoped keys (provision + charge any store) and store-scoped keys (one store only).
- `POST/GET /v1/payments` + list, hosted checkout `/pay/:id`, idempotency. The QR window
  is **the rail's, not ours**: ABA returns `expire_in_sec: 180` for a hosted checkout and
  we mirror it, so the 5-minute `checkout_ttl_seconds` applies only to codes we build
  ourselves. A code that lapses is withdrawn (`410`) and replaced via
  `POST /v1/payments/{id}/reissue`, with lineage kept.
- One **shared** account-level webhook + signing secret; events tagged with the sub-merchant.
- Outbox (events written atomically with state change) + retry delivery worker, HMAC-SHA256
  signatures (`t=...,v1=...`), store-scoped endpoint override.
- State machine pending/scanned/paid/expired; expiry sweep.

### Not yet (deliberately)
- Nothing on the confirmation path. The offline KHQR encoder exists and is pinned against
  a real captured ABA payload (`tests/test_khqr.py`), but it is **not payable** — nothing
  on ABA's side has a record of a code we build, so wallets answer "QR not found". Only an
  ABA-issued code can be both paid and confirmed, which is what `hosted_qr` defaults to for
  a PayWay link. `BAKONG_API_TOKEN` is therefore not needed for the live path; it would only
  matter if an offline code ever had to be reconciled against a ledger.
- Refunds, reversals and partial payments. A settled payment is treated as terminal.
- A push callback from ABA. Detection is poll-only: the orphan sweep runs every 30s, so
  that interval is the whole detection budget (see `docs/bakong-gateway-notes.md` §4).
- SaaS sub-merchants (BRD §6) — no sub-merchant surface exists in the API or the schema.
- Admin impersonation (BRD §11.2).
- Multi-replica deployment, properly. The queue can be shared now — `WORKER_TRANSPORT=redis`,
  with the drains optionally in their own process; see `docs/deploy.md` §9 — so replicas
  no longer each hold a private one. Rate-limit counters are still per-process, so N
  replicas mean N times the configured allowance.

Deployment itself is no longer a gap. `Dockerfile`, `web/Dockerfile`, `docker-compose.yml`,
`deploy/nginx/` and `.github/workflows/` exist, `docs/deploy.md` covers running the stack, and
rate limiting plus the audit trail landed in P1.


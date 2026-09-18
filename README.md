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

## Phase 1 POC — run it locally

Status: **working vertical slice**. This walkthrough uses the dev rail (`/_dev`, fake
Bakong); the real ABA PayWay path is implemented and was proven against live money on
2026-09-17 — two settled payments, the second with a signature-verified webhook
(`docs/production-readiness.md` P0-2). SQLite default, no external services needed here.

```bash
uv sync --all-groups          # install deps into .venv
uv run python -m chmabapay.tools.sink &   # terminal 1: local webhook sink on :9000
uv run python -m chmabapay.cli bootstrap  # terminal 2: seed tenant + stores + keys
# note the account API key, a store id, and (if WEBHOOK_SINK_URL was set) the signing secret
uv run uvicorn chmabapay.main:app --port 8000   # terminal 3: the platform API
```

Set `WEBHOOK_SINK_URL=http://127.0.0.1:9000/hook` in your environment **before** bootstrap if you
want the shared account-level webhook created automatically (it prints the signing secret).

Then simulate the whole loop:

```bash
# 1. create a payment against one sub-merchant (money would go to THAT store's link)
curl -X POST http://127.0.0.1:8000/v1/payments \
  -H "Authorization: Bearer ck_live_<ACCOUNT_KEY>" \
  -H "Content-Type: application/json" \
  -d '{"amount": 1.50, "reference_id": "order_1", "store": "<st_...>"}'
# 2. open the returned checkout_url in a browser (QR card + live status)
# 3. simulate the customer paying on the fake rail
curl -X POST http://127.0.0.1:8000/_dev/payments/<payment_id>/pay
# 4. watch the shared webhook arrive at http://127.0.0.1:9000/hook
```

Dev routes under `/_dev` are the fake Bakong rail — **never enable outside development**
(`ENABLE_DEV_GATEWAY=false`, which is the default; the local `.env` turns it on explicitly).
Store provisioning API: `POST /v1/stores`, `PUT /v1/stores/{id}/link`.

For the backing services only, run `docker compose up -d db redis` and point `DATABASE_URL` at
`postgresql+asyncpg://chmaba:chmaba@localhost:55432/chmabapay`. The compose database is published
on **55432**, not 5432, so it cannot collide with a Postgres already running on the host. Running
`docker compose up -d` without naming services starts the entire stack instead — API, both
frontends and the nginx edge on :8080; see `docs/deploy.md`.

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


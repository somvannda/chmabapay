# Architecture

## 1. Goal

Replicate CutLuy's developer-facing product and go beyond it with a **tenant (sub-merchant) model**:

- Account = hosting platform (e.g. chmaba POS). One account-scoped API key; it can **auto-provision
  stores (sub-merchants) via API** instead of CutLuy's manual per-store dashboard flow.
- Each store owns **its own payment link** (ABA Payway / Bakong merchant account), entered once by
  the sub-merchant.
- `POST /api/v1/payments` (targeting one of the account's stores) -> returns a `qr_string` + hosted
  `checkout_url` per payment. Money lands **directly in that store's own bank account**.
- Platform reports status in real time and delivers **signed webhooks to ONE account-level
  endpoint with one signing secret**, each event tagged with the sub-merchant so the POS can route.

Money flow is **direct-to-merchant** (never pooled, never held). We confirm credits, we never move them.

## 2. High-level components

```
                         ┌───────────────────────────────────────────────┐
   Developer's server    │                  OUR PLATFORM                  │
   (merchant app)        │                                               │
        │  HTTPS /api/v1/*   │   ┌──────────┐   ┌────────────┐  ┌─────────┐ │
        ├──────────────► │   │ API edge │──►│ App logic  │  │ Postgres│ │
        │   API key      │   │ FastAPI  │   │ (payments, │  │ (source │ │
        ◄───────────────► │   │          │   │  stores,   │  │  of     │ │
        │   checkout_url  │   └──────────┘   │  keys,     │  │  truth) │ │
        │                 │        │         │  quota)    │  │         │ │
        │ webhooks        │   ┌────▼─────┐   └─────┬──────┘  └────┬────┘ │
        ◄──────────────── │   │ QR       │         │              ▲      │
        │  HMAC-signed    │   │ renderer │   ┌─────▼─────┐        │      │
        └─────────────────┘   │ + SVG    │   │ Workers   │────────┘      │
                              └──────────┘   │ (ARQ)     │               │
                                             │  • status │  Redis queue  │
                                             │    poller │               │
                                             │  • webhook│               │
                                             │    sender │               │
                                             │  • expiry │               │
                                             └─────┬─────┘               │
                                                   │   BakongGateway     │
                                                   │   (adapter)         │
                                             ┌─────▼───────────────────┐ │
                                             │  BAKONG / your bank     │ │
                                             │  merchant API           │ │
                                             │  (your live creds)      │ │
                                             └─────────────────────────┘ │
                              Customer pays with their own banking app   │
                              → money lands in the MERCHANT's account     │
                              └───────────────────────────────────────────┘
```

### 2.1 Public surface (FastAPI)
- `POST /api/v1/payments`, `GET /api/v1/payments/:id`, `GET /api/v1/payments` — create/read payments.
- Store (sub-merchant) provisioning: `POST /api/v1/stores`, link management, `GET /api/v1/stores…` —
  lets a hosting platform auto-create a store the moment its end-user supplies a payment link.
- Hosted checkout page `/pay/:id` (public, no auth) — renders the KHQR card and polls status.
- Dashboard app (later): manage account, stores, webhook endpoints, API keys, billing.
- The `/api/render/khqr/:payload.svg` free renderer (same as CutLuy) is optional, low-effort, good marketing.

### 2.2 Internal services (separate processes, one codebase)
| Process | Responsibility |
| --- | --- |
| `api` | FastAPI app: REST + checkout + dashboard endpoints |
| `worker` | ARQ/asyncio workers: due-polling, webhook dispatch/retry, expiry jobs, reconciliation sweep |
| `scheduler` | Periodic jobs via Redis streams (or a single arq worker with cron) |

All share one DB and one Redis. Deploy as containers.

## 3. Request lifecycle

1. Developer calls `POST /api/v1/payments {amount, reference_id, store}` with an API key.
2. API auth resolves the key to its account. The **store** is chosen by the `store` param
   (or implied when a store-scoped key is used). The store's own payment link supplies the
   merchant's destination account + credentials — money goes to that store, not the account.
3. Payment row inserted `status=pending`, unique `id` (public token). KHQR payload is generated
   (offline TLV builder or Bakong create-QR — see Phase 0) with:
   - merchant account id (money goes straight to the merchant),
   - `amount`, `currency=USD`, merchant name/location from the store,
   - a **unique bill/reference** for this payment (enables matching later),
   - expiry timestamp (≈ 5 min) matching `expires_at`.
4. Return `{id, status, amount, qr_string, checkout_url, expires_at}`.
5. Customer opens checkout (or developer renders QR themselves) and pays from their bank app.
6. Bakong status feed (poller or push) reports the outcome to the `worker`, which:
   - transitions the payment to `scanned` / `paid` / `expired` / `failed`,
   - on `paid`: records the Bakong transaction reference, increments the plan quota,
   - enqueues the signed webhook event.
7. Webhook sender POSTs to each enabled endpoint with retries/backoff (max 8), log every attempt.

## 4. Correctness principles (apply throughout)

- **DB is source of truth**; Redis only queues work. Workers must be safe to re-run
  (idempotent state transitions via conditional `UPDATE ... WHERE status='pending'`).
- **Outbox pattern**: webhook events are written to the DB in the same transaction as the
  payment state change; a worker publishes them. Never lose an event on crash.
- **Idempotency**: create-payment honours `Idempotency-Key`; webhooks carry stable event ids.
- **One authoritative status transition**, guarded by row locks / atomic updates. A payment
  may go `pending → scanned → paid` or `pending → expired`. Terminal states are immutable.
- **Amount matching is exact** (cents), never fuzzy, when confirming credits; see detection.md
  for mismatch policy (under/over-payment).

## 5. Security

- API keys stored as SHA-256 hash only; full key shown once (`ck_live_…` — live mode is the
  only mode that can be minted; see decision D-2).
- Webhook signatures: `X-...-Signature: t=<ts>,v1=<hmac>` HMAC-SHA256 over `t.<rawBody>`
  with a per-endpoint secret; constant-time compare; reject stale `t`.
- Store secrets (Bakong creds per merchant) encrypted at rest (KMS/AES-GCM envelope), never logged.
- Public checkout is read-only by payment token; never exposes keys or merchant Bakong secret.

## 6. Multi-tenancy

One uniform model supports **both** customer types — the only difference is store count and who
provisions stores:

- **Individual merchant (CutLuy parity):** an account with a single store. The merchant signs in,
  adds their own link in the dashboard, and uses either an account-scoped or a store-scoped key
  plus their own webhook endpoint. Nothing new needed.
- **Hosting platform / POS provider (chmaba POS):** the same account shape with many stores,
  auto-provisioned via `POST /api/v1/stores`, an account-scoped key, and one shared webhook + signing
  secret; events carry the `store` for routing.

```
Account (hosting platform: chmaba POS)
├── account-scoped API key(s)          ── authenticate + provision
├── webhook endpoint + signing secret   ── SHARED across all its merchants
└── Stores (sub-merchants, auto-provisioned via API)
    ├── store A  → payment link A   (money → owner A's account)
    ├── store B  → payment link B   (money → owner B's account)
    └── … each store may optionally hold its own scoped API key + own webhook secret
```

Rules:
- **Provisioning by API**: an account-scoped key can create stores and attach/verify their
  payment links. No manual per-merchant dashboard steps (unlike CutLuy).
- **Shared webhook**: endpoints and signing secrets live at the **account** level; a payment event
  for any of the account's stores is delivered there and carries the `store` so the platform can
  route it to the right store owner. Per-store endpoints/secrets remain supported for delegation.
- **Money is per-store, never pooled**: the destination comes from the specific store's payment
  link at payment time. The account (platform) never receives the sub-merchant's funds.
- **Keys**: account-scoped keys act on any store the account owns (create payments, provision
  stores). Store-scoped keys (CutLuy parity) act only on their own store and are optional.
- **Quota is pooled per account** (all its stores), counting only `paid` payments over the last
  30 days. Enforced at create time (`402 quota_exceeded`). Per-store quota caps are a later option.
- See data-model.md for the exact tables and api.md for the provisioning endpoints.

## 7. Non-goals for v1

- Settlement/payouts (money never touches us).
- Refunds, disputes, receipts (merchant's responsibility, per the CutLuy model).
- KHR-currency payments (v1 is USD-only, like CutLuy). KHQR supports currency flag; easy later.
- Recurring billing / cards. Only KHQR scan-to-pay.

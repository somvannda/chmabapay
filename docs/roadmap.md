# Roadmap & milestones

## Phase 0 — Credential spike (do FIRST, gate everything after)

No product code. Answer the unknowns in `docs/detection.md` §7 using your live Bakong creds
in a scratch script against the real/sandbox API. Deliverable: `docs/bakong-gateway-notes.md`
with concrete endpoint shapes, plus a decision:

1. Credential scope: one account vs. many (on-behalf-of)? Determines whether one merchant can
   have many stores each with their own destination account, or whether we onboard each
   merchant's own creds.
2. KHQR creation: API generate vs. offline TLV encoder (port NBC JS SDK; Python encoder +
   test vectors from known-good payloads).
3. Confirmation: check-transaction endpoint (Strategy B) and/or push (Strategy A) and/or
   transaction-history (Strategy C). Record request/response verbatim.
4. Real test payment → measure detection latency end-to-end.

**Success criterion:** we can, with our creds, (a) create a dynamic KHQR that pays a merchant
account we control, and (b) programmatically learn it was paid within seconds.

> If your creds only cover ONE account that is yours, then the CutLuy "many stores, each with
> their own payment link" model only works if each merchant supplies their own Bakong creds to
> us. Phase 0 decides this. Do not assume.

## Phase 1 — Vertical slice POC (mock gateway, no real money)

Prove the loop with a `FakeBakongGateway` so UI/API/webhooks are built and testable before the
real rail is wired.

- FastAPI skeleton; Postgres schema (data-model.md) + migrations (Alembic).
- `POST /v1/payments` → dynamic KHQR via offline encoder (Phase 0 output) or stored mock → row.
- Hosted checkout page `/pay/:id` rendering the branded KHQR card + polling; success/failure redirects.
- Webhook outbox + delivery worker with HMAC signing and retries (send to a public test sink).
- Expiry worker. Atomic transitions + idempotency (replay same create → same id).
- `FakeBakongGateway`: a test endpoint that flips a payment to `paid` so we can E2E the whole path.
- Tests: pytest, unit + E2E. Lint: ruff. Formatting: black. Type: mypy/pyright.

**Exit criteria:** a payment created through the API shows a QR, gets "paid" via the fake rail,
fires a verified webhook, appears in dashboard list. All under automated tests.

## Phase 2 — Real gateway, single merchant (go live privately)

- Implement the real `BakongGateway` per Phase 0 findings (Strategy B primary, C backstop).
- Secrets vaulting for merchant creds; key mgmt; encrypted at rest.
- Live sandbox first, then a single real store = yourself (dogfood) collecting a real KHQR payment.
- Reconcile sweep + orphan credits view; alerting (email/Telegram).
- Quota/billing v1 minimal: plan tiers enforced at create time (billing UI later).
- Dashboard: auth (Google), stores, links, API keys, webhook endpoints, payments list, events log.

**Exit criteria:** you (one merchant) accept a live Bakong KHQR payment through your own platform
and receive the webhook. Reliable over a few days of real traffic.

## Phase 3 — Multi-merchant + hardening

- Onboard N real merchants; each connects their own store + payment link per the Phase 0 model.
- Hardening: rate limiting, idempotency soak, webhook signature fixtures, failure drills,
  load test creates (target >60/min/key), observability (structured logs, metrics, tracing).
- Admin console for suspension/closed states, audit log UI.
- Legal texts (Terms/Privacy per our model — we're software + status reporting, not a PSP),
  onboarding flows, support.

**Exit criteria:** sustained external merchants accepting payments; dashboards/latency meet targets.

## Phase 4 — Growth features (nice-to-haves, CutLuy parity)

- Free SVG KHQR-card renderer endpoint + public marketing page.
- Telegram payment alerts per store.
- Test-mode API keys + separate sandbox stores.
- Public SDK examples (Python/Node/PHP) and docs site.
- Billing self-serve paid with KHQR (dogfood). Optional KHR currency.

## Cross-cutting notes

### Compliance (not legal advice)
- CutLuy's model = software + status reporting; money moves merchant↔payer directly, so there is
  no money custody and no money-transmission in our flow. Confirm with a Cambodian lawyer
  nonetheless: "as-is, lawful business, merchant-responsible-for-refunds/disputes" framing matches
  CutLuy's Terms, but your contract with merchants must be explicit that you never hold or move funds.
- Merchant due diligence (KYC/KYB) is your own legal duty, not a platform feature — your merchant
  agreement must be explicit about lawful-business use only, with no
  gambling/ML/prohibited goods clauses. Mirror CutLuy's restricted-business list.
- You will store per-merchant Bakong credentials: secure them (vault + envelope encryption),
  define data-retention, and include in a privacy policy.
- NBC/Bakong may have merchant-API terms about acting for third parties; **check on-behalf-of
  usage is permitted** (Phase 0). This is the single biggest legal/technical risk.

### Open decisions to confirm with the user
- Frontend framework for dashboard + checkout (recommend a small React SPA via Vite; Python-only
  option: server-rendered Jinja/HTMX — decide in Phase 1).
- Hosting/region (latency to Bakong APIs; e.g., Singapore/Cambodia) and object storage for logos.
- Payment IDs: CutLuy-style 22-char token (recommended).
- Real-time TTL: 5-minute QR expiry (CutLuy parity) vs configurable.

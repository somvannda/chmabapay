# Payment detection — the critical part

> This is the document that decides whether the product works. Read it before writing code.

## 1. The problem

A KHQR is just a string of text. When a customer scans it, money travels
payer-bank → Bakong switch → **merchant's** bank account. We never see the money.
Yet our whole product promise is *"your webhook fires the moment it's paid."*

So: **how do we know an incoming credit happened — for an account that isn't even ours?**

CutLuy's own privacy policy answers it obliquely:

> "We receive a transaction reference and a status (pending, scanned, paid, expired, failed)
> **from our banking partner**" ... "To create payments, **poll their status**, and deliver signed webhooks."

Translation: CutLuy has an **arrangement with a bank/Bakong participant** that hands it a
status feed for KHQR payments made against merchants on that rail. The confirmation layer is
the entire moat. We must secure an equivalent feed, or build the next best thing.

## 2. What we know about the rail

From the public NBC KHQR material (the `bakong-khqr` SDK published by NBC at
gitlab.nbc.gov.kh) and merchant practice:

- KHQR generation is **offline**: a TLV/EMV payload containing merchant account id
  (bakong id, e.g. `devit@abaa`), merchant name, city, MCC, currency, amount, an optional
  unique **bill number / reference**, and an **expiry timestamp** (required for dynamic QRs
  with an amount).
- "Dynamic KHQR" = amount + expiry embedded at scan time → we can mint a unique QR per payment.
- Payer apps are all Bakong member banks/wallets; the QR is interoperable.
- What is NOT public/open: a push notification to the *merchant* when a credit lands, and
  the exact shape of your credentials' check-transaction capabilities. **That is Phase 0 work.**

## 3. Detection strategies (ranked)

Pick the best that your live credentials actually expose. Implement as one `BakongGateway`
adapter so we can switch/augment later without touching the rest of the system.

### Strategy A — Push / webhook from Bakong or your bank (ideal, matches CutLuy)
- You receive an event (or poll only as backstop) that carries transaction reference + status
  for a KHQR paid to a merchant account under your arrangement.
- Latency: seconds. Supports the `scanned → paid` nuance.
- **Requires**: Bakong/bank onboarding where you register webhook endpoints, or a
  participant relationship. Confirm in Phase 0.

### Strategy B — Per-payment check-transaction polling (most likely with merchant API creds)
- After generating each dynamic QR (unique bill number + amount), poll the Bakong API's
  check-transaction endpoint with that payment's QR/reference.
  Bakong returns the transaction status once a matching payment exists.
- Poll every ~2–3 s for the payment's 5-minute life → still feels real-time to a user.
- Best-effort `scanned` if the API distinguishes "transaction initiated/confirming" from "completed".
- **Requires**: an endpoint reachable with your creds that answers per-transaction status.
  Exact name/request shape = Phase 0 finding.

### Strategy C — Account transaction-history reconciliation (fallback / manual ops)
- List the merchant account's incoming transactions (via your API access) and match credits
  to open payments by (amount, date-window, bill reference) — but **exact amount + unique bill
  reference matching only**, because two same-amount payments are indistinguishable otherwise.
- Runs on a sweep every N seconds; supports `paid` reliably, but `scanned` may be unavailable.
- Also the correct **safety net** for A/B: a reconcile sweep that re-checks everything marked
  paid and flags anything missed (e.g. webhook down, poller bug).

**Recommended default:** build Strategy B as the primary path with a small reconcile sweep
(Strategy C) as backstop, then upgrade to Strategy A when the partner relationship exists.

## 4. The matching problem (design it now)

Money has no pointer back to our payment row unless we encode one. Encode uniqueness in the QR:

1. Every payment mints a **unique dynamic KHQR** (per-payment bill number, amount, expiry).
2. Confirmations are only accepted when they match **exactly**:
   - credited amount == payment amount (cents), **and**
   - payment reference/bill number == ours, **and**
   - timestamp within the payment's validity window, **and**
   - destination account == the store's merchant account id.
3. Do **not** do fuzzy "any credit to this account = this payment." Duplicates/over/under
   payment are handled explicitly:

| Case | Policy |
| --- | --- |
| Duplicate full payment after `paid` | Record as `paid` only once; log the second credit; do NOT fire `payment.completed` twice. Surfacing duplicates to the merchant is their refund problem (we never hold funds). |
| Under-payment (less than amount) | Do not mark paid. Treat as unmatched; alert merchant via dashboard/log. QR is still open/expiring. |
| Over-payment (more than amount) | Accept and mark paid (customer intends to pay); note excess in payment record + webhook metadata. Merchant refunds difference. |
| Credit outside a payment's window / no open payment | Log as orphan; expose in dashboard reconcile view. |
| Two open payments, same amount, same account | Differentiated by unique bill reference in the QR → no collision. |

## 5. Payment state machine

```
                 scan/confirm                     paid
   pending ────────────────► scanned ──────────────────► paid (terminal, fires payment.completed)
      │                        │
      │ ttl (≈5 min)           │ ttl
      ▼                        ▼
   expired (terminal)       expired (terminal)        failed (terminal)
```

- `pending`: created, QR not yet scanned.
- `scanned`: QR scanned, customer confirming in their banking app (only if feed provides it;
  otherwise we go straight to `paid`).
- `paid`: terminal. Fires `payment.completed`, counts toward quota, stores Bakong txn reference.
- `expired`: terminal. Fires `payment.expired`. Expiry is driven by a scheduler that runs
  `UPDATE ... SET status='expired' WHERE status IN ('pending','scanned') AND expires_at < now()`
  — never trust a client to tell us it expired.
- `failed`: reserved for a confirmed failure from the rail; otherwise expire.

## 6. Deliverables that depend on this doc

- DB fields on `payments`: `bakong_ref`, `qr_string`, `bill_number`, `scanned_at`, `paid_at`,
  `gateway_status_raw`, `matched` flags. See data-model.md.
- `BakongGateway` interface (create-qr, check-payment, handle-push) with pluggable impls.
- A reconcile/sweep worker + orphan view in the dashboard.
- Deterministic, atomic transitions (no double-fire) — architecture.md §4.

## 7. Phase 0 spike checklist (answers we must get before coding)

With your live Bakong merchant API creds, verify against the real/sandbox API:

1. **Credential scope** — do your `client_id`/`secret` act on one merchant account only, or
   can they act *on behalf of* many merchant accounts (CutLuy needs many stores)?
2. **KHQR generation** — does the API expose a create-QR endpoint, or must we generate the
   TLV payload offline (port the NBC JS SDK; add a Python encoder + test vectors)?
3. **Confirmation endpoint(s)** — exact request/response for checking a transaction by QR or
   reference; does the response distinguish `scanned` (initiated) from `paid` (completed)?
4. **Push option** — can we register a webhook/callback for incoming KHQR payments?
5. **Reconciliation** — can we list a merchant account's incoming transactions (Strategy C)?
6. **Latency** — for a real test payment, how quickly does a check show `paid`?
7. **Rules** — bill-number charset/length limits, minimum/maximum amounts, USD support on QR.

Record the findings in `docs/bakong-gateway-notes.md` (create it during the spike) and pick the
primary strategy. **Do not build the payment path until 1–3 are answered.**

# ChmabaPay Business Requirements Document (BRD)

> **Version:** 1.0  
> **Date:** 2026-09-10  
> **Goal:** Build a Khmer KHQR Payment Gateway (CutLuy competitor) with 2 account types, richer KHQR generation API, end-to-end settlement detection, admin panel with CutLuy-style subscription plans, and a SaaS Sub-Merchants white-label feature.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Account Model (2 Types Only)](#2-account-model-2-types-only)
3. [Plans & Pricing (CutLuy Parity + SaaS Extensions)](#3-plans--pricing-cutluy-parity--saas-extensions)
4. [The Golden Resolution Rule (Scope Inheritance)](#4-the-golden-resolution-rule-scope-inheritance)
5. [User Story 1 — Individual Account (Sokha the Noodle Vendor)](#5-user-story-1--individual-account-sokha-the-noodle-vendor)
6. [User Story 2 — Business Account (KhmerPOS — Billing + SaaS White-label)](#6-user-story-2--business-account-khmerpos--billing--saas-white-label)
7. [Dashboard UX — Navigation & Page-by-Page Specification](#7-dashboard-ux--navigation--page-by-page-specification)
8. [Data Model Changes vs. Current Code](#8-data-model-changes-vs-current-code)
9. [Operational Flows (Diagrams) — End-to-End Payment + Detection + Webhook](#9-operational-flows-diagrams--end-to-end-payment--detection--webhook)
10. [API Surface — Required Additions vs. Existing](#10-api-surface--required-additions-vs-existing)
11. [Admin Panel (Platform Owner) — Exact Spec](#11-admin-panel-platform-owner--exact-spec)
12. [Gap Filling — Unsounded-but-Required Capabilities](#12-gap-filling--unsounded-but-required-capabilities)
13. [Implementation Order — 3 Milestones with Working Demos](#13-implementation-order--3-milestones-with-working-demos)

---

## 1. Executive Summary

ChmabaPay is a **white-label Khmer KHQR Payment Facilitator Gateway**. It supports two account types:

| Account Type | Who | Core Capability |
|---|---|---|
| 🧑 Individual | Solo vendor / freelancer / 1-5 stores | Per-store scoped API keys + webhooks; paste ABA PayWay link or bank account → accept KHQR payments |
| 🏢 Business | Private company LTD / POS platform / PSP reseller / SaaS | **Shared** account-wide keys + webhooks out of the box. On **Scale/Enterprise** plans: unlock Sub-Merchants (let *your* customers input their own ABA link or bank account; money goes to them directly) + white-label checkout |

**Settlement detection runs on 3 layers today** (kept from existing implementation; no scope change):

1. **SSR status poll of the ABA PayWay link page** (#1 priority for ABA-linked payments)
2. **Bakong Open API MD5 cascade search** (#2 fallback for all payments)
3. **Full Bakong receipt cascader** (7-tier lookup for rich transaction matching)

**Zero PCI scope** (QR-only, no card PANs ever touch our infrastructure).

---

## 2. Account Model (2 Types Only)

Only **two** values exist in the `account_type` enum. Business LTD vs. SaaS is **not** a type — it is a **plan tier + feature gate toggle** within the same Business account type.

```
                                  ┌─────────────────────────────┐
                                  │     2 CHMABAPAY ACCOUNTS    │
                                  └─────────────────────────────┘
              ┌─────────────────────────┐                 ┌───────────────────────────────┐
              │ 🧑 INDIVIDUAL           │                 │ 🏢 BUSINESS (ONE TYPE)         │
              │                         │                 │  Plan-gated feature split:     │
              │  Default Plan: Starter  │                 │  ┌──────────┬────────────────┐ │
              │  (free)                 │                 │  │ Growth   │ Scale / Ent    │ │
              │                         │                 │  │ ($29)    │  ($99+/custom)│ │
              │  • Keys = Store-scope   │                 │  ├──────────┼────────────────┤ │
              │    ONLY                │                 │  │ • Shared │ • Shared keys  │ │
              │  • Webhooks = Store-   │                 │  │   keys ✅│   ✅           │ │
              │    scope ONLY          │                 │  │ • 50     │ • 500+ stores  │ │
              │  • <5 stores           │◄────────────────┤  │   stores│ • Sub-MERCHANT │ │
              │  • No Sub-Merchants    │  Plan upgrade   │  │ • Sub-M │  (UNLOCKED) ✅ │ │
              │    (feature OFF)       │  ↔ downgrade    │  │   OFF ❌ │ • White-label  │ │
              │  • No White-label      │                 │  │ • White │  checkout ✅   │ │
              │                         │                 │  │   label │ • 100K+ txns   │ │
              │                         │                 │  │   OFF ❌│                │ │
              │                         │                 │  └──────────┴────────────────┘ │
              └─────────────────────────┘                 └───────────────────────────────┘
```

### Columns on Account Table (New vs. Existing)

| Field | Indiv. Req. | Business Req. | Notes |
|---|---|---|---|
| `account_type` | ✅ "individual" | ✅ "business" | Enum: individual / business |
| `google_sub` | ✅ | ✅ | Existing |
| `email` / `name` | ✅ | ✅ | Existing |
| `status` | ✅ active/suspended | ✅ | Existing |
| **`saas_sub_merchants_enabled`** | ❌ FALSE (hard-coded) | Set by PLAN | New bool, plan overrides |
| **`whitelabel_enabled`** | ❌ FALSE | Granted per account by the platform operator | New bool, enforced by the store + checkout APIs |
| `is_platform_admin` | Platform owner only | Platform owner only | New bool default False |

---

## 3. Plans & Pricing (CutLuy Parity + SaaS Extensions)

Mirror CutLuy pricing tiers exactly for merchant familiarity. **Only Business accounts can choose paid tiers.** Individual is locked to Starter Free.

### 3.1 Plan Feature Matrix

| Feature | Starter (Free) | Growth ($29/mo) | Scale ($99/mo) | Enterprise (Custom) |
|---|---|---|---|---|
| **Valid account types** | Individual only | Business only | Business only | Business |
| **Payments included** | 100 / month | 10,000 / month | 100,000 / month | Custom |
| **Overage per payment** | $0.025 | $0.010 | $0.006 | Custom volume tiers |
| **Max stores** | 5 | 50 | 500 | Unlimited |
| **Sub-Merchants (SaaS)** | 🔴 OFF | 🔴 OFF | 🟢 ON 500 cap | 🟢 ON Unlimited |
| **White-label checkout** | 🔴 OFF | 🔴 OFF | 🟢 ON | 🟢 ON + SSO |
| **Account-wide shared keys/webhooks** | 🔴 OFF (Store-only enforced) | 🟢 ON (default) | 🟢 ON | 🟢 ON |
| **API keys per store** | 2 | 5 | 20 | Unlimited |
| **Webhooks per store** | 1 | 3 | 10 | Unlimited |
| **CSV / reports export** | 🔴 OFF | 🟢 ON | 🟢 ON | 🟢 ON + S3 exports |
| **Priority support** | ❌ Community | ❌ Email (48h) | ✅ Email (12h) | ✅ Dedicated + Slack |
| **Trial days (paid tiers)** | N/A | 14 | 14 | N/A custom |
| **Public in pricing UI** | ✅ Shown | ✅ | ✅ | ✅ Contact sales CTA |

**Enforcement note.** White-label checkout is enforced today as a per-account entitlement
(`Account.whitelabel_enabled`), granted by the platform operator in the admin console
(`PATCH /v1/admin/accounts/{account_id}`) — there is no automatic plan → entitlement mapping yet.
Without it, `POST`/`PATCH /v1/stores` reject the branding fields with `403 whitelabel_not_enabled`
and `/pay/{id}` renders platform-branded. The tier column above describes the intended pricing
packaging, not the current mechanism.

### 3.2 Invoicing & Plan Ledger

#### Plan Ledger — Atomic Counter
Every `mark_paid()` (see models.py payments) appends one row atomically:

```
PlanLedgerEntry(id, account_id, period_month="2026-09", resource_type="payment",
                resource_id=payment_public_id, amount_cents_delta=+2,
                description="Overage payment #4,282 (Starter plan 100 included)",
                created_at)
```

- `amount_cents_delta = +0` for INCLUDED payments (below cap)
- `amount_cents_delta = +0` for OVERAGE — plans have no per-payment overage price; they differ only in the included quota

#### Plan Invoice (Monthly Auto-generation on T+1)
```
PlanInvoice(
  account_id, subscription_id, month="2026-09", status=draft|issued|paid|void,
  base_fee_cents=plan.monthly_fee_cents,
  usage_payments_count=SUM(payments), overage_payments_count=MAX(0,usage-included),
  overage_fee_cents=0,
  total_due_cents=base + overage,
  paid_at,
  chmabapay_payment_id -> FK payments (we bill customers USING OUR OWN GATEWAY)
)
```

Upsell: auto-generate a KHQR via our own create_payment() API. The customer pays via KHQR to us → paid → invoice.status=PAID. (Dog-fooding our gateway — best proof of product-market fit.)

---

## 4. The Golden Resolution Rule (Scope Inheritance)

**The ONE rule every merchant and every code path learns and respects:**

> **"Every resource resolves to the MOST SPECIFIC scope possible.**  
> If it exists on a **Store**, only that Store uses it.  
> If it exists only on an **Account**, all child Stores inherit it."

### 4.1 Resource-to-Scope Mapping Table

| Resource | Individual Default / UX | Business Default / UX | Enforcement |
|---|---|---|---|
| **API Key** | HIDE Account-scope option; force STORE-scope via dropdown | SHOW Account-wide (shared) DEFAULT radio + Store-specific option | Account-scoped keys are available on every plan |
| **Webhook Endpoint** | Same → store-scope only, hide shared option | Same → Account-wide DEFAULT radio + store option | Same plan gate + resolver |
| **Signing Secret for Webhooks** | Per-store whsec_… | Per-endpoint whsec_… (works for both scopes) | Same |
| **Payment Destination (PaymentLink)** | 1:1 per Store (1 link / store via existing PaymentLink) | Same; *plus* Sub-Merchant destinations auto-create shadow stores + 1:1 links | DB unique=True already on PaymentLink.store_id |
| **Redirect Success/Failure URLs** | Per Store (existing) | Per Store; *plus* Sub-Merchant redirect overrides on SubMerchant | Checkout router priority: SubMerchant.url > Store.url > Account-level default fallback |
| **Plan limits** | Starter (per individual account) | Plan per Business account (one plan covers all stores + sub-merchants) | PlanLedger aggregator |

### 4.2 Code-Level Key→Payment Resolution

Pseudocode — implemented in `auth.resolve_key_context()` + `services.payments.create_payment()`:

```python
# ————————————— Key scope + target resolution ————————————— #
Given Bearer Token + optional body params:

STEP 1 — prefix → scope
  if key starts with "st_":
    → FORCE store = ApiKey.store_id  (store-scoped key)
    → REJECT 400 if caller tried to set a DIFFERENT store_id

  if key starts with "ck_":
    → account-scoped key → proceed to STEP 2

STEP 2 — account-scoped target selection
  if body.sub_merchant_id:
    → require plan_gate: Account.saas_sub_merchants_enabled=True
    → look up SubMerchant by public_id under this account
    → target store = SubMerchant.shadow_store_id

  elif body.store_public_id:
    → look up Store by public_id under this account
    → target store = that Store.id

  else:  # auto fallback
    n = COUNT(ACTIVE stores for account)
    if n == 1:
      target store = the one store
    else:
      return HTTP 400: "Please specify store=… or sub_merchant_id=…; you have N active stores."
```

### 4.3 Webhook Delivery Fan-out (Scope Rule)

Implemented in `webhooks.process_due_deliveries()` via SQL JOIN:

```
For payment PAID event:
  Target endpoints = DISTINCT webhook_endpoints WHERE:
    (endpoint.store_id = payment.store_id)            # Store-scope SPECIFIC
    OR
    (endpoint.account_id = store.account_id AND endpoint.store_id IS NULL)  # Account-scope INHERIT
  No duplicates — uq_event_endpoint (event_id, endpoint_id) unique prevents double-delivery.
```

---

## 5. User Story 1 — Individual Account (Sokha the Noodle Vendor)

**Actor:** Sokha — street-food noodle vendor, 1 store on a PHP custom POS

**End state:** Sokha signs up → pastes ABA PayWay link → generates KHQR from his POS → customer pays → POS auto-marks order paid via signed webhook.

### Step-by-step Journey

1. **Sign-in & Onboarding**
   - Visit `chmabapay.com` → click **Sign in with Google**
   - OAuth callback → `Account.upsert(google_sub, email, name, account_type=INDIVIDUAL, plan=Starter)`
   - Welcome: "Which best describes you?" → picks **🧑 Individual (FREE Starter plan)**

2. **Store Setup**
   - Dashboard empty state → clicks big **[+ Create your first store]**
   - Wizard form:
     - Store name: *Sokha Noodles*
     - City (default Phnom Penh)
     - **Where should payments go?** (3 tabs — show only first 2 for simplicity):
       - **Tab 1 📎 ABA PayWay link (recommended):** `https://link.payway.com.kh/ABAPAYpe518710Y`
       - **Optional pre-fill bakong_id hint** (from Tag 30.01 of any real KHQR he scanned): `126071610243081`
       - **Tab 2 🏦 Bank Account (Style B derivation):** Bank dropdown + Account # → auto preview Tag 30.01 resolved + Tag 30.00 GI
   - [Verify & Create Store] → calls `services.stores.create_store() + attach_link()`
   - Success: store public_id `st_sokha_noodles` + PaymentLink ACTIVE

3. **One-Click Quick Setup (2 Steps)**
   - Wizard offers: *"Set up API key + webhook now?"* → YES
   - **Step A:** Generate STORE-SCOPED key → `st_live_aB3cXyZ…` (shown ONCE, copy-to-clipboard with "we won't show this again"). HIDE account-scope option entirely.
   - **Step B:** Webhook URL input → Sokha enters `https://sokha-pos.kh/hook`
     → Signing secret `whsec_9x8a7s6d…` shown ONCE
     → Endpoint scoped to STORE-ONLY (individual gate enforced)

4. **POS Integration (Sokha's PHP code)**
   - Sample curl (copy from docs inside dashboard):
     ```
     curl -X POST https://api.chmabapay.com/v1/payments \
       -H "Authorization: Bearer st_live_aB3cXyZ..." \
       -H "Content-Type: application/json" \
       -d '{"amount": 5.50, "reference_id": "ticket_42", "metadata": {"table": "A3"}}'
     ```
   - Response: 201 `{ public_id: "pay_abc123", qr_string: "000201010212…", checkout_url: "https://chmabapay.com/pay/pay_abc123", qr_md5, instruction_ref }`
   - POS renders KHQR SVG (uses `qrcode` lib or `<img src=checkout_url/qr.png>` API)

5. **Payment + Detection + Webhook**
   - Customer scans QR in ABA app → enters 5.50 → confirms
   - Bakong DLT settles → qr_md5 indexed
   - ChmabaPay Strategy B bg loop: `bakong_verify_loop()` → every 2s checks 50 pending → MD5 hits → amount matches (epsilon 0.005)
   - → `mark_paid(PAID, bakong_ref, gateway_status_raw)`
   - → `enqueue_event(type=payment.completed, payload=full payment JSON)`
   - → `webhook_loop()` → picks event → HMAC signs → POSTs to `https://sokha-pos.kh/hook`
     ```
     POST /hook HTTP/1.1
     Host: sokha-pos.kh
     X-ChmabaPay-Event: payment.completed
     X-ChmabaPay-Signature: t=1694352792,v1=9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08
     { "payment_id": "pay_abc123", "status": "paid", "amount_cents": 550, "reference_id": "ticket_42", "paid_at": "...", "bakong_ref": "txn_xxx" }
     ```
   - Sokha POS validates signature (Stripe-style: reconstruct payload + `t=1694352792.<raw_body>` + hmac_sha256 compare_digest with whsec_9x8… + verify max_age 300s)
   - → POS marks ticket_42 PAID → kitchen prints

### Exit Criteria for Demo ✅
- [ ] Sokha can sign in with Google
- [ ] Paste a PayWay link → store is ACTIVE
- [ ] Make test payment via `/v1/payments` with st_… key
- [ ] Trigger fake rail payment (Phase 1 dev `/_dev/payments/{id}/pay`)
- [ ] Signed webhook is delivered to `tools/sink.py` (our dev webhook sink server, already existing) → verify signature passes

---

## 6. User Story 2 — Business Account (KhmerPOS — Billing + SaaS White-label)

**Actor:** KhmerPOS — a Cambodian POS SaaS with 500 merchant customers (noodle shops, cafes, retailers)

**End state:**
1. **Use Case A (Growth plan):** KhmerPOS uses ChmabaPay to charge THEIR OWN end-customers $49/mo for POS subscriptions → money to KhmerPOS bank.
2. **Use Case B (Upgrade → Scale plan):** KhmerPOS offers "Accept KHQR on your store, no ChmabaPay account needed" button inside THEIR OWN dashboard → end-customers (like Sokha, but *KhmerPOS customer not ChmabaPay user*) paste ABA link or bank account → ChmabaPay generates KHQR → settlement goes DIRECTLY to the end-customer's account (money never touches ChmabaPay or KhmerPOS).

### Step-by-step Journey

#### Step 1 — Signup + Growth Plan Setup (Use Case A: Billing own platform)

1. Google sign-in → "Which describes you?" → **🏢 Business** → default to **Growth plan 14-day trial ($29/mo)**
3. Account-level Keys → **[+ Create shared key (works for ALL stores)]** (default UX; we don't even ask scope)
   → `ck_live_khmerpos_xyz…` shown once
4. Account-level Webhooks → **[+ Add shared webhook]**
   → URL `https://api.khmerpos.kh/chmabapay-hq` + signing secret `whsec_khmerpos_1a2b…`
5. Stores → **[+ Create Store: "KhmerPOS HQ Billing"]** with KhmerPOS's own ABA PayWay link
6. Test: 49$ subscription payment → `POST /v1/payments` with ck_… key → `{ store: "st_khmerpos_billing", amount: 49.00, reference_id: "khmerpos_sub_8821" }`
   → Customer pays → webhook to khmerpos HQ URL → subscription activated. **Use Case A Works.**

#### Step 2 — Upgrade to Scale (Use Case B: White-label SaaS to KhmerPOS's customers)

7. Billing & Plans → **[Upgrade to Scale ($99/mo)]**
   - Immediately: `Account.saas_sub_merchants_enabled = True` and `whitelabel_enabled = True` (plan sets)
   - Magically, 👥 **Sub-Merchants** tab appears in sidebar (was hidden on Growth). *No account-type change needed.*
8. KhmerPOS adds "Enable KHQR Payments" section inside THEIR OWN end-customer dashboard UI:
   > "Let your customers pay with Bakong KHQR — we (KhmerPOS) partner with ChmabaPay; money goes DIRECTLY to your bank account. Enter your payment destination below:"
   - 3 tabs presented via KhmerPOS UI:
     - Tab 1: ABA PayWay link paste + bakong_id hint
     - Tab 2: Direct Bakong ID paste (12xx… 15 digits)
     - Tab 3: Bank Code dropdown + Account # (Style B derive_bakong_id)
9. **Sokha (end-customer of KhmerPOS — NOT a ChmabaPay user!)** enters ABA PayWay link + bakong_id → KhmerPOS backend makes:
   ```
   POST /v1/platform/sub-merchants
   Authorization: Bearer ck_live_khmerpos_xyz…
   {
     "external_id": "khmerpos_user_sokha_7721",
     "display_name": "Sokha Noodles (via KhmerPOS)",
     "khqr_config": {
       "link_type": "aba_payway_link",
       "raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y",
       "bakong_id": "126071610243081"
     },
     "redirect_success_url": "https://khmerpos.kh/shop/{sub_id}/pay-done",
     "redirect_failure_url": "https://khmerpos.kh/shop/{sub_id}/pay-failed",
     "support_email": "help@khmerpos.kh",
     "whitelabel_css_override": "… KhmerPOS brand colors, ChmabaPay logo hidden …"
   }
   ```
   → Response: 201 `{ sub_merchant_id: "sm_sokha_abc", resolved_bakong_id: "1260716…", tag30_00: "abaakhppxxx@abaa" }`
   → Internally: a "shadow store" st_shadow_sokha_… + PaymentLink auto-created under KhmerPOS account. Sokha never sees ChmabaPay UI.
10. Next day customer buys noodles: KhmerPOS → `POST /v1/payments` with `sub_merchant_id=sm_sokha_abc` + amount 5.50
    → ChmabaPay resolver hits sub_merchant → shadow store → uses SOKHA'S PaymentLink destination → **money settles to SOKHA'S ABA account, not KhmerPOS's**
11. Customer scans → pays → Bakong settles → mark_paid → SubMerchant counters incremented atomically: `total_payments_count +=1`, `total_volume_cents += 550`
12. Webhook fires to KhmerPOS's shared HQ URL with payload including `sub_merchant_id=sm_sokha_abc` and `external_id=khmerpos_user_sokha_7721` → KhmerPOS correlates → kitchen prints ticket in Sokha's shop.

### Exit Criteria for Demo ✅
- [ ] Create shared ck_… account key → generate payment → works WITHOUT specifying store (single store auto fallback)
- [ ] Upgrade to Scale → Sub-Merchants tab appears
- [ ] POST /v1/platform/sub-merchants → creates + returns shadow + destination resolved
- [ ] Create payment with sub_merchant_id → marks paid → SubMerchant counters +1
- [ ] SubMerchant whitelabel_css_override renders on /pay/{id} HTML page

---

## 7. Dashboard UX — Navigation & Page-by-Page Specification

### 7.1 Top-level Navigation

```
TOP NAV:
  [logo] ChmabaPay    Search payments (public_id / reference / qr_md5 / customer email / amount)
                        ➜ Results: quick jump to Payment detail + Payment timeline

  [Notifications bell]   [Plan name badge: Starter / Growth / Scale]  [Avatar menu → Settings / Sign out]

LEFT SIDEBAR (account-type + plan-gated):
  🏠 Overview                                ✅ ALL
  🛒 Stores                                  ✅ ALL
  💰 Payments                                ✅ ALL
  🔑 API Keys                                ✅ ALL        (IND: scope=store only forced)
  📡 Webhooks                                ✅ ALL        (IND: scope=store only forced)
  👥 Sub-Merchants                           🔴 IND ❌ GROWTH  🟢 SCALE/ENT
  💼 Billing & Plans                         ✅ ALL
  ⚙️ Settings                                ✅ ALL
```

### 7.2 Page Specifications

#### 7.2.1 🏠 Overview

```
4 SUMMARY CARDS (ALL ACCOUNTS):
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │ This Month   │ │ Success Rate │ │ Avg Time to  │ │ Active Stores│
  │ $X,XXX Paid  │ │  XX.X%       │ │ Pay: XX s    │ │   X          │
  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘

30-DAY LINE CHART: Daily paid volume $ (bars) + Daily payments count (line overlay)

RECENT PAYMENTS TABLE (top 20):
  ID | Store | Amount | Status badge | Reference | Paid at
  → row click → Payment detail

SMART BANNERS (contextual):
  INDIVIDUAL (Starter, <100 payments): ⚡ "Upgrade to Growth → Account-wide shared keys for 50 stores"
  BUSINESS (Growth, 500+ payments):   🚀 "Unlock Sub-Merchants → offer KHQR to your customers' customers. Upgrade to Scale."
```

#### 7.2.2 🛒 Stores

```
LIST VIEW columns:
  Public ID  |  Name  |  Destination (truncated PayWay link or bakong_id preview)  |  Status badge  |  Keys count  |  Last payment at  |  Actions [Open → Payments filtered]  [Edit]  [Disable]

EMPTY-STATE (account-type aware):
  INDIVIDUAL:       ╔═════════════════════════════════════╗
                    ║ 🛒 No stores yet. Create your first!║
                    ║  [ + BIG Create Store CTA ]        ║
                    ╚═════════════════════════════════════╝

  BUSINESS:         ╔════════════════════════════════════════════════════════════╗
                    ║ 🛒 No stores yet. Two ways to start:                      ║
                    ║  [+ Create Store (UI wizard)]  [+ Import stores CSV (50)] ║
                    ╚════════════════════════════════════════════════════════════╝

PER-STORE DETAIL tabs:
  Tab A: Overview  → store stats, destination preview w/ Tag 30.01 bakong_id + Tag 30.00 GI badge
  Tab B: Payments  → list filtered to this store
  Tab C: Keys      → (per-store scope ONLY allowed for IND; BUSINESS also shows account-wide list header link)
  Tab D: Webhooks  → (per-store scope ONLY allowed for IND; BUSINESS also shows account-wide list header link)

CREATE STORE WIZARD (3 screens):
  1/3 Name + City + Owner phone/support email
  2/3 Payment destination 3-tabs:
      • ABA PayWay link (auto SSR preview)
      • Direct Bakong ID
      • Bank Code + Account (Style B derivation: auto-resolved preview with green tick)
  3/3 Redirect success + failure URLs
```

#### 7.2.3 💰 Payments

```
LIST:  Columns: ID | Store (or Sub-Merchant, if set) | Amount | Status badge | qr_md5 (truncated 8ch) | Paid at | Reference |
FILTER BAR (left):
  • Store dropdown → multi-select (default: All)
  • Sub-Merchant dropdown (if saas_enabled)
  • Status chips:  Pending | Scanned | Paid | Expired | Failed
  • Date range picker: From → To
  • Amount min/max
  • Reference / public_id / qr_md5 search box
  → [Apply Filters]  [Clear]
EXPORT: [Download CSV] button (PlanFeature gated — Starter hides with upgrade tooltip)

PAYMENT DETAIL (modal or full page):
  ┌─ KHQR (inline SVG render) ───────────────────────┬─ PAYMENT TIMELINE ──────────┐
  │                                                  │  12:00:00  Created          │
  │  Amount: $5.50 USD                               │  12:00:15  SCANNED          │
  │  Status: PAID (green)                            │  12:00:42  Bakong indexed  │
  │  qr_md5: 8f3c1a98… (copy btn)                    │     via check_by_md5        │
  │  bakong_ref: txn_abc123… (copy btn)              │  12:00:42  ✅ PAID          │
  │  reference_id: ticket_42                         └──────────────────────────────┘
  │  bill_number: INV-2026-0042                          │  WEBHOOK DELIVERIES (expandable):
  ├──────────────────────────────────────────────────────┤  # | Endpoint | HTTP | Attempts | At
  │ [Show EMVCo tags (debug): Tag 00 → 01 → 30.00, etc]  │  1 | https://…| 200  | 1        | 12:00:43
  │ gateway_status_raw (collapsible JSON Bakong API)     │  2 | store-sc…| 408  | 2 retry nxt
  └──────────────────────────────────────────────────────┘  [Retry all failed] [Send test event] (Plan gated)

  BUTTONS bottom:
    · [Print PDF receipt]   (customer copy: KHQR, amount, bakong_ref, merchant info)
    · [Open Hosted Checkout /pay/{id}]
```

#### 7.2.4 🔑 API Keys

```
LIST columns:
  Prefix (ck_ / st_) | Label / Name | 🔒 Scope badge (Account-wide green / Store: st_…) | Mode (Live / Test) | Last used | Created | Actions

SCOPE RADIO IN MODAL (plan-gated for INDIVIDUAL):
  INDIVIDUAL:
    ┌────────────────────────────────────────────────────────┐
    │ ⚙️ Scope (Individual plan — always per store):         │
    │ Store: [Dropdown: st_sokha_noodles / st_sokha_cafe]   │
    └────────────────────────────────────────────────────────┘
  BUSINESS:
    ┌────────────────────────────────────────────────────────┐
    │ ⚙️ Scope:                                              │
    │ ◉ 🟢 Account-wide (shared, default)                    │
    │    │ This key works for ALL stores + sub-merchants     │
    │ ◯ 🛒 Store-specific  [Dropdown: which store]          │
    └────────────────────────────────────────────────────────┘

OTHER FIELDS:
  Name: "My POS prod"
  Mode:  ◉ Live   ◯ Test  (Test mode: force mark_paid after 5s fake delay → detect webhook replay; no Bakong traffic)
  [Create Key] → ONE-TIME PLAINTEXT DISPLAY  (yellow warning: "Store this safely. We cannot show it again. Copy now.")

REVOKE / ROTATE flow:
  [Revoke] button → "Are you sure? Services using this key will stop working immediately." → set revoked_at + status=revoked
  [Rotate] → create new key with same scope + labels + deprecate old with 7d warning banner.
```

#### 7.2.5 📡 Webhooks

```
LIST columns:
  Endpoint URL (truncated) | 🔒 Scope (Account/Store) | Events (All or comma list) | Signing Secret prefix (whsec_…) | Status (Active/Disabled) | Last delivery status (200 ✓ / errors) | Actions

ADD/EDIT MODAL:
  URL: https://…/webhook
  Events:
    ◉ All events (default):  payment.completed, payment.scanned, payment.expired, payment.failed
                             (Business + SaaS adds: sub_merchant.created, plan.invoice.paid/issued)
    ◯ Select:  [ ] payment.completed  [ ] payment.scanned  [ ] payment.expired  [ ] payment.failed
               (SaaS-only: [ ] sub_merchant.created/updated)
  Scope (same pattern as Keys for IND vs BUSINESS)
  [Add Endpoint] → SIGNING SECRET ONE-TIME DISPLAY  whsec_… copy

  NOTE (2026-09-21): the selectable list is now the four events the platform
  actually raises — payment.completed, payment.expired, payment.superseded,
  payment.reversed. `payment.scanned` is emitted only by the development
  gateway and `payment.failed` has no producer, so neither is offered: a
  merchant who subscribed to one would build a handler for an event that
  never arrives. See the launch-gap-closure spec, G-11.

ENDPOINT DETAIL tabs:
  Tab 1: Settings (edit URL, events, status, disable)
  Tab 2: Deliveries log (200 most recent rows):
         Event type  | HTTP status | Attempts | Response body (last, collapsed) | Created at
         [Retry all failed for this endpoint]  [Send test event payment.completed with sample payload]

SIGNATURE PLAYGROUND (top-right help menu → help):
  Paste any secret + timestamp + body → our dashboard computes signature + shows Python/PHP/JS code snippets (Stripe-style)
```

#### 7.2.6 👥 Sub-Merchants (Plan Gated — Scale/Enterprise only)

```
LIST columns:  ID  | External ID  | Display Name | KHQR Mode (PayWay/Bakong/Bank) | Volume $ | Payments | Status | Actions
SEARCH + FILTERS: by external_id, mode, status, volume min
IMPORT: [CSV bulk import 1000 rows → columns: external_id, display_name, link_type, raw_link, bakong_id, bank_code, account_number, redirect_success, ...]

ADD SUB-MERCHANT FORM:
  1. Identifiers: external_id (required, Saas provider's internal), display_name, support_email
  2. KHQR destination (3-tabs):
     Tab PayWay link
     Tab Direct Bakong ID
     Tab Bank account (Style B derivation → live preview)
  3. Routing + branding: redirect_success / failure URLs (optional: include {sub_id} template var) ; whitelabel_css pastebox + live /pay/{id} preview
  → Submit: auto creates shadow store + PaymentLink, resolves bakong_id, returns green OK

DETAIL view:
  Tab 1: Overview → counters, destination preview
  Tab 2: Payments → list filtered to SubMerchant.shadow_store_id
  Tab 3: Edit → destination, redirects, whitelabel
  Tab 4: Reports → CSV export of all payments for this sub-merchant
```

#### 7.2.7 💼 Billing & Plans

```
CURRENT PLAN CARD:
  ┌────────────────────────────────────────────────────────────────────────────┐
  │ 🟢 Plan: Growth ($29/mo)   [Change Plan → upgrade/downgrade modal]       │
  │ Trial: 12 days remaining (upgrade to Scale → prorated, applies now)       │
  │ Payments this month: 3,281 / 10,000  (progress bar 32% green)            │
  │ Next billing date: Oct 1, 2026                                            │
  │ [Download last invoice PDF]                                               │
  └────────────────────────────────────────────────────────────────────────────┘

PLANS COMPARISON TABLE (CutLuy-style columns matching §3.1 matrix):
  → rows: Payments included / Overage / Stores / Sub-Merchants / Whitelabel / Shared keys / Reports / Support
  → columns: Starter | Growth | Scale | Enterprise (contact sales CTA card)
  → Current plan badge  →  [Downgrade] button  on higher plan rows, [Upgrade] button on higher tiers
  → Downgrade scheduling: "Scheduled for end of period on Oct 1. Current features remain active until then."

INVOICES TABLE (last 24 months):
  Period | Status (Paid / Pending / Due / Void) | Total Due | Paid At | [Pay Now KHQR] (auto-generates OUR gateway payment)
```

#### 7.2.8 ⚙️ Settings

```
TOP ACCOUNT-TYPE CARD (2 options):
  ┌─────────────────────────────────────────────────────────────────────────┐
  │ ⚙️ Account Type:                                                         │
  │ ◉ 🧑 Individual   ◯ 🏢 Business                                         │
  │                                                                           │
  │ Changing:  Business→Individual = "Downgrade plan to Starter first"      │
  │            Individual→Business = auto-move to Growth ($29/mo, 14-day trial)│
  └─────────────────────────────────────────────────────────────────────────┘

TABS:
  • Profile → name, phone, language (EN | ខ្មែរ i18n), email (non-editable, Google), support_email footer
  • Security → password (disabled if Google-only), 2FA TOTP, active sessions list, [Logout all other sessions]
  • Notifications → email toggles:
     ☐ Payment succeeded (per-payment digest disabled by default → opt-in instant)
     ☑ Daily payment summary
     ☑ Payment failed (instant)
     ☑ Invoice due 3d before / day of / overdue (separate toggles)
     ☑ Plan limit 80% warning
     ☑ Plan limit 100% exceeded (urgent)
  • Platform-branded (Business + plan gate): White-label → primary color, custom logo, hide/show "Powered by ChmabaPay" footer on /pay/ page
  • Danger Zone → [Delete account permanently] → typed confirmation of account name; warns: "All stores, payments, keys, webhooks DELETED. Cannot be undone."
```

#### 7.2.9 🛡️ Admin Console (Standalone app `web/admin`, port 3002; Platform Owner Only, gated by is_platform_admin)

```
MENU (left):
  👥 All Accounts                          → list/search, actions: [Impersonate] [Change plan] [Suspend/Activate]
  🛒 All Stores                            → global store search, [Disable/re-enable]
  🔑 All Keys                              → global revoke (fraud response: instant revoke single key or all by account with reason)
  💳 Plan Management                       → CRUD plans (see Section 11.4)
  💼 Invoices                              → global view, Mark manually paid, send 3d overdue email reminders
  📊 Platform Reports                      → CSV export pages:
                                               - Monthly MRR by plan
                                               - Top 100 merchants by volume
                                               - Plan churn + upgrades/downgrades
                                               - Settlement success rate (%)
                                               - Bakong Open API health / avg latency
  🛠️ Platform-wide Settings                → Default signup plan, trial days, global rate limit, signups on/off (registration closed banner), support_email footer admin override
```

---

## 8. Data Model Changes vs. Current Code

This section references [models.py](file:///e:/Development/chmabapay/src/chmabapay/models.py) line numbers where the base model exists and notes what is added.

### 8.1 Account Table — New Columns (Existing base at L67-L78)

```python
# —————————— Status + type enums to add after existing ——————————
ACCOUNT_TYPE_INDIVIDUAL = "individual"
ACCOUNT_TYPE_BUSINESS = "business"

# —————————— NEW Columns on Account ——————————
account_type:                  Mapped[str]       # L after google_sub
# Plan gates (set via plan assignment, admin overrides possible)
saas_sub_merchants_enabled:    Mapped[bool]  default=False
whitelabel_enabled:            Mapped[bool]  default=False
# Admin
is_platform_admin:             Mapped[bool]  default=False
```

### 8.2 SubMerchant Table — NEW

```python
class SubMerchant(Base):        # SaaS white-label feature on Scale/Enterprise Business accounts
    __tablename__ = "sub_merchants"

    id:                 Mapped[int] pk
    public_id:          Mapped[str]  "sm_…"  unique index
    account_id:         Mapped[int]  FK accounts.id  index
    external_id:        Mapped[str]  (Saas provider's internal ID)  index
    display_name:       Mapped[str]
    status:             Mapped[str]  default="active"    # active/disabled

    # 1:1 shadow store — existing code paths (payments/KHQR) always resolve to store_id
    shadow_store_id:    Mapped[int]  FK stores.id  unique

    # KHQR destination (1 chosen path)
    link_type:          Mapped[str]  # aba_payway_link | bakong_id | bank_account
    raw_link:           Mapped[str|null]   # for PayWay links
    merchant_account_id: Mapped[str|null]  # resolved bakong_id (Tag 30.01)
    merchant_name:      Mapped[str|null]
    payway_client_id:   Mapped[str|null]
    bank_code:          Mapped[str|null]  # ABA/ACLB/CADI/WING… (BANK_PROFILES key)
    account_number:     Mapped[str|null]  # source for Style B derive_bakong_id
    resolved_bakong_id: Mapped[str|null]  # output of derive_bakong_id, cached

    # SaaS-provided routing (priority higher than Store fallback)
    redirect_success_url: Mapped[str|null]
    redirect_failure_url: Mapped[str|null]
    support_email:      Mapped[str|null]
    whitelabel_css:     Mapped[str|null]  # inline CSS injected on /pay/{id}

    # Usage counters (updated in mark_paid() atomically)
    total_payments_count:  Mapped[int]   default=0
    total_volume_cents:    Mapped[int]   default=0

    created_at / updated_at ...
```

### 8.3 Plan, PlanSubscription, PlanInvoice, PlanLedgerEntry, AuditLog — NEW

```python
# ————————————— Plan (CutLuy-style pricing tiers) —————————————
class Plan(Base):
    __tablename__ = "plans"
    id: pk
    name: str                  # "Starter"
    code: str (unique slug)    # "starter"
    monthly_fee_cents: int     # 0 for Free
    base_payments_included: int
    currency: str default USD

    # Feature gate booleans / numbers
    max_stores: int | null
    max_keys_per_account: int
    max_webhooks_per_account: int
    priority_support: bool
    # csv_export_enabled was dropped on 2026-09-21: CSV export is available on
    # every plan, so the flag gated nothing (launch-gap-closure D6 / migration 0010).
    is_public: bool default True         # shows in pricing UI
    is_active: bool default True         # retired plans remain historical
    ...

# ————————————— Active subscription of an account to a plan —————————————
class PlanSubscription(Base):
    __tablename__ = "plan_subscriptions"
    id: pk
    account_id: int FK index
    plan_id: int FK
    status: str  # active / past_due / canceled / trial
    started_at, canceled_at, next_billing_at, trial_ends_at

# ————————————— PlanInvoice (monthly auto) —————————————
class PlanInvoice(Base):
    __tablename__ = "plan_invoices"
    id: pk
    account_id, subscription_id
    period_month: str   "2026-09"
    status: draft / issued / paid / void
    base_fee_cents, usage_payments_count, overage_payments_count, overage_fee_cents, total_due_cents
    paid_at: datetime|null
    chmabapay_payment_id: int|null FK payments.id   # we bill via our own gateway

# ————————————— Plan Ledger (atomic counter per event) —————————————
class PlanLedgerEntry(Base):
    __tablename__ = "plan_ledger_entries"
    id: pk
    account_id index
    period_month index   "2026-09"
    resource_type: str   "payment" / "sub_merchant_seat" / "overage_topup"
    resource_id: str     payment_public_id or sub_merchant_id
    amount_cents_delta: int
    description: str
    created_at

# ————————————— Audit log (privileged actions) —————————————
class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: pk
    actor_account_id: int FK accounts.id  # whoever acted: operator or merchant
    action: str   # account.suspended / plan.changed / key.revoked / ...
    target_type: str  # Account / Store / Plan / ApiKey / Invoice
    target_id: int
    details: JSON
    created_at
```

### 8.4 Existing Tables — Minor Additions

- ApiKey.mode already exists at L137 (`mode=live`). Honor test-mode bypass in bakong_verify_loop: if ApiKey.mode=test → mark_paid after 5s fake delay after any payment creation.
- WebhookEndpoint: add events=[] JSON already exists; add `status` column already exists.
- Event: type=payment.* exists; add type keys `sub_merchant.created`, `plan.invoice.issued`, `plan.invoice.paid` as Business-only.

---

## 9. Operational Flows (Diagrams) — End-to-End Payment + Detection + Webhook

### 9.1 Standard Checkout Flow

```
    ┌────────────┐        ┌───────────────────────────┐       ┌──────────────────┐
    │  MERCHANT  │        │       CHMABAPAY           │       │   CUSTOMER APP   │
    │  (POS/WEB) │        │   GATEWAY (our code)      │       │  ABA/ACLEDA/WING │
    └──────┬─────┘        └─────────────┬─────────────┘       └────────┬─────────┘
           │ ① POST /v1/payments         │                              │
           │ (amount, ref_id)            │                              │
           ──────────────────────────────>                              │
           │                            │ ② resolve scope → target store│
           │                            │ ③ amount validation (min/max,│
           │                            │   2-decimal Decimal check)   │
           │                            │ ④ idempotency DB guard       │
           │                            │ ⑤ build_khqr_payload() →    │
           │                            │   → EMVCo TLV → CRC16        │
           │                            │ ⑥ MD5 stored (qr_md5 index)  │
           │ ⑦ 201 + public_id + qr     │                              │
           <──────────────────────────────                              │
           │                            │                              │
           │ ⑧ Customer gets QR (POS or │                              │
           │   redirect /pay/{id} HTML) │                              │
           ─────────────────────────────────────────────────────────────>
           │                            │                              │ ⑨ Scan QR → Amount → Confirm
           │                            │                              │
           │                            │    ╔════════════════════════════════════════╗
           │                            │    ║  BAKONG DLT SETTLEMENT (NBC Rail)     ║
           │                            │    ║  → Transaction written, MD5 indexed   ║
           │                            │    ╚════════════════════════════════════════╝
           │                            │                              │
           │                            │ ⑩ 3-PRONG DETECTION RUNS:   │
           │                            │                              │
           │                            │   [Strategy A - ABAPayWay SSR page]    │
           │                            │    ├ re-fetch link.payway.com.kh/<slug> │
           │                            │    ├ ABAPAY_TOKENS (HTML + __NUXT__ scan)│
           │                            │    └ PAID signal + amount match → PAID │
           │                            │                              │
           │                            │   [Strategy B - qr_md5 Bakong index]   │
           │                            │    └ bakong_verify_loop → check_by_md5  │
           │                            │      → amount match → mark_paid()       │
           │                            │                              │
           │                            │   [Strategy C - cascade fallback]       │
           │                            │    └ full verify_receipt → md5 → short → │
           │                            │      instruction_ref → external_ref ... │
           │                            │                              │
           │                            │ ⑪ mark_paid:                │
           │                            │   • payment.status ← PAID   │
           │                            │   • paid_at = now            │
           │                            │   • bakong_ref = hash        │
           │                            │   • PlanLedgerEntry append   │
           │                            │   • (IF SubMerchant) counter │
           │                            │     total_volume += 550     │
           │                            │   • enqueue_event(outbox)    │
           │                            │     payment.completed       │
           │                            │                              │
           │ ⑫ Fan-out via scope rule:  │                              │
           │   STORE endpoints +        │                              │
           │   ACCOUNT endpoints        │                              │
           │   (no duplicates via uq)   │                              │
           │                            │                              │
           │   HMAC SIGN + BACKOFF      │                              │
           │   2ⁿ × up to 3600s × 8x   │                              │
           <─────────────────────────────                              │
           │ ⑬ Merchant validates      │                              │
           │   signature + fulfills    │                              │
           │   order / prints receipt  │                              │
           └────────────────────────────┴──────────────────────────────┘
```

### 9.2 SaaS Sub-Merchant Variant (Same diagram, different routing)

The flow is **identical to §9.1** except:

- Step ① body contains `{ sub_merchant_id: sm_…, …}` instead of `{store_id: st_…}`
- Step ② resolver does: SubMerchant (saas gate on) → shadow_store_id → uses that
- Step ⑤ QR destination = SubMerchant's PaymentLink → CUSTOMER'S bank account
- Step ⑪ additionally increments `SubMerchant.total_volume_cents + total_payments_count`
- Step ⑫ webhook includes `sub_merchant_id` + caller's `external_id` in payload for correlation

---

## 10. API Surface — Required Additions vs. Existing

Today's mounted routers in [main.py](file:///e:/Development/chmabapay/src/chmabapay/main.py#L48-L56):

```
payments (create/list/get), stores (CRUD, link attach), checkout (/pay/{id}),
transactions (Bakong search/verify/reconcile), khqr (from-link/from-account/bank-codes),
dev (Phase 1 fake rail — only when enable_dev_gateway=true)
```

### 10.1 New Routers Required

```
routers/auth.py           —   Google OAuth sign-in + session JWT cookie
routers/platform.py       —   /v1/platform/*      (SaaS Business plan gate)
routers/admin.py          —   /v1/admin/*         (is_platform_admin gate)
routers/reports.py        —   /v1/reports/*       (CSV exports, plan gated)
routers/account.py        —   /v1/me/*            (session auth, Settings page endpoints)
routers/billing.py        —   /v1/billing/*       (plan list, upgrade/downgrade, invoices)
```

### 10.2 Endpoint Matrix

| Group | Method | Path | Auth | Scope | Body (brief) | Returns |
|---|---|---|---|---|---|---|
| **Auth** | GET | `/auth/google/login` | Public | — | — | 302 to Google |
| | GET | `/auth/google/callback` | Public | — | code,state from Google | 302 to dashboard + set httpOnly session cookie JWT + CSRF token |
| | POST | `/auth/signout` | Session | — | — | 204 clear cookie |
| **Me / Account** | GET | `/v1/me` | Session | All | — | Account + plan features + active stores |
| | PATCH | `/v1/me/profile` | Session | All | name, phone, lang, support_email | 200 |
| **Keys** | GET | `/v1/keys` | API Key + Session | All | Query: ?scope=account/store | list |
| | POST | `/v1/keys` | API Key + Session | Plan gate IND|BUS| | {name, mode, scope, store_id if store} | key display once |
| | DELETE | `/v1/keys/{id}/revoke` | API Key + Session | Owner | reason | 200 revoked_at |
| | POST | `/v1/keys/{id}/rotate` | API Key + Session | Owner | — | {new key display, deprecation_notice_at: old + 7 days} |
| **Webhooks** | CRUD mirrors Keys → | `/v1/webhooks` | Session + Owner | Plan gate | — | — |
| | POST | `/v1/webhooks/{id}/send-test` | Session | Owner | event_type | 200 request_id |
| **Billing** | GET | `/v1/billing/plans` | Session | All | — | public plans matrix |
| | POST | `/v1/billing/change-plan` | Session | Business | {plan_code, schedule_immediately (only upgrades)} | 200 (effective_at, proration if any) |
| | GET | `/v1/billing/invoices` | Session | All | query period_month | list |
| | GET | `/v1/billing/invoices/{id}/khqr` | Session | Owner | — | 201 {payment_id, qr_string → pay this invoice via our own gateway!} |
| **SaaS Sub-Merchants (Platform Router)** | POST | `/v1/platform/sub-merchants` | API Key (account-scope) + plan gate | Business Scale/Ent | {external_id,display_name,khqr_config,redirects,whitelabel_css} | 201 + resolved bakong_id |
| | GET | `/v1/platform/sub-merchants` | API Key or Session | Owner | query: ?status?external_id?page | list (paginated) |
| | GET | `/v1/platform/sub-merchants/{id}` | Same | — | — | detail + counters |
| | PATCH | `/v1/platform/sub-merchants/{id}` | Same | — | edits to destination/routing | 200 |
| | POST | `/v1/platform/sub-merchants/import` | Same | — | CSV (max 1000) via multipart | {created, failed_rows: [error]} |
| | DELETE | `/v1/platform/sub-merchants/{id}/disable` | Same | — | reason | 200 status=disabled |
| **Reports** | GET | `/v1/reports/payments.csv` | API Key or Session | Plan gate: not Starter | ?from=&to=&store_id=&sub_merchant_id=&statuses= | CSV streaming |
| | GET | `/v1/reports/payments.json` | Same | — | same filters + pagination | list + totals summary |
| **Admin (Platform Owner only)** | CRUD Plan (mirror) | `/v1/admin/plans/*` | Session + is_admin | Owner | — | full CRUD |
| | PATCH | `/v1/admin/accounts/{id}/suspend` | Session + is_admin | Owner | {reason} | status=suspended |
| | POST | `/v1/admin/accounts/{id}/impersonate` | Session + is_admin | Owner | — | 200 + {impersonation_session_token} (admin can act as user for 30 min) |
| | DELETE | `/v1/admin/keys/{id}/revoke-global` | Session + is_admin | Owner | {fraud_reason} | instant revoke |
| | GET | `/v1/admin/reports/mrr.csv` | Session + is_admin | Owner | month | CSV platform MRR |

### 10.3 Existing Router Changes (Small)

- `payments.create_payment()` → step ② resolver extended with sub_merchant_id path (§4.2 STEP 2)
- `stores.create_store()` → enforce PlanFeature.max_stores cap with HTTP 402: "Plan limit reached. Upgrade to Growth → 50 stores"
- `checkout /pay/{public_id}` → CSS override priority: `SubMerchant.whitelabel_css > Store.default_none > Account.branding_css (if whitelabel_enabled)`
- `mark_paid()` services.payments → append PlanLedgerEntry atomically; if SubMerchant shadow_store increment counters; if payment.mode=test bypass Bakong detection after 5s fake delay

---

## 11. Admin Panel (Platform Owner) — Exact Spec

### 11.2 Accounts Table

```
Search bar (email, account_id, name, google_sub)
Filters: Account type, Plan, Active/Suspended, Created date range
Columns: ID | Email | Name | Type | Plan (badge) | Stores | Payments | Volume $ | Status | Actions
Actions:
  [Change Plan] → modal: pick plan, effective immediate or end of period, proration toggle
  [Impersonate] → opens a new tab where admin acts as that user (logs every action in audit with impersonation=true)
  [Suspend / Activate] → with reason field → email notification to account
  [Jump to → Payments] with filter
```

Shipped today: `PATCH /v1/admin/accounts/{account_id}` toggles the white-label entitlement
(`whitelabel_enabled`), surfaced as an Enable/Disable button on the account detail page. Store
branding writes return `403 whitelabel_not_enabled` while it is off and the hosted checkout stays
platform-branded.

### 11.3 Plan CRUD

```
LIST → Code | Name | Monthly Fee | Included Payments | Overage | Stores Max | Sub-M enabled? | Active?
EDIT PLAN form → full matrix of §3.1 as editable fields.
[+ Create New Plan] → clone default scale template, edit as needed.
Delete button: disabled unless "historical" (zero active subscriptions; otherwise → mark is_active=false + hide from pricing page)
```

### 11.4 Platform Settings

```
[ ] Registration Signups ON/OFF (public marketing page works but sign in disabled)
Default new signup plan: [Starter dropdown]
Trial days: [14]
Global rate limit (req/min per api key): [100]
Per-IP public checkout limit (req/min per /pay/*): [50]
Admin support email (shown on footer of admin emails): [admin@chmabapay.com]
ChmabaPay HQ ABA PayWay link (for invoice self-billing KHQR generation): [st_chmabapay_hq store id]
```

---

## 12. Gap Filling — Unsounded-but-Required Capabilities

These weren't in your explicit scope but are **non-negotiable for production**. All are low-effort and included in milestones below.

| # | Gap | Why Required | Solution (Built into Milestone) |
|---|---|---|---|
| **12.2** | Test-mode keys | Every merchant needs to develop + test webhooks with no real money | ApiKey.mode=test. When `ApiKey.mode=test` → payments force mark_paid after 5s fake delay. Dev rail also accessible for rich tests (scanned→paid) |
| **12.3** | Idempotency across more endpoints | Prevent duplicate payment links, duplicate sub-merchants, duplicate keys | Add `Idempotency-Key` header enforcement for all POST mutations (already on payments; extend to Stores, Keys, Webhooks, Sub-M create, Billing change-plan) |
| **12.4** | Webhook signature playground | CutLuy has it; merchants need self-serve way to debug HMAC | Settings help → "Signature playground" page: paste secret+timestamp+body → preview signature + Python/PHP/JS/Java/C# copy snippets |
| **12.5** | Khmer language dashboard i18n | Many Khmer SME owners don't read English; differentiator vs CutLuy if we ship | EN / ខ្មែរ toggle top right. All static strings via gettext JSON dict (no heavy framework needed; keep it simple — `i18n.t(key, lang)` helper) |
| **12.6** | Rate limiting + abuse | Free tier abuse scenarios: infinite payment creation on Starter; public checkout DoS | Per-API-key req/min counter in memory with sliding window; per-IP public checkout throttle |
| **12.7** | Fraud: duplicate qr_md5 within 24h for same store | Rare but destructive: customer pays twice for same invoice | mark_paid → check if another PAID payment with same bill_number and same amount exists in 24h → flag with warning to merchant in dashboard; do not auto-webhook 2nd (wait for merchant override, or auto-refund via Bakong when/if that API becomes available) |
| **12.8** | PDF receipt + statement download | Customer proof of payment + merchant dispute defense | Payments detail → `[Print PDF receipt]` button; Billing → invoices `[Download PDF statement]` (use `reportlab` or pure HTML → print CSS PDF) |
| **12.9** | Email notifications | invoice due, 80% plan limit | `fastapi-mail` or `resend` client. Triggered from background loops / admin actions. 2 templates: HTML (rich) + plain text fallback |
| **12.10** | Structured audit logging (JSON) + trace_id every request | Compliance admin audit + debug Bakong failures | Every HTTP request gets trace_id in response header X-ChmabaPay-Trace; every mark_paid logs JSON structured line with {trace_id, payment_id, source_strategy (Bakong/ABA SSR/cascade), via_short_hash if any, amount, bakong_ref} |

---

## 13. Implementation Order — 3 Milestones with Working Demos

Each milestone produces a **runnable, demo-able build** (not just code). Progress = demo the user story end-to-end.

### Milestone 1 (Weeks 1-2) — **Individual Story + Dashboard Skeleton + Billing backend**

**Goal: Individual Sokha can sign in with Google, create a store, paste ABA link, receive payments, get webhook → Demo.**

Backlog:
- [ ] **DB:** Add Account new columns (account_type, plan gates, is_platform_admin)
- [ ] **DB:** Create Plan, PlanSubscription, PlanLedgerEntry tables with default Starter/Growth/Scale/Enterprise plan seed (migration + `db.seed_default_plans()`)
- [ ] **Auth router (NEW):** `routers/auth.py` Google OAuth (authlib/fastapi-sso) + JWT session cookie + `/auth/signout`
- [ ] **Dashboard account router (NEW):** `/v1/me` → session auth, profile
- [ ] **Keys/Webhooks session endpoints:** `/v1/keys` + `/v1/webhooks` session CRUD (plan-gate scope options for IND vs business — still show Business scope but reject if IND tries; but M1 demoes only Individual)
- [ ] **Dashboard UI (Next.js or Vue pick one):** Login redirect, Overview (4 cards mock + real data once connected), Stores page → Create Store wizard 3-tab destination, Payments list + filter, Detail with QR + timeline, Per-store Keys + Webhooks tabs, Settings profile
- [ ] **Mark paid hook:** append PlanLedgerEntry atomically; enforce PlanFeature.max_stores cap in create_store
- [ ] **Test-mode bypass:** ApiKey.mode=test → mark_paid fake delay 5s + mark after
- [ ] **Billing router (NEW, backend only):** `/v1/billing/plans` → public plans matrix, change-plan endpoint (Individual → Business = auto Growth trial)

**Milestone 1 Exit Demo Checklist:**
- [ ] New user sign in with Google → directed to onboarding → picks Individual
- [ ] Creates store with PayWay link
- [ ] Test-key mode: create payment → auto-marks paid (no Bakong needed) → signed webhook POST to sink dev server
- [ ] Upgrade from Starter → Growth (via Billing page). Business type auto-switched to business. Shared key scope option appears on key create.

### Milestone 2 (Weeks 3-4) — **Business + Admin Panel + CutLuy Plans**

**Goal: Private LTD company → Growth plan → shared account key → 5 stores → Demo Plan invoices + CSV exports.**

Backlog:
- [ ] **PlanInvoice scheduler:** On T+1 midnight background job → generate invoices for all active subscriptions for previous month; create KHQR payment via our own gateway to our own HQ store
- [ ] **Billing Invoices page:** Invoices list with [Pay Now KHQR] button → shows our gateway QR (dog food)
- [ ] **Reports router (NEW):** CSV export of payments (plan-gated: hidden on Starter) + JSON list with totals summary
- [ ] **Admin console pages:** Accounts list + impersonate, Plan CRUD, platform settings, global key revoke
- [ ] **Dashboard: Notification center email + in-app (12.9 gap):** fastapi-mail integration, plan limit 80%/100% email, invoice reminders
- [ ] **Dashboard Khmer i18n (12.5 gap):** EN/KH toggle top-right + string files for all pages built M1
- [ ] **Rate limit middleware (12.6 gap):** Per-key sliding window 100 req/min; public checkout /pay/* IP throttle 50/min
- [ ] **Structured JSON logs + X-ChmabaPay-Trace header (12.10 gap):** loguru + request_id middleware

**Milestone 2 Exit Demo Checklist:**
- [ ] Admin creates shared account key → Payment created (no store param, auto uses single store) → paid → webhook fires to shared URL
- [ ] Invoice generates on T+1 → shows [Pay Now KHQR] (using our gateway) → fake pay → invoice → paid
- [ ] Download payments CSV for account → works

### Milestone 3 (Weeks 5-6) — **Business Scale Plan → Sub-Merchants → SaaS White-label**

**Goal: Same Business account upgrades from Growth → Scale → Sub-Merchants tab appears → KhmerPOS-style end-to-end demo.**

Backlog:
- [ ] **SubMerchant table + shadow store auto-create pattern** migration; `create_sub_merchant()` service: validates destination → runs Style B derivation if bank_account → auto-creates store st_shadow_<hash> + PaymentLink 1:1
- [ ] **Platform router (NEW):** `/v1/platform/sub-merchants` CRUD + CSV bulk import (1000 max) → plan gate `saas_sub_merchants_enabled` = True
- [ ] **Payment resolver extension** (§4.2 STEP 2 — sub_merchant_id → shadow store)
- [ ] **mark_paid counter atomics** → SubMerchant.total_payments_count / total_volume_cents = connection.execute(UPDATE … col=col+1 WHERE id)
- [ ] **Checkout page whitelabel CSS override + redirect priority** (SubMerchant fields)
- [ ] **Dashboard Sub-Merchants page (SaaS plan visible only):** list, detail, add form 3-tab destination, counters, import CSV
- [ ] **Scope rule in dashboard UI:** Sub-Merchants tab visibility = `Plan.allow_saas_sub_merchants` (so Growth account sees it hidden)
- [ ] **Upgrade path UI:** Billing → Upgrade from Growth to Scale (prorate, effective immediately) → page refresh: Sub-Merchants tab appears
- [ ] **Signature playground (12.4 gap):** docs/help page → sample code snippets for 4 languages
- [ ] **Fraud duplicate detection (12.7 gap):** mark_paid pre-check for same store bill_number in 24h + warning banner in dashboard if triggered
- [ ] **PDF receipts (12.8 gap):** Print PDF receipt button on payment detail + PDF statement on invoices

**Milestone 3 Exit Demo Checklist (Full Story 2):**
- [ ] Existing Business (Growth) → Billing page → [Upgrade to Scale]
- [ ] New 👥 Sub-Merchants tab appears. Admin-created Sub-Merchant with bank account = Style B → resolved bakong_id shows green
- [ ] `curl POST /v1/platform/sub-merchants` (external CSV import test) → 100 rows batch created
- [ ] Payment created with sub_merchant_id parameter → paid via dev rail → counters increment atomically on SubMerchant row
- [ ] Whitelabel CSS renders on hosted /pay/{id} checkout page for that payment
- [ ] Dashboard signature playground → merchant can generate + verify webhook signatures
- [ ] Export CSV payments filtered by sub_merchant_id → works

---

**This BRD is the single source of truth for implementation.**

All future code changes, PRs, and feature discussions should reference:
- For data schema: §8
- For UX: §7
- For flows: §9
- For API additions: §10
- For delivery order: §13 (milestone checklists are the definition of done)

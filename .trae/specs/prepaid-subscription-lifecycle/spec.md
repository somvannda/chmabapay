# Prepaid Subscription Lifecycle — Spec

**Goal.** Make plan billing a prepaid renewal loop that works with KHQR: raise the invoice
*before* the paid period ends, tell the merchant in time to pay, and freeze the account when the
money never arrives.

**Audited revision.** Schema at Alembic `0011`, source at the state described below.

**Why this is not "recurring billing".** KHQR is a pull payment the customer initiates.
There is no mandate and no token, so the platform can never charge on its own. Every
mechanism in this spec follows from that single constraint: the merchant must be *asked*
in time and *able* to pay at the moment they decide to, and the only lever on a
non-payer is what they stop being entitled to.

---

## 1. What the platform does today (verified)

| # | Behaviour | Evidence |
| --- | --- | --- |
| C-01 | The billing sweep selects `next_billing_at <= now` — the invoice is raised **after** the credit is spent. | `src/chmabapay/services/billing.py:257-267` |
| C-02 | The sweep advances `next_billing_at += 30d` **whether or not an invoice was written and whether or not anything was paid**. | `src/chmabapay/services/billing.py:296-299` |
| C-03 | Nothing reads an invoice's status outside the sweeper. `_get_active_sub` returns `trial`/`active` rows only, and no code voids, suspends or downgrades on non-payment. A merchant who never pays therefore keeps their paid plan indefinitely while open invoices accumulate. | `src/chmabapay/routers/billing.py:98-112`; no writer of `void` anywhere |
| C-04 | The first paid period *is* prepaid: `change-plan` raises an invoice for the current month and parks the subscription as `pending`. Only renewals are arrears. | `src/chmabapay/routers/billing.py:230-250` |
| C-05 | `PlanInvoice` has no `due_at`, `period_start` or `period_end`. The only temporal keys are `period_month` (`YYYY-MM`) and `paid_at`. "Seven days in advance" is not expressible in the current schema. | `src/chmabapay/models.py:334-363` |
| C-06 | The period key is a calendar month but the credit is a rolling 30 days, and they drift: `2026-01-31 + 30d = 2026-03-02`, so February is never billed; later a 30-day step can land twice in one calendar month, where the second occurrence hits `uq_plan_invoice_period`, returns the existing row, and the schedule advances anyway — that period goes unbilled. | `src/chmabapay/services/billing.py:35,282-297`; `models.py:341-346` |
| C-07 | Unpaid statuses are inconsistent: `issue_invoice` writes `open`, the column default is `draft`, `get_invoice_khqr` promotes `draft`→`issued`, and the admin route adds `waived`/`credited`. | `services/billing.py:224`; `models.py:352`; `routers/billing.py:433`; `routers/admin.py:971-975` |
| C-08 | `issued_at` is read with `getattr(..., None)` in the serializer and is **not a column** — the field was intended and never added. | `src/chmabapay/routers/billing.py:329,435` |
| C-09 | The only outbound notification path is Telegram to the **operator** activity group. `ACTIVITY_TELEGRAM_CHAT_ID`, no email provider anywhere in the repository or dependencies. | `src/chmabapay/services/notifications.py:42-55`; no SMTP/SES/Resend in `pyproject.toml` or `deploy/.env.example` |
| C-10 | Manual settlement exists and does activate a parked subscription: `POST /v1/admin/invoices/{id}/resolve` with `mark-paid`/`waive`/`credit`. | `src/chmabapay/routers/admin.py:978-1037` |
| C-11 | Plan limits are enforced **at create time only**, by reading the active subscription's plan: stores, keys, webhooks, and the monthly payment quota. Existing resources are never deleted or disabled when a plan changes. | `routers/stores.py:48-73`; `routers/keys.py:113`; `routers/webhooks.py:179`; `services/payments.py:382-413` |
| C-12 | The heartbeat scheduler is `loop.call_later`-based with per-interval dedup keys; `W3` runs hourly off `billing_sweep_interval_seconds` (default 3600). | `src/chmabapay/workers/runtime.py:124-330` |

**The money finding.** C-02 + C-03 together mean a merchant can consume a paid plan
forever without paying for it. That is a larger revenue leak than the reminder cadence,
and it is the reason Phase 3 exists.

---

## 2. Invariants this design establishes

1. **Coverage is bounded by `next_billing_at`.** The instant it names is the end of the
   period the merchant has paid for. It moves **only on settlement** — never on issuance.
2. **At most one open invoice per paid subscription.** A consequence of (1): the next
   invoice is not raised until the previous one has moved the period end forward.
3. **An invoice is a claim for a specific window**, `[period_start, period_end)`, with a
   `due_at`. These are instants, so no timezone or calendar-month arithmetic is involved
   in any decision.
4. **Enforcement keys off `due_at`, not off the label.** Overdue is derived
   (`status = open AND due_at < now`) rather than stored, because a stored flag needs a
   writer to keep it true and that writer is a second source of truth.
5. **Notification is at-most-once per (invoice, tier, channel)**, enforced by a unique
   constraint, not by the caller remembering.
6. **Nothing activates until it is paid** — already true today
   (`services/billing.py:9-18`) and preserved.
7. **A lapsed account can always pay, and can always restore its ability to earn.** Freezing
   (§7.2) never removes the route to `/dashboard/billing`, and the billing page always offers a
   plan the merchant can afford — so there is always a way out of the freeze that does not depend
   on money they do not have.
8. **Every `paid` invoice's window is a window the merchant was entitled to.** Which is why a
   reinstatement issues a new invoice for the granted period instead of marking the lapsed one
   paid (§5.3).

---

## 3. Target lifecycle

`lead = 7d`, `grace = 7d`. `D` is `next_billing_at` — the end of the paid period.

### Timeline for one renewal

`D` is `next_billing_at` — the instant the paid coverage ends.

```
  D-30  ────────────  the previous period was settled; coverage runs to D
  D-7   ────────────  W3 raises the renewal invoice, due_at = D, and tells the merchant
  D-3   ────────────  W6 records tier due_3
  D-1   ────────────  W6 records tier due_1
  D     ────────────  due today; next_billing_at does NOT move without a payment
  D+1   ────────────  W6 records tier overdue_1
  D+3   ────────────  W6 records tier overdue_3
  D+6   ────────────  W6 records tier overdue_final, naming the freeze date
  D+7   ────────────  W6 enforce: freeze the account (status = restricted)
  any   ────────────  a payment sets next_billing_at = period_end (§5.3)
```

### Subscription transitions (states unchanged, transitions new or narrowed)

| From | Trigger | To | Side effect |
| --- | --- | --- | --- |
| `free` | `change-plan` to a paid tier | `pending` | invoice raised, `due_at = now`, nothing in force yet |
| `pending` | settlement | `active` | `next_billing_at = period_end`; every other `trial`/`active` row canceled |
| `pending` | abandoned 30d | `canceled` | invoice voided `superseded` |
| `active` | `change-plan` to Free | `canceled` + `free` active | immediate — a downgrade must never wait on a payment |
| `active` | renewal settled | `active` | `next_billing_at = period_end`; the anniversary is preserved |
| `active` | grace expired (`D+7`) | `active` — no transition | the *account* is frozen (`restricted`); the plan, the stores and the invoice are all left as they were (§7.2, D1) |
| `canceled` | payment of a voided invoice | `canceled` + new `active` | fresh period from `paid_at` (§5.3 reinstatement) |

`pending` continues to grant nothing (`_get_active_sub` reads `trial`/`active` only), so
the existing "parked purchase" behaviour is untouched. Each row above leaves exactly one
subscription in `trial`/`active`, which is the invariant `_get_active_sub` assumes when it
reads a single row.

### Invoice state machine

```
   raised by W3 (renewal)                     settled by mark_paid,
   or change-plan (upgrade)                   the admin route, or a
        │                                     late KHQR payment
        ▼                                                │
   ┌────────┐   payment arrives   ┌──────┐ ◄─────────────┘
   │  open  │ ──────────────────► │ paid │
   └────────┘                     └──────┘
        │
        ├──► void       grace expired · superseded by an abandoned upgrade · operator
        ├──► waived     operator forgives the debt — not income, no paid_at
        └──► credited   operator grants the service without money — same
```

`open` is the only collectible state. `draft` and `issued` are legacy aliases of `open`
(C-07) and are read as `open`; nothing writes them again. **A `void` invoice is still
payable** — `get_invoice_khqr` only refuses `paid` — and that is intentional rather than
incidental: paying one is the reinstatement path in §5.3, which is how a merchant who was
frozen yesterday buys their plan back. `void_reason = 'downgraded'` is the merchant choosing a
different plan (§7.5) and `'grace_expired'` is reserved for an abandoned window.

### Worked timeline

An account on Starter ($9.99), paid through October 12, lead 7 days, grace 7 days:

| When | Platform | Merchant sees |
| --- | --- | --- |
| Oct 12 − 30d (Sep 12) | period paid; `next_billing_at = Oct 12` | "Active — renews Oct 12" |
| **Oct 5** (`D-7`) | W3 raises `INV-…-2026-10`, `due_at = Oct 12`, posts to the ops feed | Banner *info*: renews Oct 12, $9.99, **Pay now** |
| Oct 9 (`D-3`) | W6 records `due_3` | Banner *info*, same CTA |
| Oct 11 (`D-1`) | W6 records `due_1` | Banner *info*: renews tomorrow |
| **Oct 12** (`D`) | W6 records `due_today`. `next_billing_at` **unchanged** — no payment yet | Banner *warning*: due today |
| Oct 13 | W6 records `overdue_1` | Banner *warning*: payment overdue |
| Oct 15 | W6 records `overdue_3` | Banner *warning*: overdue; plan still active |
| Oct 18 (`D+6`) | W6 records `overdue_final` | Banner *critical*: frozen tomorrow — stores stop generating codes, dashboard becomes read-only |
| **Oct 19** (`D+7`) | W6 `enforce` sets `status = restricted`. The plan, the stores and the invoice are all left exactly as they were | Everything is still listed, but the dashboard is read-only and no store can generate a code. Only the billing page accepts input |
| Oct 19, later | Merchant opens billing and pays | Fresh QR minted, invoice `paid`, account back to `active` on Starter, every store resuming exactly as it was |
| *or* Oct 19, later | Merchant opens billing and picks Starter instead | Pro invoice voided (`downgraded`), Starter invoice raised for the new period, the freeze lifts when it settles — and which 5 stores stay is theirs to choose |

Three things this walkthrough is meant to make obvious: the merchant is never charged for a
period they have already used; the freeze is always preceded by a named date; and the freeze takes
capability, never data — and never the ability to pay.

---

## 4. Data model

### 4.1 `plan_invoices` — new columns

| Column | Type | Notes |
| --- | --- | --- |
| `period_start` | `DateTime(tz)` nullable | First instant of the covered window. `NULL` only on pre-migration rows. |
| `period_end` | `DateTime(tz)` nullable | Exclusive end. **Settlement sets `next_billing_at = period_end`** — one rule for both a first purchase and a renewal. |
| `due_at` | `DateTime(tz)` nullable | When payment is expected. Drives every reminder threshold and the grace clock. |
| `voided_at` | `DateTime(tz)` nullable | Set when a merchant chooses a different plan (§7.5), or by an operator. |
| `void_reason` | `String(32)` nullable | `downgraded` \| `superseded` \| `grace_expired` \| `pre_lifecycle` \| `operator` |

Constraints:

- **Drop** `uq_plan_invoice_period` (C-06: the calendar-month key both drifts and skips).
- **Add** a **partial** unique index on `(subscription_id, period_start)`, excluding voided
  rows: `WHERE status <> 'void'`. This is the idempotency guard for the sweep — a retried
  or duplicated run raises nothing and writes nothing — and the exclusion is what keeps a
  void from being permanent. A plain unique key on the pair would mean that once an invoice
  for a window was voided, that window could never be invoiced again, because the voided
  row would still hold the key. A void is a historical record, not a claim, so it must not
  reserve a window. Use both `postgresql_where` and `sqlite_where` so the constraint holds
  on whichever backend the suite runs against.
- Rows with a `NULL` `subscription_id` or `NULL` `period_start` do not participate (both
  SQLite and Postgres treat `NULL`s as distinct), which is exactly the behaviour wanted for
  legacy rows.
- `period_month` is **kept**, redefined as a display label derived from `due_at`. It stays
  in the KHQR reference id (`INV-{id}-{period_month}`), the admin list filter and the
  portal table, so those keep working; nothing decided from it any more.

Statuses normalise to: `open` (unpaid, collectible) · `paid` · `void` · `waived` ·
`credited`. Reads treat the legacy `draft` and `issued` as `open` via one shared
`UNPAID_STATUSES` constant; writes emit `open` only. `issued_at` is dropped from the
serializer in favour of the real `due_at`.

### 4.2 `plan_invoice_reminders` — new table

| Column | Type | Notes |
| --- | --- | --- |
| `id` | int PK | |
| `invoice_id` | FK `plan_invoices.id`, not null | |
| `tier` | `String(24)` | `due_3`, `due_1`, `due_today`, `overdue_1`, `overdue_3`, `overdue_final` |
| `channel` | `String(16)` | `in_app`, later `email` |
| `sent_at` | `DateTime(tz)` not null | |
| `detail` | `JSON` nullable | Tier copy version, delivery result |

Unique: `(invoice_id, tier, channel)`.

A table rather than a JSON list on the invoice, for the same reason `uq_plan_invoice_period`
existed: two worker replicas and a retry must converge on one message, and the only
enforcement that actually holds is a database constraint. The `channel` column is what
lets email be added later without re-sending the in-app tier.

**What an `in_app` row means.** An in-app reminder is not a delivery, it is a state: the
banner is rendered from `due_at` whether or not any worker ran. So the row is not what
makes the merchant see the warning — it is the record that the platform *was* warning them
from that hour onward. That asymmetry is deliberate and is the rule this feature follows:

- **Display is derived** from `due_at` and the tier table, so a worker outage can never
  hide a warning the merchant was entitled to see. Fail-open for the merchant.
- **Recording is idempotent** and row-based, so the platform can answer "were they warned
  before we cut them off?" during a dispute, and so a push channel added later cannot
  double-send. Fail-closed for us.

### 4.3 `plan_subscriptions`

No new columns. `next_billing_at` keeps its name; its **meaning is narrowed** to "the
instant the paid coverage ends" and it is now written only by settlement, a chosen downgrade and
reinstatement. `CREDIT_PERIOD` stays 30 days.

### 4.4 Migration `0012_billing_renewal_periods`

1. Add the five nullable columns to `plan_invoices`.
2. Backfill from `created_at` (a real column, so the SQL is portable):
   `period_start = created_at`, `period_end = created_at + 30d`, `due_at = created_at + 30d`.
3. Void every currently-open invoice, `void_reason = 'pre_lifecycle'`.
4. Drop `uq_plan_invoice_period`; create the partial unique index from §4.1.
5. Create `plan_invoice_reminders`.
6. Add `stores.billing_suspended_at`, nullable, no backfill (§4.5).

All through `op.batch_alter_table`, matching `0009`/`0011`.

**Why step 3 is not optional.** A pre-lifecycle open invoice has no reminder history — no
tier rows exist and none can honestly be invented. If it were left open, the first
enforcement run would find its `due_at` (backfilled as `created_at + 30d`) already past and
freeze the account having never sent it a single warning, which is precisely the failure
the Wave 2 → Wave 3 gate exists to prevent. Voiding it retires a claim that predates the
warning machinery, and W3 re-raises for the same window on its next run with a real
`due_at`, so the merchant gets the full tier sequence. The account is not harmed: for a
renewal this happens inside the lead window, and for an abandoned upgrade the invoice was
never collectible in the first place.

(Step 3 voids rows; it does not move `next_billing_at`. Nothing about the migration changes
what any account is entitled to.)

### 4.5 `stores` — one new flag

| Column | Type | Notes |
| --- | --- | --- |
| `billing_suspended_at` | `DateTime(tz)` nullable | Set when the store cap is applied (§7.6); cleared on settlement. |

Added to migration `0012` as a nullable column, so no backfill is needed and no existing
store is affected — every current row is `NULL`, which is the truthful value.

A flag rather than a new `status` value, for the reason in §7.6: `disable_store` overwrites
`status` and `enable_store` has to infer what to restore, so a status field cannot carry
"why". Kept separate from `STORE_DISABLED`, an operator's deliberate abuse decision, so the
two can never overwrite each other and reinstatement cannot accidentally re-enable a store
an operator shut down.

---

## 5. Flows

### 5.1 Renewal invoice issuance — W3, hourly

```
sweep(now):
  subs = SELECT s, p FROM plan_subscriptions s JOIN plans p
         WHERE s.status IN ('trial','active')
           AND p.monthly_fee_cents > 0              -- free plans bill nothing; not swept
           AND s.next_billing_at - lead <= now      -- inside the lead window
           AND NOT EXISTS (invoice i
                           WHERE i.subscription_id = s.id
                             AND i.period_start   = s.next_billing_at
                             AND i.status IN UNPAID_STATUSES)
         ORDER BY s.next_billing_at LIMIT n

  for (sub, plan) in subs:
      invoice = issue_invoice(sub, plan,
                              period_start = sub.next_billing_at,
                              period_end   = sub.next_billing_at + CREDIT_PERIOD,
                              due_at       = sub.next_billing_at)
      if invoice.created:
          notify_ops(invoice)                  -- existing activity feed, same audience
          notify_merchant(invoice, kind="renewal_notice")   -- this IS the T-7 reminder

  commit()                               -- announcements strictly after the commit
```

Two behavioural changes from today, both load-bearing:

- **`next_billing_at` is not advanced here.** The window ends where it ended; only a
  payment moves it. This is what makes a non-payer's lapse visible (invariant 1).
- **The fee filter replaces the "advance even when nothing to bill" rule**
  (`billing.py:236-255`). A free subscription has no period to advance toward, and the
  paid subscription created by an upgrade carries its own date, so a free row's stale
  `next_billing_at` is inert. This also removes the unbounded `while` re-examination.

Because issuance is guarded by "no unpaid invoice for this window" *and* by the unique
index, a retried run, a restarted process and two replicas all converge on one invoice. The
index excludes `void` rows (§4.1), so a window whose invoice was voided can still be raised
again.

The issuance notice is deliberately **not** a tier row and gets no `due_7` entry in
`plan_invoice_reminders`. The invoice's own `created_at` is the record that the merchant was
told, and inventing a tier for it would make the table claim a warning that was never
pending.

### 5.2 Reminder dispatch — W6, hourly (job type `remind`)

Tiers, as offsets from `due_at`:

| Tier | Threshold | Intent |
| --- | --- | --- |
| `due_3` | `due_at - 3d` | renews in 3 days |
| `due_1` | `due_at - 1d` | renews tomorrow |
| `due_today` | `due_at` | due today |
| `overdue_1` | `due_at + 1d` | payment overdue |
| `overdue_3` | `due_at + 3d` | overdue; service continues for now |
| `overdue_final` | `due_at + 6d` | **frozen tomorrow** |

There is no `due_7` tier: issuance at `D-7` already carries that message, so a tier there
would send the merchant two notices in the same hour. Issuance is therefore the **first
derived state**, not a tier — one function, used by both the reminder job and the notice
endpoint:

```
derived_state(account, invoice, now) =
    "frozen"            when the account is `restricted`                   -> critical
    "issuance"          when the invoice is open and `now < due_at - 3d`   -> info
    last reached tier   otherwise                                          -> per the table
```

Because both callers share it, the banner and the recorded tiers cannot disagree about what
the merchant is being told.

```
remind(now):
  invoices = SELECT i, s, p FROM plan_invoices i
             JOIN plan_subscriptions s ON s.id = i.subscription_id
             JOIN accounts a          ON a.id = i.account_id
             JOIN plans p             ON p.id = s.plan_id
             WHERE i.status IN UNPAID_STATUSES
               AND i.due_at IS NOT NULL
               AND s.status IN ('trial','active')   -- upgrades (pending) are not dunned
               AND a.status = 'active'              -- a suspended account is not dunned
             ORDER BY i.due_at LIMIT n

  for (invoice, sub, plan) in invoices:
      tier = last tier in TIERS where threshold(tier, invoice.due_at) <= now
      if tier is None: continue
      if record_exists(invoice.id, tier, 'in_app'): continue
      insert_reminder(invoice.id, tier, 'in_app')   -- unique key; a duplicate is a lost race
      notify_merchant(invoice, tier)                -- push channels only; no-op under D2
```

**What recording is, and is not.** The row is written the moment the tier is reached, and
for `in_app` that is the entire operation: the banner is derived from `due_at` (§4.2), so
there is no message to deliver and nothing that can fail after the insert. Two replicas
racing produce one row and one notification, because the loser's insert violates the unique
key. A push channel added in Phase B is where delivery state and bounded retries become
necessary, which is why `channel` is part of the key from the start.

**Why "most urgent reached" and not "every tier reached".** A sweep that was down for four
days must not deliver four stale reminders at once. Selecting the *last* threshold that
has passed collapses the backlog into the single message that is true right now, and the
tier pointer only ever moves forward, so a skipped tier can never be replayed later. The
`overdue_final` tier therefore always precedes the freeze by one day, which is the
"warn before a destructive transition" rule this codebase already applies to operator
actions.

**The one hard rule for every channel: never embed a QR.** ABA's session is 180 s
(`routers/billing.py:365-375`), so a code inside a message opened an hour later is dead on
sight, and re-issuing on demand is the only behaviour that is reliably payable. Every
notice therefore ends at a **deep link** — `/dashboard/billing?pay={invoice_id}` — and the
billing page mints a fresh code when it loads.

### 5.2.1 The notification flow, step by step

```
 1  CLOCK     hourly heartbeat, dedup key `billing-remind:{YYYY-MM-DDTHH}`
              (same shape as w3_billing.billing_heartbeat_dedup_key)
                 │
 2  QUEUE     enqueue {type: "remind"} on the billing.lifecycle queue
                 │
 3  SELECT    invoices: status IN UNPAID_STATUSES, due_at NOT NULL,
              subscription in (trial|active)      -> excludes upgrades
              account status = active             -> excludes suspended accounts
                 │
 4  DECIDE    state = derived_state(invoice, now)   (§5.2, shared with the notice endpoint)
              "issuance"               -> nothing to record; the invoice row is the record
              a tier, already recorded -> skip
              a tier, unrecorded       -> go to 5
                 │
 5  RECORD    INSERT plan_invoice_reminders (invoice_id, tier, 'in_app');
              a unique violation means a sibling replica got there — stay silent.
              For in_app this is the whole operation: nothing is delivered after it
                 │
 6  DISPLAY   the banner is rendered from `due_at` by /v1/billing/notices,
              independently of step 5 — an outage in 1-5 cannot hide a warning
                 │
 7  ACT       CTA -> /dashboard/billing?pay={id} -> InvoicePaymentModal
              -> GET /v1/billing/invoices/{id}/khqr   (an existing route)
                 │
 8  SETTLE    /pay/{public_id}/status polls -> W1 detects -> mark_paid
              -> billing.settle_invoice  (§5.3)
                 │
 9  STOP      invoice is `paid`; no tier is derivable any more, so the banner
              clears on the next poll. The tier rows stay as the audit trail.
```

Steps 5 and 6 being independent is the whole point of the split in §4.2, and it is what
makes step 9's "the banner clears" safe: the banner is driven by the invoice's status, not
by the worker's bookkeeping.

### 5.2.2 Tier copy

Copy lives with the other message builders in `services/notifications.py`, so one tier
cannot read differently depending on who sent it, and the notice endpoint returns the same
strings the workers record. Worked for a $9.99 Starter renewal due Oct 12:

| Tier | Level | Title | Body | CTA |
| --- | --- | --- | --- | --- |
| **issuance** *(not a tier)* | info | Your Starter plan renews on Oct 12 | $9.99 for Oct 12 – Nov 11. Pay any time before then. | Pay now |
| `due_3` | info | Your Starter plan renews in 3 days | $9.99 for Oct 12 – Nov 11. Pay any time before Oct 12. | Pay now |
| `due_1` | info | Your Starter plan renews tomorrow | $9.99 for Oct 12 – Nov 11. | Pay now |
| `due_today` | warning | Your Starter plan is due today | Pay today to keep Starter active. | Pay now |
| `overdue_1` | warning | Your payment is overdue | Your plan is still active. Settle $9.99 to avoid an interruption. | Pay now |
| `overdue_3` | warning | Your payment is 3 days overdue | Starter is still active. On Oct 19 your account will be frozen and your stores will stop generating payment codes. | Pay now |
| `overdue_final` | critical | Your account will be frozen tomorrow | Pay $9.99 today. From tomorrow your stores cannot generate new payment codes and the dashboard becomes read-only, until the invoice is settled or you move to a plan you can afford. | Pay now |
| **`frozen`** *(account state, not a tier)* | critical | Your account is on hold | New payment codes are stopped and the dashboard is read-only. Settle $9.99, or move to a plan you can afford — both are one tap. Your data is untouched either way. | Resolve billing |

The `overdue_final` body names the date and what **stops**, and the `frozen` body says what is
**kept** — "your data is untouched" — because a merchant whose business just went dark needs to
know the difference between a hold and a deletion. Both are rendered from the same plan and
status data the enforcement uses, so the warning cannot describe a consequence the sweep does not
apply. `frozen` is derived from the account rather than from any invoice, and outranks every
tier: it is the one thing a frozen merchant needs to see.

### 5.2.3 Display and dismissal

- The banner shows the single most urgent open notice; `info` is dismissible per
  `(invoice_id, tier)` in `localStorage`, `warning` and `critical` are not. Persisting that
  server-side would mean a schema change to remember a scroll-past, and a dismissal that
  survives a cleared browser is not worth one.
- The billing page lists every invoice with its own `Due <date>` and badge, so a dismissed
  banner never becomes a hidden debt — it is always one click away, on the page whose whole
  job is billing.
- One poll per dashboard page load, plus the existing payment-status polling. No new
  background timer in the browser.

### 5.3 Payment — settlement, both paths

`mark_paid` → `settle_invoice_for_payment` (already runs **before** the commit, so payment,
invoice and plan change are durable together — keep that).

```
settle(invoice, payment, paid_at):
    if invoice.status == 'paid': return

    if paid_at <= invoice.due_at + grace:
        # (a) paid before the freeze: the window the invoice already describes
        invoice.status = 'paid'; invoice.paid_at = paid_at
        subscription.next_billing_at = invoice.period_end     -- anniversary preserved
    else:
        # (b) paid after the freeze: that window is gone, so this invoice is NOT paid
        cancel the account's current subscription(s)
        create subscription(plan = invoice's plan, status = 'active', started_at = paid_at,
                            next_billing_at = paid_at + CREDIT_PERIOD)
        granted = create invoice(subscription = that new subscription,
                                 period_start = paid_at,
                                 period_end   = paid_at + CREDIT_PERIOD,
                                 due_at       = paid_at,
                                 chmabapay_payment_id = payment.id,
                                 status = 'paid', paid_at = paid_at)
        void(invoice, reason = 'grace_expired')   -- nothing was collected against it
        audit('billing.reinstated', {lapsed_invoice, granted_invoice, gap_days, source})

    account.status = 'active'                  -- unfreeze (§7.9) — both branches
    clear billing_suspended_at                 -- restore the store cap — both branches
```

**The discriminator is the freeze, not the subscription's status.** `paid_at <= due_at + grace`
is the same line the enforcement job uses, so "before the freeze" and "after the freeze" mean
exactly one thing on both sides. Keying on `subscription.status` would be wrong under this
model: the freeze is account-level and deliberately leaves the subscription `active` (§7.2, so
that unfreezing is one status write), which means a subscription-status test would send every
frozen merchant down branch (a) — and a merchant frozen for a month would pay and *lose* that
month, because branch (a) preserves an anniversary that has already passed.

Unfreezing and releasing the store cap are common to both branches: whichever way they paid,
the merchant comes back.

**Branch (a) is the whole happy path**: `next_billing_at = period_end`. A first purchase has
`period_end = now + 30d`; a renewal has `period_end = old_end + 30d`. Both fall out of one
assignment, so there is no proration branch to get wrong.

**Branch (b) is the answer to "they paid on day 16 — what do we bill?"** The granted period
starts at `paid_at`, not at the original due date:

- The merchant did not have the plan between `D` and `paid_at` — the account was frozen and
  could not take a single payment — so billing them from `D` charges for service they never
  received.
- Metering the gap would need a proration engine driven by day-counts, producing a number like
  `$29.99 for 15 days` that a merchant paying by QR cannot reconcile with anything they were
  shown. One number, or no number.

The 15 days are written off deliberately. The amount is small next to the cost of explaining
it, and a merchant returning after a lapse is worth more than the gap.

The lapsed invoice is voided (`grace_expired`) rather than flipped to `paid`: it describes a
window the merchant never received and against which no money was collected. Marking it paid
would book revenue into the month of a period that was never served. **The invariant this buys:
every `paid` invoice's `period_*` is a window the merchant was actually entitled to**, which is
what keeps a revenue report readable a year later — and why the granted invoice names the
subscription created for it, rather than the canceled one whose window it replaced. The
payment's `chmabapay_payment_id` moves from the void invoice to the granted one inside the same
transaction — which is why `settle_invoice_for_payment` cannot only flip a status, it has to
perform this handoff.

**Day 6 against day 16, side by side:** paying at `D+6` leaves the anniversary alone and gives
six grace days free; paying at `D+16` restarts the clock from the payment. Both follow from one
rule — *the merchant is billed for the period they actually receive* — and that is the only
rule that has to hold.

This function is currently split between `settle_invoice_for_payment`,
`activate_subscription` and the admin `resolve` route, and none of the three unfreezes the
account or handles a subscription the freeze has already stopped. Consolidating them into one
`billing.settle_invoice(session, invoice, *, paid_at, source)` is part of the work.

### 5.4 Grace enforcement — W6, hourly (job type `enforce`)

```
enforce(now):
    if not settings.billing_enforce_enabled:
        log the freezes that WOULD happen; change nothing      -- ships off (§8)

    due = SELECT i, s, a FROM plan_invoices i
          JOIN plan_subscriptions s ON s.id = i.subscription_id
          JOIN accounts a           ON a.id = i.account_id
          WHERE i.status IN UNPAID_STATUSES
            AND i.due_at IS NOT NULL
            AND i.due_at + grace <= now
            AND s.status IN ('trial','active')       -- only coverage actually in force
            AND a.status = 'active'                  -- already frozen or suspended: leave it
            AND s.account_id NOT IN (platform admins)   -- decision D4

    for (invoice, sub, account) in due:
        account.status = 'restricted'                -- §7.2: freeze, change nothing else
        audit('billing.account_frozen', {...})
        notify_activity(format_account_frozen(...))   -- the same feed that announces a raised
                                                      -- invoice (§5.7); after the commit

    # parked purchases do not linger forever, or an unpaid upgrade blocks the next one
    for sub in pending subscriptions where created_at + 30d <= now:
        sub.status = 'canceled'; sub.canceled_at = now
        void(sub's open invoice, reason='superseded')
```

**The invoice is not voided here, and no plan is changed.** The debt is what the freeze is keyed
on, and paying it is one of the three ways out (§7.5) — voiding it would remove the very thing
the merchant is being asked to settle. It is voided later, and only if they choose a different
plan. `downgrade_to_free` and `apply_store_cap` are **not** called from this job at all; they
belong to the merchant's own choice.

**No store row is touched**, because the freeze is account-level (§7.2). That is what makes an
unfreeze a single status write, and why this job cannot leave a merchant half-restored.

Idempotency comes from the account-status guard: a second run finds the account already
`restricted` and skips it.

An operator can resolve an invoice by hand — `mark-paid` unfreezes as it settles — and can also
freeze or unfreeze directly. That is the escape hatch for the common local case where a merchant
pays by bank transfer and nobody settles the invoice, which would otherwise leave them frozen
with no in-product way out.

### 5.5 Upgrade invoice — the `pending` branch

An upgrade invoice is **not** a renewal: nothing is in force, so nothing can lapse. It is
carried by a `pending` subscription, which is exactly the condition the reminder and grace
queries filter on. Consequences, all deliberate:

- No dunning tiers and no downgrade for an upgrade invoice. The merchant is on Free (or on
  their old plan) and loses nothing by not paying.
- Surfaced in-app only — the billing page and `GET /v1/billing/notices`.
- `change-plan` refuses a new purchase while an unpaid invoice exists
  (`409 open_invoice_unpaid`), which is exactly why the 30-day abandonment in §5.4 matters:
  without it, one abandoned upgrade would block every future purchase on the account.
- Abandoned after 30 days (§5.4), voiding the invoice `superseded` and releasing that guard.

### 5.6 Failure and duplicate-run behaviour

| Situation | Outcome |
| --- | --- |
| Two replicas run `remind` simultaneously | Unique `(invoice_id, tier, channel)` — one message, the loser raises and skips. |
| Worker down 4 days across the due date | One reminder (`overdue_1` or later by then), not four. |
| Worker down across `D-7` | The invoice is raised late, in the run that recovers. The issuance notice is the merchant's first warning and the tier clock is already advanced, so the sequence degrades to fewer notices, never to wrong ones. |
| `next_billing_at` years in the past (bad import, clock jump) | The lead window is satisfied, one invoice is raised for that stale window, its `due_at` is already past, and the enforce job freezes the account. Converges in one cycle — no loop, so `MAX_CATCH_UP_PERIODS` is no longer load-bearing. |
| Payment arrives while `enforce` is mid-run | Settlement and enforcement take the invoice row; whoever commits second sees the other's status and no-ops. The guard is the status field in both directions. |
| Reminder channel unavailable | An `in_app` tier has no delivery to fail — the record is the write and the banner is derived, so a merchant is never worse off for a worker outage. Email is Phase B (T-16) and will need delivery state and a bounded retry on its own `channel` row; being in the key is what allows that without touching the in-app rows. |
| Operator voids an invoice for a window that is still in force | The partial unique index lets W3 raise the window again on its next run. With a plain unique key the voided row would hold the key and the window could never be billed. |

### 5.7 Operator flow

1. The ops activity feed already announces a raised invoice and now also announces a freeze.
   Same channel, same audience — no new surface for the team to watch.
2. Admin → Invoices (`web/admin/app/invoices/page.tsx`) gains a `due_at` column and an
   overdue filter, so an operator can see what is about to lapse without opening each row.
3. Five manual outcomes on the existing `POST /v1/admin/invoices/{id}/resolve`:
   - **mark-paid** — settles, and runs the same `settle_invoice` as the QR path, so a bank
     transfer quoted over the phone leaves the account in exactly the state a QR payment
     would, unfrozen. This is the common case in a market where merchants pay by transfer, and
     it is the single most important operator action in this feature: without it a merchant who
     paid by transfer stays frozen with no in-product way out.
   - **waive** / **credit** — unchanged; neither writes `paid_at`, so neither counts as income.
     Both must also unfreeze, since the debt they resolve is what the freeze is keyed on.
   - **void** (new) — retires the claim and grants nothing. An operator sometimes knows an
     invoice should never have been raised.
4. Order matters, and both directions work: resolving before the grace expiry prevents the
   freeze, and resolving after it unfreezes the account, because `mark-paid` runs the same
   settlement path as a QR payment.
5. An operator can also freeze or unfreeze directly, for the case where the state and the money
   have got out of step and neither a payment nor a resolution is the right instrument.

---

## 6. Notification surfaces

**Phase A — in-app (no new infrastructure).** The only channel that can ship without a
provider decision, and the merchant is already inside the portal.

| Surface | Content |
| --- | --- |
| Banner in the portal shell (`web/landing/components/portal/DashboardShell.tsx`), every `/dashboard/*` page | Most urgent open notice. `info` for the issuance notice and `due_3`/`due_1`, `warning` at `due_today`/`overdue_1`/`overdue_3`, `critical` at `overdue_final` and while `frozen`. |
| Read-only mode while frozen | Every settings page renders with its inputs disabled and the banner's CTA pointing at billing. The mutation routes refuse with `account_restricted` regardless, so the UI state is a courtesy and the gate is the enforcement. |
| Billing page (`web/landing/app/dashboard/billing/page.tsx`) | Per-invoice `Due <date>`, status badge `Open` / `Overdue 3d` / `Paid` / `Void`, and the existing Pay action. |
| Dashboard home plan card | "Renews `<date>`" or "Payment overdue". |

New endpoint `GET /v1/billing/notices` returns at most one notice with server-computed
`days_until_due` and copy, so the wording cannot drift between client and server. The field
is `state`, not `tier`, because it is `issuance` or a tier:

```
{ "notices": [ { "level": "warning", "state": "overdue_1", "title": "Your payment is overdue",
                 "body": "Your plan is still active. Settle $9.99 to avoid an interruption.",
                 "action_label": "Pay now", "action_url": "/dashboard/billing?pay=12",
                 "dismissible": false, "invoice_id": 12, "period_label": "2026-10",
                 "amount_cents": 999, "amount_formatted": "$9.99",
                 "due_at": "...", "days_until_due": -1 } ] }
```

Three of those fields exist so the portal owns no policy of its own: `level` is the styling
decision, `dismissible` is the §5.2.3 dismissal rule, and `title`/`body`/`action_label` are the
copy — all computed server-side, so adding a tier is one server change rather than a server
change plus a client release. `{"notices": []}` is the calm answer, and it is an empty list
rather than a reassuring banner: a merchant who owes nothing needs no interruption.

The action URL opens the existing `InvoicePaymentModal`, which already mints a live code.

**Phase B — email, when a provider exists.** `plan_invoice_reminders.channel = 'email'`,
one template per tier, same deep link, no embedded QR. Requires a provider and credentials;
no mailer exists in the codebase today (C-09).

**Deliberately not used:** merchant Telegram. `store.telegram_chat_id` is per-store and
carries sale notifications; dangling billing dunning off it would mix audiences and reach
the wrong chat.

**Operator feed:** unchanged. An invoice being raised, an account being frozen and an account
being unfrozen all continue to post to `ACTIVITY_TELEGRAM_CHAT_ID`.

---

## 7. When an account lapses

### 7.1 Downgrade alone is toothless

The plan deltas (`db.py:63-123`): Free 1 store / 1 key / 3,000 payments · Starter 5 / 3 /
15,000 · Pro 50 / 10 / 1,000,000.

Every limit is enforced **at create time only** (C-11) — `_enforce_max_stores`
(`routers/stores.py:48-73`), `check_plan_quota` (`services/payments.py:382-412`). So a
merchant who downgrades from Pro keeps **all 50 stores minting codes and taking money**. The
downgrade costs them nothing they were already using, which means it creates no pressure to
pay at all. This is the leak §1 called out as larger than the reminder cadence; the store cap
below is what closes it.

### 7.2 The rule: freeze the account, do not silently downgrade it

At `due_at + grace` the account becomes **frozen** — `account.status = restricted` — and is
not downgraded:

| | While frozen |
| --- | --- |
| Session | Works. Log in, log out, read anything of theirs. |
| Writes | Refused. The settings pages render read-only. |
| New payment codes | **None, from any store.** The freeze is account-level, so not one store row is touched. |
| API keys | Refused with `403 account_restricted`. |
| Still running | Detection and webhook delivery, so a payment already in a customer's hand still settles. |
| The debt | Survives. The invoice stays `open` — it is what the freeze is keyed on, and paying it is one of the ways out. |

Nothing consequential is decided on the merchant's behalf: their plan stays as it was, their
stores stay as they were, and the only way forward is the billing page (§7.5).

### 7.3 Why freeze rather than auto-downgrade

Two reasons, and the second is the one that decided it:

- **Consent.** Silently moving a merchant to Free changes their plan and revokes capability they
  never agreed to give up. A freeze is honest about what is happening — the account is on hold
  — and leaves the decision with them.
- **A lapse recovered as a downgrade is worth more than a lapse written off as Free.** A
  merchant who cannot pay $59.99 may well pay $9.99 for Starter. Auto-downgrading to Free gives
  that away for $0 and destroys the exact moment they would have chosen Starter. Freezing turns
  a lapse into a sale; auto-downgrade turns it into churn.

The cost is real and should be named: **while frozen, all of the merchant's stores stop**, so
their own customers see dead codes. That is the price of the stronger pressure, and it is
acceptable only because the merchant can end it in one tap — by paying, or by choosing a plan
they can afford.

### 7.4 The new state, and why it is not `suspended`

`restricted` is a third account state, and it cannot reuse `suspended`:

- `get_current_session_account` raises **401 whenever the status is not `active`**
  (`routers/auth.py:385-386`). A suspended account **cannot sign in at all**, so it cannot reach
  `/dashboard/billing` — the one page that can clear the debt. Reusing it would lock the merchant
  out of the collection path.
- A code minted before the freeze dies in 180 s (`routers/billing.py:365-375`), so there may be
  no payable code in their hands either.
- `suspended` already means something else and is operator-only: an abuse or review decision
  (`routers/admin.py:450-483`) or self-erasure (`routers/account.py:365`). Overloading it would
  make "we are reviewing your account" indistinguishable from "you owe $9.99" — for the merchant
  and for the audit trail.

**Enforcement lives in two choke points, not sixty routes.** Both already receive the `Request`:

| Choke point | Covers |
| --- | --- |
| `get_current_auth_context` (`auth.py:99-110`) | stores (via `_require_store_manage`), keys, payments, webhooks, khqr, transactions, reports |
| `get_current_session_account` (`routers/auth.py:365-387`) | account settings, billing |

The rule: `active` → everything. `restricted` → GET/HEAD plus an allowlist
(`GET /v1/billing/*`, `POST /v1/billing/change-plan`, and the invoice-KHQR mint, which is how
the debt gets paid). Everything else → `403 account_restricted`. `suspended` keeps its 401
lockout. `resolve_key_context` (`auth.py:54-55`) refuses `restricted` outright, which freezes
the entire API in one line.

**One read is refused, because it is not a read.** `GET /v1/transactions/check-status/{id}`
settles the payment it polls — its handler reaches `status_reconciler` → `mark_paid`. It is
excluded from the read allowance (T-22), and nothing is lost by that: settlement is W1's job on
its own sweep, so a code a customer is still holding settles exactly as §7.6 requires. What
stops is the *merchant* driving writes.

The allowlist is a constant, not a judgement call per route, and it is asserted against the
app's own route table: every mutating route reachable by a merchant must answer
`403 account_restricted` except `POST /v1/billing/change-plan`. Both constants are checkable
because they are real paths — a typo would otherwise leave a hole no test could see.

**The guarantee is a test, not care.** Enumerate the app's routes and assert that every mutating
method returns 403 for a restricted session except the allowlist — the shape of the existing
`test_every_operator_action_is_admin_gated`. A new route that forgets the rule then fails the
suite instead of quietly working for a frozen merchant.

### 7.5 Resolving the freeze: three outcomes on the billing page

| Choice | What happens | Unfrozen |
| --- | --- | --- |
| **Pay the invoice** | `settle_invoice` runs as normal — branch (a) if the window is still theirs, branch (b) if it lapsed (§5.3). | On settlement. |
| **Downgrade to Free** | The unpaid invoice is voided (`downgraded`). No money changes hands, because Free costs nothing. | Immediately, as an ordinary Free account with its 1 store. |
| **Downgrade to a paid tier** | The unpaid invoice is voided; an invoice is raised for the chosen plan's period; the freeze lifts when that settles. | On settlement — usually a minute later. |

**Why the old invoice is voided in every branch.** The freeze is keyed on an open overdue
invoice. If a merchant chose Starter and the Pro invoice stayed open, they would stay frozen
*while on Starter* — permanently, because the thing blocking them is a plan they have already
declined to buy. Choosing a smaller plan is a resolution, not an evasion: the platform writes
off a period it was never going to collect, and in the Starter case collects $9.99 it would
otherwise have lost to churn.

**The one-tap path is the point.** The banner links to the billing page with the invoice
preselected, and the payment modal mints a fresh code on load — never a stale one (§5.2). A
frozen merchant who has to hunt for the way out is a merchant who phones support instead.

**Choosing a paid tier retires the lapsed subscription with the invoice.** Voiding frees the
`(subscription_id, period_start)` key, so a subscription whose coverage has already ended would
have W3 raise that same invoice again on its next run — and the merchant would be back at the
same click, owing the same period, an hour later. The lapsed row is therefore canceled as part
of the choice (`billing.retire_live_subscriptions`), and the purchase is parked as `pending`,
which grants nothing until it is paid. Nothing is lost: their plan returns the moment the money
does.

**Choosing Free voids unpaid invoices whether or not the account is frozen.** A merchant in the
renewal window who clicks Free is leaving the plan that invoice bills for, so the claim goes
with it. Left open it would sit on the account as a debt for a plan nobody is on, blocking the
next purchase through `find_open_invoice` — the same trap, reached without a freeze.

The two branches are the §7.4 allowlist's whole reason for existing: with reads open and these
two writes open, a frozen merchant can do everything needed to stop being frozen, and nothing
else.

### 7.6 The store cap — and only for a chosen downgrade

The freeze is account-level and touches no store row (§7.2), which is what makes unfreezing a
one-line status change. A merchant who *chooses* Starter while holding 50 stores is a different
situation: they are unfrozen, on a plan that allows 5. That is where the cap applies, and it
applies on any drop in entitlement — chosen here, or a voluntary `change-plan`.

**A flag, not a status.** Add `stores.billing_suspended_at` (nullable timestamp) beside
`is_internal`, rather than a new `status` value. `disable_store` *overwrites* `status`
(`services/stores.py:266`) and `enable_store` then has to **infer** what to restore from
whether a payment link exists (`services/stores.py:290-309`) — the status field loses the
"why", so a restore is a guess. With a flag: a billing hold and an operator's abuse disable
cannot overwrite each other, and "clear exactly what billing set" is one condition.

**The cap applies on any drop in entitlement** — a chosen downgrade here, or a voluntary
`change-plan`, which is self-serve and immediate (`routers/billing.py:200-224`). Without the
rule, a Pro merchant could click downgrade-to-Free and keep all 50 stores running for free: the
same free ride as not paying, reached through a menu instead of a lapse. One entitlement deserves
one rule. The downgrade stays instant and frictionless — the existing principle that making a
merchant wait to leave a paid plan would trap them on it is untouched, because the cap enforces
the plan they chose rather than delaying the choice.

**Which resources survive, and letting the merchant choose.** The default is applied
deterministically in the same transaction as the downgrade — the oldest `max_stores` stores — so
the account is never left over its allowance while a merchant dithers, and a re-run is idempotent.
The billing page then offers a **chooser**: which 5 of the 50 stores stay live. Until they choose,
the deterministic default holds, so the downgrade never blocks on a decision — and a merchant whose
real business is a store the platform did not happen to pick is not stranded, which matters because
that store is the income that funds their next payment.

**Keys and webhooks are deliberately not capped — D14.** A key is a credential, not capacity:
`create_payment` refuses a held store regardless of which key presents it, so extra keys on a
capped account can no longer mint anything. Revoking them would therefore add no enforcement at
all, while breaking a live integration the merchant's own systems depend on — the one kind of
damage that turns a dunning conversation into a churn. Webhooks are the same case inverted: a
merchant's webhook endpoint has to keep receiving events for payments that settle during the
window, so capping them would suppress the platform's own notifications. The store allowance is
the lever the plan actually sells; that is where the cap belongs.

**Distinct error code.** `create_payment` refuses a billing-suspended store with
`store_billing_suspended`, not the existing `store_disabled` (`services/payments.py:145-146`).
An integrator has to be able to tell "my merchant switched this off" from "the platform is
holding this store".

**In-flight payments are untouched.** The check sits in `create_payment`; detection runs over
payments, not stores. A customer already holding a code still pays, and the merchant still
receives the money. Stranding a payment that is already in someone's hand would be
indefensible and would also create refund exposure against the merchant.

**Not touched:** API keys, webhooks, payment records, history, exports. Revoking keys breaks a
live integration for no revenue benefit — the store cap is the lever the plan sells. Leaving
them also means reinstatement needs no re-issuing dance.

**UI:** the store list marks suspended rows "Suspended — plan limit" with a link to billing and
the slot-swap action. A merchant who cannot see *why* 49 stores stopped will call support
instead of paying.

### 7.7 The quota month-boundary trap

`check_plan_quota` counts the **calendar month** and compares it to the active plan's
`base_payments_included` (`services/payments.py:382-412`). A merchant who settles 40,000 payments
and then chooses a smaller plan mid-month is instantly over that plan's allowance and blocked
from taking *any* payment until the first of the next month — a total stop arrived at by
accident, and one the merchant cannot reconcile with anything they were shown.

**Recommendation:** the new allowance takes effect at the **next calendar-month boundary**; the
store cap is the immediate lever. Throughput is already drastically reduced by the cap, so
deferring the quota costs little and removes an unexplainable cliff.

### 7.8 Free is never frozen

Free is an ordinary plan. A Free account signs up, runs its 1 store, takes payments up to 3,000 a
month, and is **never frozen** — because freezing is the consequence of an unpaid *debt*, and
Free creates none. `apply_store_cap` is likewise a consequence of choosing a plan smaller than
your current usage, which by definition cannot happen to an account already at or under the Free
allowance.

The frozen state and the plan are orthogonal: `status` is about an unpaid invoice, `plan` is
about entitlement. Conflating the two is what made "Free has 0 stores" look like an option — it
is not one. **0 stores describes the frozen state, not a plan.**

### 7.9 Unfreezing restores exactly what billing took

Clearing `restricted` back to `active` restores everything, because the freeze never changed
anything: the plan is what it was, and not one store row was touched. Separately,
`settle_invoice` clears `billing_suspended_at` on any stores carrying it — so a store an operator
disabled for abuse stays disabled, because its flag was never set. Nothing is re-created,
re-issued or re-chosen.

---

## 8. Configuration

| Setting | Default | Why it is deployment-tunable |
| --- | --- | --- |
| `billing_lead_days` | 7 | How far ahead the invoice is raised = how long the merchant has to pay. |
| `billing_grace_days` | 7 | Policy, and the number support will be asked about. |
| `billing_enforce_enabled` | **false** | The switch on the one irreversible step. Ships off, so the freeze can be observed in production (tiers recorded, notices shown) and turned on only after real merchants have received a full `overdue_final` cycle. |

Tier offsets and the 30-day credit period stay module constants in
`services/billing.py`, next to `CREDIT_PERIOD`, with the tier table in one place.

---

## 9. Edge cases

| Case | Expected |
| --- | --- |
| Pays on `D+6`, inside grace | Invoice `paid`, `next_billing_at = period_end` (anniversary preserved), grace days free, never frozen. |
| Pays on `D+8`, after the freeze | Unfrozen, plan active from `paid_at` for a fresh 30 days, `billing.reinstated` audited. Nothing to restore — the freeze changed nothing. |
| Two paid subscriptions parked against one invoice | Impossible — invariant 2, plus the partial unique index. |
| Free account | Never invoiced, never reminded, never frozen, no store ever held. Free is an ordinary plan (§7.8). |
| Upgrade while a renewal invoice is open | `change-plan` refuses with `409 open_invoice_unpaid` and points at the open invoice. Paying for two periods through one QR flow is more confusing than a redirect; recorded as a deliberate UX trade-off. |
| Merchant dismisses the banner | The notice is dismissible for `info` only, and the billing page still lists the invoice with its due date and badge. Dismissal never hides a debt from the page that exists to show it. A `frozen` notice is not dismissible. |
| Operator suspends the account | Every guard stops, including the freeze (`a.status = 'active'` guard) — and `suspended` outranks `restricted`, so an abuse lockout is never softened by a billing state. The operator's action is the message; dunning on top of it is noise. |
| Frozen merchant tries to write | `403 account_restricted`, except the billing allowlist. Reads still work. |
| Frozen merchant's customers | A code issued before the freeze still settles — detection and webhooks are internal. New codes are refused at `create_payment`. |
| Operator voids an invoice for a window that is still in force | The partial unique index lets W3 re-raise it; with a plain unique key the voided row would hold the key and that window could never be billed again. |
| Pre-migration open invoices at deploy | Voided by the migration (`pre_lifecycle`) and re-raised by W3 with a real `due_at`, so no account is frozen without having first been warned (§4.4). |
| Invoice paid by the bank after the invoice was voided | The payment still settles it, because `get_invoice_khqr` only refuses `paid` — and now that is intentional rather than incidental (§5.3 reinstatement). |
| Platform admin's own plan comes due | Never auto-frozen (decision D4). |
| Pro with 50 stores is frozen | All 50 stop generating codes at once and the dashboard is read-only; not one store row is touched, so paying restores all 50 instantly (§7.2). |
| Pro with 50 stores *chooses* Starter | Not frozen — a normal Starter account, with the oldest 5 stores active by default and a chooser to change which 5 (§7.6). |
| Frozen merchant chooses Free | Pro invoice voided (`downgraded`), unfrozen, and an ordinary Free account: 1 store, 3,000 payments a month. The platform writes off a period it was never going to collect (§7.5). |
| Chosen-downgrade mid-month with 40,000 payments already settled | The new allowance begins at the next calendar-month boundary, so the store cap is the immediate reduction. Enforcing 3,000 at once would stop every payment for the rest of the month with nothing in the notices to explain why (§7.7). |
| Merchant re-picks which stores stay | Exact at every instant: the count never exceeds the allowance, and the swap is one transaction (§7.6). |
| Pays 15 days late | Granted 30 days from `paid_at`, the gap written off, and the lapsed invoice left `void`. No partial-period invoice (§5.3). |
| Displayed dates | Stored and compared as UTC instants; rendered in `Asia/Phnom_Penh`. No day-boundary arithmetic anywhere in the logic. |

---

## 10. Decisions required

| ID | Decision | Recommendation |
| --- | --- | --- |
| D1 | What happens at the end of grace | **Freeze the account** — `status = restricted`: read-only, no store can generate a code, API refused. Not a downgrade; the plan and the debt stay put. |
| D2 | Reminder channel for the first release | **In-app only**; email is Phase B and needs a provider choice. |
| D3 | Lead / grace / tiers | **7 / 7**, tiers at `D-3, D-1, D, D+1, D+3, D+6`. |
| D4 | Exempt platform-admin accounts from auto-freeze | **Yes** — the HQ store and plan-fee collection hang off that account, and the failure mode is the platform unable to collect its own revenue. |
| D5 | Pre-migration open invoices at deploy | **Void them in the migration** (`pre_lifecycle`). They carry no reminder history, so leaving them open would let the first enforcement run freeze an account that was never warned — the exact failure the Wave 2 → Wave 3 gate exists to prevent. See §4.4. |
| D6 | Keep `period_month` | **Yes, as a display label** — it is in the KHQR reference id, the admin filter and the portal table. |
| D7 | Freeze, or downgrade automatically | **Freeze.** Consent, and because a lapse recovered as a Starter downgrade beats a lapse written off as Free (§7.3). |
| D8 | Which resources survive a chosen downgrade | **The oldest N by default, then a chooser** — deterministic and idempotent, and the merchant is never stranded on a resource the platform happened to pick (§7.6). |
| D9 | Quota timing after a downgrade | **The new allowance from the next calendar-month boundary**; the store cap is the immediate lever (§7.7). Without it, a mid-month downgrade is an accidental total stop no notice warned about. |
| D10 | What a reinstatement bills | **A new invoice for the window actually granted**, from `paid_at`; the lapsed invoice stays `void` (§5.3). Keeps "paid" meaning "paid for service received" and stops revenue landing in a month that was never served. |
| D11 | The account state for a non-payer | **A new `restricted` state, not `suspended`** — a suspended account cannot sign in at all, so reusing it would remove the very page that can clear the debt (§7.4). |
| D12 | API access while frozen | **Refused wholesale** with `403 account_restricted`. A read-only API in a payment gateway is a half-state that invites silent failures in the merchant's own systems. |
| D13 | Does a frozen account ever resolve itself | **No auto-resolution.** Freezing costs nothing, preserves the debt, and the merchant can return any month. Accepted cost: frozen accounts accumulate and will show up in support queues. |
| D14 | Cap API keys and webhooks on a downgrade | **No — stores only.** A key is a credential rather than capacity: the store cap already stops it minting, so revoking keys adds no enforcement while breaking a live integration. Webhooks must keep delivering for payments that settle during the window (§7.6). |

---

## 11. Out of scope

- Proration on a mid-period upgrade (`change_plan` bills a full period; unchanged).
- **Retroactive billing of a lapsed gap.** The days between `D` and a late payment are written
  off, not invoiced in part (§5.3). Deliberate: a partial-period charge needs a day-count
  proration engine and produces a number the merchant cannot reconcile with anything they were
  shown.
- **Auto-downgrade on non-payment.** Deliberately not built: the account is frozen and the
  merchant chooses (§7.3). An automatic move to Free would also destroy the Starter upsell that
  makes the freeze worth having.
- **An operator-side view of frozen accounts.** They accumulate by design (D13), and a support
  list of "who is frozen, since when, for how much" is useful — but it is not this feature.
- Trials. `trial_ends_at` is never populated and signup goes straight to Free-active, so
  there is no trial→paid conversion flow to re-time.
- Any true recurring charge. KHQR has no mandate; this is not a limitation to work around,
  it is the constraint the whole design serves.
- Paying several periods in advance.
- Late fees, interest, or partial payment of an invoice.
- Choosing and wiring an email provider (Phase B).

---

## 12. Acceptance

| # | Test | Asserts |
| --- | --- | --- |
| A-01 | Invoice is raised at `D-7`, not at `D` | Sweep at `D-8d` writes nothing; at `D-7d` writes one invoice with `due_at = D`. |
| A-02 | The schedule does not advance on issuance | Two consecutive sweeps in the lead window leave `next_billing_at` at `D` and produce one invoice. |
| A-03 | The schedule advances only on payment | After settling, `next_billing_at == period_end` exactly, for both a first purchase and a renewal. |
| A-04 | One reminder per tier | An hourly sweep over a simulated 8-day window yields exactly the six tier rows, one per tier, no row for `issuance`, and no duplicates under a second concurrent sweep. |
| A-05 | Late recovery collapses the backlog | A sweep run for the first time at `D+4` sends exactly one reminder (`overdue_3`). |
| A-06 | Paid invoices are never reminded | Settle the invoice at `D-2`; no tier rows afterwards. |
| A-07 | Upgrade invoices are not dunned | A `pending` subscription's invoice gets no tier rows and survives `due_at + grace`. |
| A-08 | The freeze happens once, at `due_at + grace` | `status = restricted`; the invoice is still `open`, the plan is unchanged, and not one store row was written. A second sweep changes nothing. |
| A-09 | A frozen account is read-only | Reads work; every mutating route except the billing allowlist returns 403 `account_restricted`; every API key returns 403. Asserted across the app's whole route table, not a hand-listed few. |
| A-10 | Unfreezing restores everything | Paying sets `status = active`; all 50 stores mint again with no per-store restore step, because none was ever flagged. |
| A-11 | Free accounts are untouched | No invoice, no tier rows, no notice, never frozen, no store ever held, across a full window (§7.8). |
| A-12 | Notices are server-computed | `/v1/billing/notices` reports `state: frozen` while the account is restricted (outranking every tier), `state: issuance` through the lead window before `due_3`, the most urgent tier after that, and an empty list when nothing is due. |
| A-13 | Constraint holds | Two live invoices for one `(subscription_id, period_start)` raises; the sweep's retry path resolves to the existing row. |
| A-14 | Legacy rows survive | A pre-migration invoice with `period_start IS NULL` does not violate the new index and is treated as `open` by the shared status constant. |
| A-15 | Deploy does not ambush anyone | After `upgrade head`, every previously-open invoice is `void/pre_lifecycle`, no account's `next_billing_at` moved, and W3 re-raises the in-window periods with a `due_at` in the future. |
| A-16 | A void does not reserve a window | Void an invoice for a window whose subscription is still in force; W3 raises a replacement for the same `period_start` without a constraint violation. |
| A-17 | Warnings survive a worker outage | Delete every `plan_invoice_reminders` row for an overdue invoice; W6 re-records the tiers, and `/v1/billing/notices` reports the correct tier either way — display is derived, recording is bookkeeping. |
| A-18 | A chosen downgrade caps the resources | Pro→Starter holding 50 stores: after it settles, exactly 5 mint codes, the other 45 answer `store_billing_suspended`, and all 50 rows survive with their keys, links and history. |
| A-19 | The chooser moves the allowance | Re-picking which 5 stores stay leaves exactly 5 able to mint — never 6, never 4 — across repeated calls and a re-run of `apply_store_cap`. |
| A-20 | In-flight payments survive both states | A payment created before a freeze, and one created before a chosen cap, both still settle and still deliver their webhooks. |
| A-21 | A chosen downgrade restores only what billing took | A later upgrade clears `billing_suspended_at`; a store an operator disabled stays disabled. |
| A-22 | A post-freeze payment bills from the payment | Unfrozen by a late payment: the lapsed invoice stays `void`, a new `paid` invoice covers `[paid_at, paid_at + 30d)`, and `next_billing_at == granted.period_end`. |
| A-23 | Paid means served | On every path — first purchase, renewal, in-grace renewal, post-freeze reinstatement — a `paid` invoice's `period_start`/`period_end` equals the subscription window it bought. |
| A-24 | A frozen merchant can get out three ways | While frozen: paying unfreezes with the plan intact; choosing Free voids the invoice and unfreezes as an ordinary Free account; choosing Starter voids it and unfreezes on settlement. Each ends with `status = active` and no open invoice — the assertion behind invariant 7. |

Plus: `ruff check src tests` clean, and the existing billing/admin suite
(`tests/test_billing_invoices.py`, `tests/test_admin_plans.py`, `tests/test_admin_actions.py`)
still green.

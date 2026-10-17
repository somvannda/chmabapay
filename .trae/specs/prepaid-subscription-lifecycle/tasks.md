# Prepaid Subscription Lifecycle — Tasks

Ordered by dependency. Wave 3 must not ship before Wave 2 is live in production: cutting a
merchant off without the warnings that precede it is the failure this ordering exists to
prevent.

Status values: `pending`, `in_progress`, `complete`, `blocked`.

Legend: **S1** blocker (revenue or correctness) · **S2** major · **S3** minor

---

## Decisions that unblock the later waves

| ID | Decision | Recommended | Blocks |
| --- | --- | --- | --- |
| D1 | What happens at the end of grace | Freeze the account (`restricted`) | T-22, T-09 |
| D2 | First-release channel | In-app only — Phase B (email, T-16) shipped later on Resend | T-08, T-10, T-11 |
| D3 | 7 / 7 / tiers at `D-3, D-1, D, D+1, D+3, D+6` | as stated | T-08, T-09 |
| D4 | Exempt platform-admin accounts | Yes | T-09 |
| D5 | Pre-migration open invoices | Void them in the migration | T-01 |
| D6 | Keep `period_month` as a label | Yes | T-01, T-03 |
| D7 | Freeze, or downgrade automatically | Freeze | T-09, T-22 |
| D8 | Which resources survive a chosen downgrade | Oldest N by default, then a chooser | T-18, T-19 |
| D9 | Quota timing after a downgrade | The new allowance from the next calendar-month boundary | T-21 |
| D10 | What a post-freeze payment bills | A new invoice for the granted window, named against a new subscription; the lapsed one is voided `grace_expired` | T-06 |
| D11 | The account state for a non-payer | A new `restricted` state, not `suspended` | T-22 |
| D12 | API access while frozen | Refused wholesale, `403 account_restricted` | T-22 |
| D13 | Does a frozen account auto-resolve | No | T-09 |
| D14 | Cap API keys and webhooks too | No — stores only | T-18 |

---

## Wave 1 — Periods, due dates, advance issuance and the resource cap

> Closes `C-01`, `C-05`, `C-06`. After this wave the platform bills ahead of the period
> instead of behind it, the calendar-month key that skips months is gone, and a plan's store
> allowance is enforced when entitlement drops. Nothing acts on a *lapse* yet — nothing is voided
> and no account is frozen for non-payment, that is Wave 3 — but the cap cannot wait for Wave 3,
> because as soon as entitlement is modelled, "downgrade to Free" is otherwise a free ride
> (T-18).
>
> **Status note**: every task in this wave shipped in the first implementation pass. Their
> `Status` lines still read `pending` until the Wave 3–5 work brought the file up to date, so
> they were re-checked against the code rather than assumed: migration `0012` is at head,
> `UNPAID_STATUSES` and `void` exist as described, `issue_invoice` takes the explicit window,
> `issue_due_invoices` uses `next_billing_at - lead <= now` and never advances the schedule
> (`MAX_CATCH_UP_PERIODS` is gone), `change_plan` raises `409 open_invoice_unpaid` with the
> invoice named (`period_already_invoiced` is gone), the three settings exist with lead 7 /
> grace 7 / enforce off, and `apply_store_cap` is what holds and releases the surplus.

### T-01 — Migration `0012_billing_renewal_periods`
- **Status**: `complete` · **Priority**: S1 · **Depends on**: D5, D6
- **Description**: Add nullable `period_start`, `period_end`, `due_at`, `voided_at` and
  `void_reason` to `plan_invoices`. Backfill the three dates from `created_at`
  (`+ 30d` for the latter two). **Void every currently-open invoice** with
  `void_reason = 'pre_lifecycle'`. Drop `uq_plan_invoice_period` and create a **partial**
  unique index on `(subscription_id, period_start) WHERE status <> 'void'`, with both
  `postgresql_where` and `sqlite_where` set. Create `plan_invoice_reminders` with the unique
  `(invoice_id, tier, channel)`. Also add `stores.billing_suspended_at` — nullable, no
  backfill, so no existing store changes behaviour and `NULL` is the truthful value for every
  current row. Use `op.batch_alter_table` throughout, matching `0009` and `0011`.
- **Why nullable**: a populated table plus a value that cannot be computed in a
  `server_default` means either a nullable column or a placeholder date. `NULL` is honest
  for a pre-migration row, and both SQLite and Postgres treat `NULL` keys as distinct, so
  legacy rows do not participate in the new index.
- **Why the index is partial**: a plain unique key on the pair means a voided invoice still
  holds its window forever, so operator-voiding an invoice for a live period would make that
  period unbillable. A void is history, not a claim. This is also what lets the
  `pre_lifecycle` voids above be re-raised normally.
- **Why step 3 voids**: an open pre-migration invoice has no tier rows and none can
  honestly be invented, so leaving it open lets the first enforcement run freeze an
  account that was never warned. Full reasoning in `spec.md` §4.4.
- **Test**: `tests/test_migrations.py` — upgrade, insert a legacy-shaped row with
  `period_start IS NULL`, upgrade again, downgrade. Then A-14, A-15, A-16.
- **The test above was written later, while closing T-15.**
  `test_the_billing_migration_retires_open_invoices_without_touching_the_schedule` writes its rows
  as **raw SQL against `0011`** — the shape a production database has when this migration runs;
  inserting through the ORM would describe a schema that does not exist yet — and then asserts the
  three claims A-15 makes: every open invoice becomes `void/pre_lifecycle`, `period_start` is
  backfilled from `created_at` while `period_end`/`due_at` are left `NULL` rather than invented,
  and no `next_billing_at` moved. It also inserts a second invoice for the same subscription
  afterwards to prove the void really released its window, and runs `downgrade 0011` so the way
  down survives the rows it just voided.
- **`pre_lifecycle` appeared in no test before this** — the migration had run in production without
  an automated assertion that it retires the invoices it says it retires.
- **Evidence**: `alembic upgrade head` on a copy of production: every previously-open
  invoice reads `void/pre_lifecycle`, and no `plan_subscriptions.next_billing_at` changed.

### T-02 — Normalise invoice statuses
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-01
- **Description**: One `UNPAID_STATUSES = ("open", "draft", "issued")` constant in
  `services/billing.py`, used by every read that means "not settled". Writes emit `open`
  only. Add `void` as a first-class unpaid-and-abandoned state. Remove the `issued_at`
  `getattr` from `_serialize_invoice` (`routers/billing.py:329,437`) — the column does not
  exist (`C-08`) and `due_at` replaces it.
- **Test**: A-14; existing `tests/test_billing_invoices.py` still green.

### T-03 — `issue_invoice` takes an explicit window
- **Status**: `complete` · **Priority**: S1 · **Depends on**: T-01
- **Description**: Signature becomes `issue_invoice(session, subscription, plan, *,
  period_start, period_end, due_at, now)`. `period_month` becomes a derived label
  (`period_month_for(due_at)`). Idempotency moves from the `(account_id, period_month)`
  lookup to the `(subscription_id, period_start)` unique key: on conflict return the
  existing row with `created=False`. `period_usage` keeps reading the ledger for the
  covered window — the count is recorded, never charged (`overage_fee_cents = 0`), so
  nothing about the amount depends on waiting for the period to finish.
- **Test**: A-13, A-03.

### T-04 — W3 raises in the lead window and stops advancing the schedule
- **Status**: `complete` · **Priority**: S1 · **Depends on**: T-03, T-13
- **Description**: Replace the `next_billing_at <= now` predicate with
  `next_billing_at - lead_days <= now` plus a `monthly_fee_cents > 0` filter and a
  not-exists check for an unpaid invoice on the same `period_start`. **Delete the
  `subscription.next_billing_at += CREDIT_PERIOD` line** and the surrounding catch-up
  `while` loop: the window ends where it ended, and only a payment moves it. The free-plan
  filter replaces the "advance even when nothing to bill" rule that made the old loop
  terminate. With the loop gone, `MAX_CATCH_UP_PERIODS` no longer bounds anything; delete
  it rather than leave a constant whose comment describes behaviour that no longer exists.
- **Test**: A-01, A-02. A regression test that a non-payer's `next_billing_at` does not
  move across ten sweeps.
- **Note**: this is the change that makes a lapse visible. Without it, Wave 3 has no
  signal to act on.

### T-05 — `change-plan` guards on an open invoice, not on the calendar month
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-03, T-18
- **Description**: Replace the `period_already_invoiced` guard
  (`routers/billing.py:226-233`) with `409 open_invoice_unpaid`, returned when the account
  already has an unpaid invoice. Build the pending subscription's invoice with
  `period_start = now`, `period_end = now + CREDIT_PERIOD`, `due_at = now`. Call
  `apply_store_cap` (T-18) on the free-plan branch, so a self-serve downgrade enforces the
  allowance it is choosing.
- **Leave the free-plan branch inline for now.** T-07 extracts it into a shared helper once
  the grace sweep needs the same code; extracting it here would mean inventing an abstraction
  with a single caller.
- **Test**: an upgrade with an open renewal invoice is refused and the refusal names the
  invoice; an upgrade with a settled history succeeds; a Pro→Free downgrade leaves one store
  able to mint codes.

### T-13 — Settings `billing_lead_days`, `billing_grace_days`, `billing_enforce_enabled`
- **Status**: `complete` · **Priority**: S2 · **Depends on**: none
- **Description**: Three deployment-tunable values in `config.py` next to
  `billing_sweep_interval_seconds`: lead 7, grace 7, and `billing_enforce_enabled`
  defaulting to **false** because it gates the only irreversible step in the feature. Tier
  offsets and `CREDIT_PERIOD` stay module constants in `services/billing.py`.
- **Test**: defaults are read when unset; a settings override changes the lead window; with
  `billing_enforce_enabled=false` the `enforce` job logs the freezes it *would* apply and
  changes nothing.

### T-18 — `apply_store_cap`, for a chosen or voluntary downgrade
- **Status**: `complete` · **Priority**: S1 · **Depends on**: T-01, D7, D8, D14
- **Description**: §7.6. Set `billing_suspended_at = now` on the stores beyond the new plan's
  `max_stores`, keeping the lowest-id ones live. Called from **two** places: the settlement of a
  downgrade the merchant chose (T-23) and `change_plan`'s free branch (T-05).
- **Stores only — never keys or webhooks (D14).** A key is a credential rather than capacity, so a
  held store refuses it anyway; revoking keys adds no enforcement and breaks a live integration.
  Webhooks must keep delivering for payments that settle during the window. If the store-only cap
  ever proves too soft, revisit D14 rather than quietly widening this function.
- **It is not part of the freeze.** The freeze is account-level and writes no store row at all
  (T-09, §7.2). This function exists only for the case where the account is *not* frozen and is
  genuinely on a plan smaller than its usage.
- **Why it matters**: without it, "downgrade to Free" is a menu item that keeps all 50 stores
  running for free — the same leak as not paying, reached without a lapse, and reachable from the
  moment T-05 ships (§7.1).
- **The check that makes it bite is part of this task.** `create_payment` and the reissue path must
  refuse a billing-suspended store, or the flag is decoration. The refusal code is
  `store_billing_suspended`, not `store_disabled` (`services/payments.py:145-146`, `:249-250`), so
  an integrator can tell "my merchant switched this off" from "the platform is holding this store".
  **Do not touch detection** — in-flight payments must still settle (§7.6, A-20).
- **What it must never do**: touch `status` (that is `disable_store`'s field, and overwriting it
  loses *why* — `services/stores.py:266` versus the restore-by-inference at
  `services/stores.py:290-309`), delete anything, touch webhooks or payment records, or clear an
  operator's `STORE_DISABLED`.
- **Idempotent by construction**: it holds whatever is over the allowance, so a second run and a
  re-run after the merchant re-picks are both no-ops.
- **Test**: A-18, A-19, A-20. Also: a voluntary Pro→Free downgrade caps, and Pro→Starter caps to 5.

---

## Wave 2 — One settlement path

> Closes the split between `settle_invoice_for_payment`, `activate_subscription` and the
> admin resolve route. None of the three currently handles a subscription the grace sweep
> has already canceled, so this wave is a prerequisite for Wave 3, not a refactor for its
> own sake.

### T-06 — `billing.settle_invoice(session, invoice, *, paid_at, source)`
- **Status**: `done` · **Priority**: S1 · **Depends on**: T-03
- **Description**: One function implementing §5.3, with two branches:
  - **(a) `paid_at <= due_at + grace`** — they paid before the freeze, so the window the invoice
    describes is still theirs: mark it `paid` and set
    `next_billing_at = invoice.period_end`. Grace days are given free and the anniversary is
    preserved.
  - **(b) any later** — they paid *after* the freeze, so that window is gone and the invoice is
    **not** marked paid. Void it (`grace_expired`) — leaving it `open` would keep the merchant
    owing a period nobody served, and `change_plan`'s `409 open_invoice_unpaid` guard would
    refuse them a purchase forever. Then create the subscription the payment actually buys
    (`status = 'active'`, `started_at = paid_at`, `next_billing_at = paid_at + CREDIT_PERIOD`),
    cancel the account's previous `trial`/`active` rows, and create the granted invoice
    (`period_start = paid_at`, `period_end = paid_at + CREDIT_PERIOD`, `due_at = paid_at`,
    `status = 'paid'`) **named against that new subscription** — a `paid` invoice pointing at a
    canceled one would be a lie in every join a revenue report makes. Move the payment's
    `chmabapay_payment_id` off the void invoice onto the granted one, and audit
    `billing.reinstated` with both invoice ids, the gap in days and the source (`khqr` | `admin`).
  - **Both branches** finish by clearing a `restricted` account back to `active` and releasing
    the store cap with `apply_store_cap`, because whichever way they paid, the merchant comes
    back. `suspended` is left alone: an abuse lockout outranks a billing state (§9).
  - **Do not branch on `subscription.status`.** The freeze is account-level and deliberately
    leaves the subscription `active` (T-09, §7.2), so a subscription-status test would send every
    frozen merchant down (a) — paying and *losing* the month they were frozen, because (a)
    preserves an anniversary that has already passed. `due_at + grace` is the same line the
    enforcement job uses, so both sides agree on what "lapsed" means.
  - This is why the function cannot only flip a status: the payment has to be re-pointed, and
    the lookup in `settle_invoice_for_payment` is by `chmabapay_payment_id`, so the handoff
    happens inside the same transaction. Keep it running **before** `mark_paid`'s commit so
    the payment, both invoices and the plan change stay durable together.
  - `lift_billing_hold(account)` is the one place that clears the freeze, called from both
    `settle_invoice` and the admin `waive`/`credit` path — the two ways a debt stops existing
    without a QR payment. `routers/admin.py` `mark-paid` calls `settle_invoice` itself, so a
    bank transfer leaves the account in exactly the state a QR payment would.
- **Why (b) issues a new invoice**: marking the lapsed one paid books revenue into a month
  that was never served and breaks the invariant that a `paid` invoice describes a window the
  merchant received. See D10.
- **Test**: A-03, A-10, A-22, A-23. Plus the existing invariant test that exactly one
  subscription is ever `trial`/`active`.
- **Shipped**: `tests/test_billing_invoices.py::test_paying_after_a_freeze_bills_from_the_day_they_paid`,
  `::test_paying_inside_grace_keeps_the_renewal_anniversary`,
  `::test_a_suspended_account_is_not_unfrozen_by_paying`.

### T-07 — `downgrade_to_free` as the single implementation
- **Status**: `dropped` · **Priority**: — · **Depends on**: —
- **Why it is not needed**: the task assumed two callers — `change_plan` and the grace sweep
  (§5.4). D1 removed the second one: the sweep freezes the account and changes nothing else, so
  **nothing auto-downgrades**, and the free branch of `change_plan` is already the only
  implementation of "move to Free". Extracting it into a service function would create a second
  entry point to the same transition with no caller, which is the opposite of one path.
- **What it was protecting**: exactly one row in `trial`/`active`, which `_get_active_sub` reads
  as a single row. That invariant is asserted by the Wave 2 tests and by
  `test_paying_after_a_freeze_bills_from_the_day_they_paid`.
- **The three ways out of a freeze** (Pay / choose Free / choose a paid tier) all reach the
  billing page's own routes — `POST /api/v1/billing/invoices/{id}/khqr` and
  `POST /api/v1/billing/change-plan` — so T-23 is a UI task plus the allowlist in T-22, not a
  service extraction.

---

## Wave 3 — Reminders, then the freeze

> **Gate: T-08 ships and runs in production before T-09 is enabled.** T-08 is the warning; T-09 is
> the consequence. `billing_enforce_enabled` stays false until the order has been observed, and
> T-22 must land before T-09 can mean anything.

### T-08 — W6 `BillingReminderWorker`, job type `remind`
- **Status**: `done` · **Priority**: S2 · **Depends on**: T-02, T-03, T-13, D2, D3
- **Description**: New worker on its own queue, registered in `worker_registry()`
  (`workers/runtime.py:53-61`) with an hourly heartbeat following
  `billing_heartbeat_dedup_key`'s pattern (`w3_billing.py:31-35`). Implement the tier table
  and the "most urgent reached tier, at most one message per run" algorithm from §5.2 as a
  single `derived_state(invoice, now)` helper — including the `issuance` state that precedes
  `due_3` — and have T-10 call the same helper, so the banner and the recorded tiers can
  never disagree about what the merchant is being told.
  Write the `plan_invoice_reminders` row when the tier is reached and treat a
  unique-constraint violation as "another replica got there". For `in_app` that insert is
  the entire operation — there is no message to deliver and nothing that can fail after it,
  which is why the banner must stay derived (T-10) rather than keyed off this row. Tier copy
  goes in `services/notifications.py` next to `format_payment_paid` and
  `format_invoice_issued`, so the wording an operator and a merchant see for one event is
  built in one module, using the tier copy table in `spec.md` §5.2.2.
- **Fail-open, fail-closed**: the tier rows are bookkeeping, **not** what makes the warning
  visible — the banner is derived from `due_at` by T-10. Do not gate display on a row
  existing, or a worker outage becomes an invisible warning and the Wave 3 gate is a
  fiction. A-17 asserts exactly this.
- **Query guards**: `status IN UNPAID_STATUSES`, `due_at IS NOT NULL`, the invoice's
  subscription is `trial`/`active` (excludes upgrades), and the account is `active`
  (excludes operator-suspended accounts).
- **`overdue_final` and `frozen` are the two copy items that matter.** `overdue_final` names the
  date and what stops — "your stores will stop generating payment codes". `frozen` is derived from
  the *account* rather than from any invoice, outranks every tier, is not dismissible, and says what
  is **kept** — "your data is untouched". A merchant whose business just went dark needs the
  difference between a hold and a deletion (§5.2.2).
- **Test**: A-04, A-05, A-06, A-07. A-05 is the important one — a worker that was down for
  four days must send one message, not four.
- **Do not**: embed a QR image in any notification. The link is
  `/dashboard/billing?pay={invoice_id}`, which mints a fresh code on load because ABA's
  session is 180 s.
- **Shipped**: `services/billing.py` — `TIER_OFFSETS`/`TIERS`, `STATE_ISSUANCE`/`STATE_FROZEN`,
  `derived_state`, `find_reminder`, `_unpaid_invoice_query`, `record_due_reminders`.
  `workers/w6_billing_lifecycle.py` — `BillingLifecycleWorker` on queue `billing.lifecycle`,
  dispatching `remind` / `enforce` and skipping an unknown type rather than failing into the
  retry loop; hourly heartbeat registered in `workers/runtime.py`, warmed up on boot like W3 and
  W5. Tests: `tests/test_billing_dunning.py`.
- **Still owed by T-10**: the tier copy in `services/notifications.py` (§5.2.2). It is deliberately
  not here: for `in_app` the insert is the whole operation and nothing renders from it, so copy
  written now would be dead code until T-10's endpoint exists. `derived_state` is already the
  single source the banner will read.

### T-09 — W6 job type `enforce`: freeze the account, and expire abandoned purchases
- **Status**: `done` · **Priority**: S1 · **Depends on**: T-08 (gate), T-22, D1, D3, D4, D7
- **Description**: §5.4. Set `account.status = restricted` for every account whose open invoice
  has `due_at + grace_days <= now` while its subscription is still in force, audit
  `billing.account_frozen`, and post to the operator feed. Skip the D4 exemption, and skip
  accounts that are not `active` — so a second run and an operator's own suspension are both left
  alone.
- **Change nothing else.** Do not void the invoice, do not change the plan, and **do not write a
  single store row** — the freeze is account-level, which is what makes unfreezing one status
  write (§7.2). Voiding the invoice here would remove the very thing the merchant is being asked
  to settle. `downgrade_to_free` and `apply_store_cap` are not called from this job at all.
- **The abandoned-purchase half is unchanged**: cancel `pending` subscriptions older than 30 days
  and void their invoices, so an abandoned upgrade cannot block the next one forever through
  T-05's guard.
- **Gated by `billing_enforce_enabled`**: with the flag false the job logs the freezes it would
  perform and touches nothing. The abandoned-purchase half stays ungated — cancelling a `pending`
  subscription grants nothing and takes nothing away.
- **Test**: A-08, A-10, A-11. Plus: two concurrent runs freeze once; a flagged-off run leaves the
  invoice `open` and the account `active`; not one `stores` row is written.
- **Shipped**: `services/billing.enforce_grace` + `_expire_abandoned_purchases`;
  `notifications.format_account_frozen`; the `enforce` heartbeat. Concurrency is handled by the
  `Account.status == active` guard rather than by locking, so a second run finds the account
  already `restricted` and skips it — tested as a sequential second run, which is the same
  predicate a concurrent one takes. Tests: `tests/test_billing_dunning.py`
  (`test_the_freeze_happens_once_at_due_plus_grace`,
  `test_an_abandoned_upgrade_is_retired_even_with_enforce_off`).
- **The flag is still false.** Enforcing a freeze in production is a switch an operator throws
  after watching the log for a full cycle; until then the job reports candidates hourly and
  changes nothing.

### T-21 — Quota timing on a downgrade
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-09, D9
- **Description**: `check_plan_quota` counts the calendar month (`services/payments.py:382-412`),
  so a merchant who settles 40,000 payments and then chooses a smaller plan mid-month would be
  instantly over the new allowance and blocked from taking *any* payment until the first of the next
  month — a total stop with nothing in the notices to explain it (§7.7). Defer the new allowance to
  the next calendar-month boundary when the account was downgraded from a paid plan during the
  current month; until then only the store cap bites.
- **Do not defer for a new signup.** A Free account that was never on a paid plan must be metered
  from day one, or every new merchant gets an unlimited first month. Derive the deferral from a paid
  subscription canceled in the current month, not from the Free subscription's `started_at`.
- **A deferral raises the ceiling, it does not clear usage.** The month's count still applies when
  the boundary passes.
- **Test**: a mid-month downgrade above 3,000 keeps payments working until the boundary and then
  enforces; a brand-new Free account is capped immediately.
- **Shipped**: `services/payments.downgraded_this_month` — true when a subscription on a plan with
  `monthly_fee_cents > 0` was canceled with `canceled_at` inside the current calendar month —
  called from `check_plan_quota` **only once the count is already over the allowance**, so a
  merchant comfortably inside their plan pays nothing for the rule.
- **Keyed on the cancellation, not on the new subscription.** `canceled_at` is already written by
  every path that replaces a subscription (`change_plan`'s free branch, `retire_live_subscriptions`,
  `activate_subscription`), so the rule needs no new column and no bookkeeping of its own. A
  deferral derived from the *new* row's `started_at` would fire for a signup too, and a Free
  merchant metered from their first day is the whole point of the tier.
- **Both downgrade shapes are covered by the one rule.** A free downgrade cancels the old paid row
  immediately; a paid one cancels it when the new invoice settles (`activate_subscription`), which
  is also inside the same month. Either way the boundary is the first of the next month.
- **It defers enforcement; it does not clear the count.** The query is the calendar-month count as
  before, so when the boundary passes the ordinary allowance applies from zero.
- **Tests**: `tests/test_billing_invoices.py` — `test_a_plan_choice_does_not_meter_the_month_it_was_made_in`
  (which demonstrates the trap first: the same account is refused with 402 *before* the downgrade,
  served *after* it, and refused again once the cancellation is moved into a previous month) and
  `test_a_new_free_account_is_metered_from_its_first_month`. The first was **verified to fail**
  with the deferral removed, so it is a real guard rather than a passing assertion.
- **The merchant-visible gap this task left, now closed.** The billing page's usage bar read
  "40,000 / 15,000" while codes still worked, because the counter is the month's count and the
  allowance now starts at the boundary. Each number was true; the page simply never said *why* they
  disagreed. It does now: `GET /api/v1/billing/subscription` reports `quota_deferred_until` — the
  instant enforcement starts, `null` when it already has — and the usage card prints one line under
  the bar when, and only when, that changes what the bar *means* (`used >= included`). Under the
  allowance the deferral is invisible to the merchant and a note would be noise.
  `payments.quota_deferred_until` is the single implementation; `downgraded_this_month` is now the
  same query read as a yes/no, so the date the page prints cannot be a different date from the one
  the quota check honours. `test_the_deferral_is_reported_and_the_date_is_the_one_enforced` watches
  the field and the `402` turn over together rather than checking the date alone — verified to fail
  by returning the *past* month boundary instead of the next one.
- **Its first version shipped a bug only a browser could see.** The field is a sibling of
  `subscription` in the response, like `current_period_end`, but the page keeps only
  `data.subscription` and read `subscription?.quota_deferred_until` — so the note silently rendered
  nothing, with `tsc --noEmit` clean and every test green. A wrong accessor is invisible to a type
  check when the type it is read from happens to describe the shape the author *thought* it was.
  `outstanding_invoice_id` sits on the same list and carries the same misread, masked by its own
  fallback; it is left alone and recorded rather than fixed in passing.
- **Browser-verified both ways.** The seeded mid-month downgrade (`3,100 / 3,000`, deferral to
  Oct 1) prints "Nothing is blocked until Oct 1, 2026 — these payments were made under your
  previous plan."; an account with no deferral (`0 / 15,000`) prints nothing below the bar, and
  neither "blocked" nor "previous plan" appears anywhere on the page.
- **Still open, and smaller**: the sidebar's plan mini-card reads the same `3,100 / 3,000` with no
  explanation, and its "resets" date is the renewal date rather than the allowance boundary. A
  one-line summary with no room for the sentence, fed by a different fetch
  (`dashboard/layout.tsx`), so it is untouched here.

### T-22 — The `restricted` state and the read-only gate
- **Status**: `done` · **Priority**: S1 · **Depends on**: D11, D12
- **Description**: Add `ACCOUNT_RESTRICTED = "restricted"` (§7.4) and enforce it in the two choke
  points, both of which already receive the `Request`:
  - `get_current_auth_context` (`auth.py:99-110`) — stores, keys, payments, webhooks, khqr,
    transactions, reports
  - `get_current_session_account` (`routers/auth.py:365-387`) — account settings, billing

  `active` gets everything. `restricted` gets GET/HEAD plus the allowlist (`GET /api/v1/billing/*`,
  `POST /api/v1/billing/change-plan`, and the invoice-KHQR mint) and `403 account_restricted` for
  everything else. `suspended` keeps its 401 lockout and **outranks** `restricted`.
  `resolve_key_context` (`auth.py:54-55`) refuses `restricted` outright, which freezes the whole
  API in one line.
- **Do not reuse `suspended`.** It raises 401 on every request, so a suspended merchant cannot
  sign in and therefore cannot reach `/dashboard/billing` — the page that clears the debt (§7.4).
- **The coverage test is a deliverable, not a nicety.** Enumerate the app's routes and assert that
  every mutating method returns 403 for a restricted session except the allowlist — the shape of
  `test_every_operator_action_is_admin_gated`. A route added later that forgets the rule must fail
  the suite rather than quietly work for a frozen merchant.
- **Test**: A-09. Plus: a `restricted` account can still list invoices, mint an invoice code and
  change plan; `suspended` still 401s everywhere.
- **Shipped**: `RESTRICTED_ALLOWED_WRITES` / `RESTRICTED_REFUSED_READS` and
  `restricted_may_reach` in `routers/auth.py`; the gate in `get_current_session_account`; the
  key refusal in `auth.resolve_key_context`; `restricted` allowed through sign-in.
  Tests in `tests/test_account_security.py`:
  `test_a_frozen_account_reads_and_pays_but_cannot_write`,
  `test_every_mutating_route_refuses_a_frozen_account`,
  `test_a_frozen_account_can_sign_in_but_a_suspended_one_cannot`,
  `test_an_api_key_is_refused_for_a_frozen_account`.
- **One finding worth keeping**: `GET /api/v1/transactions/check-status/{id}` is a *write* — it
  settles the payment it polls. It is therefore the one read the gate refuses; detection (W1)
  settles those payments on its own sweep, so nothing is lost. Discovered by auditing all 49 GET
  routes rather than by assuming GET means read.

### T-23 — The billing page's three ways out
- **Status**: `complete` · **Priority**: S1 · **Depends on**: T-06, T-18, T-22, D10
- **Description**: §7.5. The frozen merchant's one screen that accepts input, offering three
  outcomes:
  - **Pay** — the existing invoice-KHQR path. `settle_invoice` unfreezes as it settles.
  - **Choose a paid plan** — void the open invoice (`void_reason='downgraded'`), raise an invoice
    for the chosen plan's period, unfreeze when it settles, then `apply_store_cap` (T-18) and
    offer T-19's chooser.
  - **Choose Free** — void the open invoice and unfreeze immediately, as an ordinary Free account.
  Every branch must end with **no open invoice and `status = active`**, or the merchant stays
  frozen while on the plan they just chose — the trap §7.5 exists to prevent.
- **The banner is the entry point**: `GET /api/v1/billing/notices` returns `state: frozen` (T-10),
  the CTA deep-links to `/dashboard/billing`, and the modal mints a fresh code on load. One tap,
  from the banner to a payable code.
- **Test**: A-24, A-22, A-18.
- **Shipped (server half)**: `change_plan` now voids unpaid invoices in the Free branch
  (`downgraded`, plus `lift_billing_hold`), and while frozen it voids the blocking invoice and
  retires the lapsed subscription in the paid branch instead of answering `409
  open_invoice_unpaid`. `billing.retire_live_subscriptions` is the shared retirement, also used
  by `_reinstate`. Tests: `test_a_frozen_account_choosing_free_writes_the_debt_off_and_is_unfrozen`,
  `test_a_frozen_account_choosing_a_paid_plan_waits_for_the_money`.
- **The UI half**: the frozen-state copy on `/dashboard/billing` naming the three options, and
  T-19's store chooser for a merchant landing on Starter with more stores than the plan allows.
  The routes it needs already exist, which is why the allowlist is only `POST
  /api/v1/billing/change-plan` plus reads.
- **Shipped (UI)**: a `Your account is on hold` panel above the subscription panel, carrying the
  amount and due date of the invoice the freeze is keyed on and a real `<ol>` of the three ways
  out — `Pay $9.99` (opens the existing `InvoicePaymentModal` for `outstanding_invoice_id`,
  falling back to the oldest unpaid invoice), `Choose Free` and `Choose a plan` (both anchors to
  the plan cards, which are the two writes the allowlist permits). It reads the hold from
  `/api/v1/me`'s `status` rather than inferring it from an overdue invoice — `restricted` is a
  property of the account, and an operator can freeze one with no invoice at all, which is why
  the copy has a no-amount variant rather than quoting $0.00.
- **Browser-verified, and it is the reason for the T-19 fix**: while frozen the page shows the
  hold panel, `Pay $9.99` enabled, the chooser absent from the DOM, and the ordering
  hold → subscription → plans → invoices. The account's own terms gate does not interfere
  (`terms_accepted_version` must match `terms_required_version`; a stale seeded account meets the
  gate *instead of* the page, and cannot clear it while restricted — `POST /api/v1/me/terms` is
  correctly not in the allowlist).

### T-14 — Do not treat a suspended or admin account as a dunning target
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-09
- **Description**: The guards are already stated in T-08/T-09/T-22; this task is the explicit test
  and the comment that records *why*. Three reasons: an operator who suspends an account has made a
  decision, and a billing message would be noise on top of it; **`suspended` must outrank
  `restricted`**, so a billing job can never weaken an abuse lockout into a read-only account; and
  the platform admin account owns the HQ store plan fees are collected into
  (`services/billing.py:101-166`), so freezing it risks the platform being unable to collect its own
  revenue.
- **Test**: a suspended account is not frozen by the sweep and is not unfrozen by a settlement; a
  platform admin's overdue invoice freezes nothing.
- **Shipped**: three comments and two tests, no behaviour change — the guards were already in
  `_unpaid_invoice_query` (`Account.status == active`, plus `is_platform_admin.is_(False)` when the
  caller is the one that changes something).
  - `enforce_grace`'s docstring now names all three excluded accounts and says **the exclusion comes
    from the one query rather than from a check inside the loop**, so there is no path into the
    freeze that skips it.
  - `_unpaid_invoice_query` records the asymmetry that was previously only visible in the code:
    the exemption is on for `enforce_grace` and off for `record_due_reminders`, because the
    platform owner should still be *told* their own invoice is due — a message, not a consequence.
  - `derived_state` records why `suspended` is asked about *before* `restricted`: an operator's
    lockout outranks a billing state, and reading a suspended account's invoice as a billing
    matter is the first step towards treating it as one.
- **Tests** in `tests/test_billing_dunning.py`:
  `test_a_suspended_account_is_never_dunned_and_never_frozen` (eleven days of sweeps produce no
  tier row, and a sweep past the grace reports `due: 0` and leaves both the status and the invoice
  untouched) and `test_a_platform_admin_is_told_about_their_own_invoice_but_never_frozen` (all six
  tiers recorded, nothing frozen).
- **Both were verified to fail** with the guards removed (`assert 1 == 0` on `summary["due"]`), so
  they guard the freeze path rather than restating it. Worth recording: the *reminder* half of the
  suspended case is protected twice — `derived_state` returns `None` for a suspended account as
  well — so that assertion alone would not have caught a missing query guard.
- **What is already covered elsewhere**: the settlement half — a suspended account is not unfrozen
  by paying — is `test_a_suspended_account_is_not_unfrozen_by_paying` in
  `tests/test_billing_invoices.py`, and `suspended` beating `restricted` at sign-in is T-22's
  `test_a_frozen_account_can_sign_in_but_a_suspended_one_cannot`. This task deliberately adds no
  third copy of either.

---

## Wave 4 — Surfaces

### T-10 — `GET /api/v1/billing/notices` and invoice serialization
- **Status**: `done` · **Priority**: S2 · **Depends on**: T-08
- **Description**: New endpoint returning at most one notice, most urgent first, with
  server-computed `level`, `state`, `days_until_due`, `message` and `action_url` (§6). **The state
  comes from T-08's `derived_state` helper** and is never read from `plan_invoice_reminders`: that
  table records what the platform said, this endpoint decides what the merchant is owed. `frozen`
  outranks every tier and is the only state that can be returned with **no invoice involved at all**
  — a frozen account is a property of the account, not of a row (T-22). Add `period_start`,
  `period_end`, `due_at`, `days_until_due`, `is_overdue` to `_serialize_invoice` and to
  `GET /api/v1/billing/subscription` (`current_period_end`, `outstanding_invoice_id`). Both belong in
  `tests/test_openapi_schema.py`.
- **Test**: A-12, A-17. Field names are pinned by the OpenAPI test so the portal cannot
  drift onto a field the API stopped returning — the failure mode `period_month`/`period`
  already produced once (`dashboard/billing/page.tsx:42-45`).
- **Shipped**: `services/billing.py` — `NOTICE_LEVELS`, `NOTICE_ORDER` (a state's position *is*
  its urgency rank), `DISMISSIBLE_LEVELS`, `derive_notices`, `_render_notice`, `_notice_rank`.
  `services/notifications.py` — `billing_notice` (the §5.2.2 table), `_local_date`/`_period_window`
  rendering in `Asia/Phnom_Penh`, `money` made public for the formatted amount.
  `routers/billing.py` — `GET /api/v1/billing/notices`, plus `current_period_end` and
  `outstanding_invoice_id` on `SubscriptionOut`. The new path is on the public docs page, so the
  schema test's "published means documented" rule still holds. Tests: `tests/test_billing_notices.py`.
- **Two deliberate additions to the payload**, beyond the fields §6 listed: `title`/`body`/`action_label`
  in place of a single `message`, and `dismissible`. The spec's §5.2.2 table is three-part copy, and
  the dismissal rule is a policy that belongs on the server — otherwise every tier needs a client
  release. `NOTICE_FIELDS` in the test pins the whole set.
- **One case worth knowing**: a frozen account with no open invoice (an operator froze it by hand)
  gets the hold notice with no amount and no invoice, because `frozen` is a property of the account
  rather than of a row. The copy has a no-amount variant; quoting $0.00 would be worse than saying
  nothing about money.
- **A note on the reminder rows**: the endpoint never reads them. `test_warnings_survive_a_worker_that_never_ran`
  deletes them, re-records them, and deletes them again, asserting the notice is byte-identical
  throughout — which is A-17's whole claim.

### T-11 — Portal: banner, billing rows, plan card
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-10
- **Description**: A shell banner on `/dashboard/*` driven by `/api/v1/billing/notices`, with
  per-level styling consistent with the existing design tokens. Billing page invoice rows
  gain `Due <date>` and a status badge (`Open` / `Overdue Nd` / `Paid` / `Void`). The
  dashboard plan card shows the renewal date or the overdue state. The action link opens
  the existing `InvoicePaymentModal` via `?pay={invoice_id}` — no new payment UI.
- **Read-only mode**: when the notice state is `frozen`, the shell renders the settings pages with
  their inputs disabled and every save action replaced by a link to billing. This is a courtesy —
  T-22's gate is the enforcement — but without it the merchant clicks Save, gets a 403, and does not
  know why.
- **Dismissal**: `info` levels dismissible per `(invoice_id, tier)` in `localStorage`;
  `warning`, `critical` and `frozen` not dismissible. The billing page row is the backstop that
  keeps a dismissed notice from becoming a hidden debt, so do not make the dismissal suppress the
  row or the badge.
- **Keeping the banner derived**: render from the notice payload, never from the presence of
  a `plan_invoice_reminders` row (T-08).
- **Test**: rendered and checked in the browser for each level; no banner when nothing is
  due and no invoice is open.
- **Shipped**: `web/landing/components/portal/BillingNotice.tsx` (the banner, dismissal keyed
  `(invoice_id, state)` so a new state on the same invoice is visible again, `role="alert"` for
  `critical`, and a plain `<a>` CTA so the KHQR is minted by a full navigation);
  `DashboardShell` gained `notice` / `readOnly`; `app/dashboard/layout.tsx` fetches
  `/api/v1/billing/notices` on the existing `chmabapay:plan-changed` refresh path; the billing page
  gained the Due column, the derived `Overdue Nd` badge and `OpenInvoiceFromUrl` (Suspense-wrapped,
  honouring the server's `action_url`); the dashboard plan card gained its second line;
  `apiError.ts` gained the `account_restricted` copy.
- **Read-only mode needed a `fieldset`, not CSS.** The first cut cloaked the settings forms with
  `pointer-events: none`, which stops the mouse and nothing else: the inputs stayed focusable and
  typable, and Enter on a `type=submit` button still submitted. The shell now wraps the page in
  `<fieldset class="cp-readonly" disabled>` — native disabling, keyboard included — with the CSS
  resetting the fieldset to a zero box and re-declaring the page rhythm one level in, since
  `.cp-main > * + *` can no longer see the children. Billing is excluded so its controls stay live.
  **Found by the browser check, not by the suite**; re-verified after the fix (15 Tab presses never
  reach a form control, programmatic focus does not move, gaps unchanged at 20px).
- **Verified** in the browser across four states: nothing owed (no banner), overdue (critical
  banner, `Overdue 8d` badge), frozen (hold banner, no Dismiss control), frozen on settings
  (read-only). `npm run typecheck` and `npm run build` clean.

### T-12 — Admin console: void, and overdue visibility
- **Status**: `complete` · **Priority**: S3 · **Depends on**: T-09
- **Description**: Add `void` to `POST /api/v1/admin/invoices/{id}/resolve` (it must not
  activate a subscription, unlike `mark-paid`, which must now run the same
  `billing.settle_invoice` as the QR path so a transfer resolves exactly like a QR payment).
  Add `due_at` and a derived overdue filter to `GET /api/v1/admin/invoices` and to
  `web/admin/app/invoices/page.tsx`, so an operator can see what is about to lapse without
  opening each row.
- **Interaction to verify**: voiding an invoice whose window is still in force must not
  block W3 from raising that window again — that is what the partial index in T-01 is for
  (A-16).
- **Test**: `void` leaves the subscription untouched and writes an audit row; `mark-paid` after a
  freeze unfreezes and reinstates; `waive` and `credit` unfreeze too; the overdue filter returns
  open invoices past `due_at`.
- **Shipped**: `routers/admin.py` — `void` in the `InvoiceResolveIn` literal and in
  `_RESOLVED_INVOICE_STATUS`, its own branch (no `paid_at`, no `activate_subscription`, no
  `extend_coverage`, so the sweep may raise the window again), `period_start`/`period_end`/`due_at`/
  `is_overdue`/`voided_at`/`void_reason` on `_invoice_row`, and `overdue=true` on
  `GET /api/v1/admin/invoices` (unpaid, `due_at` in the past, ordered most-overdue-first).
  `web/admin/app/invoices/page.tsx` — the Due column, a derived `overdue Nd` badge, a `void` pill
  and filter option, the Void action, and per-action copy because a single "puts the pending
  subscription in force" sentence would be wrong for one of the four.
- **`void` also lifts the hold**, which the task text did not ask for but the alternative cannot be
  right: a freeze is keyed on this invoice being unpaid, so withdrawing it would strand the merchant
  frozen with nothing left to pay and no route out — the dead end T-23 exists to prevent, reached
  from the operator side. Tested for all four actions
  (`test_resolving_the_invoice_that_froze_an_account_unfreezes_it`).
- **The re-raise is A-16's, already covered**:
  `test_the_database_refuses_two_live_invoices_for_one_window` in `tests/test_billing_invoices.py`
  asserts that voiding a window releases it for re-issue, which is what makes "grants nothing" safe
  rather than a lost month.
- **Found while running the suite, not caused by this task**: `test_retrying_a_delivery_reschedules_it_and_records_why`
  fails on SQLite at `HEAD` (verified with the whole working tree stashed) — `routers/admin.py`'s
  `retry_delivery` compares the naive `next_attempt_at` SQLite returns against an aware `now`.
  Postgres returns aware, so production is unaffected; it is the same SQLite/PG asymmetry the
  billing work kept meeting. Reported, deliberately not fixed here.

### T-19 — The resource chooser, and `POST /api/v1/stores/{id}/activate`
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-18, T-23, D8
- **Description**: After a chosen downgrade leaves the account over its allowance, the billing page
  offers a chooser: swap which stores stay live, up to the plan's allowance. The endpoint holds
  whichever store is displaced and activates the requested one in a single transaction, so the count
  never exceeds the allowance and never falls below it (§7.6). Within the allowance this is the
  existing `enable_store` (`services/stores.py:279-321`) — the new behaviour is only the swap.
  Refuse a store an operator disabled (`STORE_DISABLED`) as opposed to one held by billing, and
  refuse a no-op swap. Audit `store.slot_moved` with both ids.
- **Why it exists**: without it the merchant is stuck with whichever resources the platform
  happened to keep. A business whose real store was a different one would lose its income, and that
  income is what funds their next payment.
- **Test**: A-19 — repeated calls, two concurrent calls, and a call naming the resource already
  live (a no-op that writes no audit row).
- **Shipped**: `services/stores.py` — `plan_max_stores` (extracted from the router's
  `_enforce_max_stores`, which now calls it) and `move_store_slot`, returning a
  `StoreSlotMove(store, displaced, moved)`. `routers/stores.py` —
  `POST /api/v1/stores/{public_id}/activate` answering `StoreSlotOut`. `schemas.StoreOut` gained
  `billing_suspended_at`, which is what lets a client see which stores are held. The billing
  page's chooser is in `web/landing/app/dashboard/billing/page.tsx`.
- **The count is preserved, not merely bounded.** The requested store is released, then the
  surplus is measured against the live set *including* it, and the newest live stores are held
  until the count equals the allowance — the mirror of `apply_store_cap`'s rule, and never the
  store the merchant just chose. A plan with room displaces nothing. Asserted after every pick:
  `Live (5 of 5)`, never six, never four.
- **Two decisions the task text left open.**
  1. *An already-live store is a quiet no-op, not a refusal.* The test line asked for "a no-op
     that writes no audit row", and `enable_store` already answers an already-live store this way
     — "Already live. Nothing changed, so nothing is recorded." A 409 would show a merchant an
     error toast for a harmless double-click.
  2. *`surplus` is a loop, not a single displacement.* Under a stale read one swap could need to
     hold more than one store; measuring the surplus and holding that many makes the write
     self-correcting instead of one short.
- **The account row is locked before anything is read.** Two chooser clicks arriving together
  would otherwise both read the same live set, both release a store, and leave six live. The lock
  is also why it is taken *first*: a session never refreshes a row it has already loaded, so a
  store read before the lock would still look held afterwards. Postgres honours `with_for_update`;
  SQLite ignores it and serialises the writers anyway.
  **The concurrency test does not prove the lock on SQLite** — verified by removing the clause and
  watching `test_two_picks_at_once_still_leave_exactly_the_allowance` still pass, because SQLite's
  writer lock serialises the two requests regardless. It asserts the invariant; the lock is the
  reason the invariant holds on Postgres. Recorded so the test is not mistaken for evidence it
  is not.
- **Refusals**: `409 store_disabled` for a store an operator disabled (that is
  `POST /{public_id}/enable`'s job), and nothing moves — the refusal is not a partial swap.
- **A regression this route introduced, found much later.** `test_audit.py`'s
  `test_every_mutating_route_on_the_privileged_surface_is_classified` walks `app.openapi()` and
  requires every mutating route under `/api/v1/{keys,webhooks,stores}` to be either in `AUDITED` or in
  `NOT_A_MUTATION`. The new route was in neither, so the suite was red from T-19 onward — nobody
  ran `test_audit.py` while shipping the later waves, which is exactly the gap that test exists to
  close. Fixed by classifying it as `("post", "/api/v1/stores/{public_id}/activate"): ("store.slot_moved",)`,
  matching the action `move_store_slot` writes. It is a real mutation (it decides whether money can
  be taken) so `AUDITED` is correct, not `NOT_A_MUTATION`.
- **The allowance comes off the live plan, not off the last cap.** `move_store_slot` counts against
  `plan_max_stores(session, account)` — the plan in force — while `apply_store_cap` is handed a
  number by its caller. Those two agreeing is the precondition A-19's "never six, never four"
  rests on, and in production they always agree because the cap is applied with
  `plan.max_stores` right after the plan change settles. The 50-store walk found the seam when it
  capped by hand to 5 while the plan still said Pro: the chooser then brought a store back and
  correctly left **six** live, because the plan allowed fifty. That is the right answer for the
  state built, and the state is one the product never builds — but it means a *stale* hold from a
  previous plan would be quietly over-served. A-21's "an upgrade releases the holds" is what stops
  the two drifting, and it is load-bearing rather than tidy for that reason.
- **A defect the browser check found, and the fix**: a frozen account that also holds stores (a
  merchant who downgraded and *then* stopped paying) rendered the chooser, and every "Bring back"
  answered `403 account_restricted` — the §7.4 allowlist is reads plus
  `POST /api/v1/billing/change-plan`, deliberately not this route. The chooser is now **hidden while
  frozen** rather than disabled: the freeze stops every store regardless of the cap, so the panel
  would be a list of buttons that can only fail, and the hold panel above it already names the
  three writes that do work. It returns the moment the hold lifts.
- **Browser-verified** both states: frozen (hold panel above the subscription panel, `Pay $9.99`
  enabled, the chooser absent from the DOM entirely) and unfrozen (the chooser with
  `Live (5 of 5)` / `Suspended — plan limit (3)`, and swaps that always leave 5 live and 3
  suspended while the toast names both stores).


### T-20 — Document the refusal codes, and the store-list badge
- **Status**: `complete` · **Priority**: S2 · **Depends on**: T-18, T-22
- **Description**: The **enforcement** is T-18's (`create_payment` refusing a held store with
  `store_billing_suspended`) and T-22's (the account gate). What is left here is making those codes
  legible: declare `store_billing_suspended` and `account_restricted` in the documented error set
  (`openapi.py:34`, `docs/api.md`), and mark held stores in the portal store list as
  "Suspended — plan limit" with the amount owed, a link to billing, and T-19's chooser.
- **Why it is not cosmetic**: three different refusals now exist — `store_disabled` (the merchant
  switched it off), `store_billing_suspended` (the platform is holding this store), and
  `account_restricted` (the whole account is on hold) — and the correct response to each is
  different. A merchant who cannot tell them apart calls support instead of paying.
- **Test**: A-18; each code reachable, and each documented.
- **Shipped (docs)**: `openapi.py` — the `ErrorOut` docstring, the `AUTH_ERRORS[403]` description
  and the `BAD_REQUEST_ERROR[400]` description each name the code and the situation behind it, so
  `/docs` distinguishes "the merchant switched this off" from "the platform is holding it" from
  "the whole account is frozen". `docs/api.md` — the payment-creation paragraph and the global
  error table gained `store_billing_suspended` and `account_restricted`, and the
  `payment_link_disabled` / `store_disabled` row was split so each code has its own meaning.
- **Shipped (UI)**: `web/landing/app/dashboard/stores/page.tsx` — held rows show
  `suspended — plan limit` instead of their `status` (a held store keeps reading `active`, which is
  the confusion the badge removes), a strip above the table names the count, the plan's allowance
  and the amount owed with a `Plan & billing` link, and each held row offers `Bring back` (T-19's
  route). The amount and the allowance are fetched **only once a held store has been seen**, so an
  ordinary account pays nothing for a panel it will never see; the amount is allowed to stay
  unstated rather than guessed at if that read fails.
- **Frozen accounts get a link, not a button.** While `restricted` the row offers
  `Resolve on billing` instead of `Bring back`, for the reason T-19 recorded: the gate serves only
  the billing writes, so the button could only ever answer 403. This is the same decision as the
  chooser's, made in the second place it applies.
- **Tests**: `tests/test_billing_invoices.py::test_a_held_store_is_refused_with_its_own_code` — the
  integrator's half of A-18: `POST /api/v1/payments` against a held store answers `400
  store_billing_suspended` while the store that kept its slot still mints. And
  `tests/test_openapi_schema.py::test_every_refusal_a_caller_must_tell_apart_is_documented` — the
  three codes appear in the published 400/403 descriptions *and* in `docs/api.md`, read from disk
  with the same skip-if-absent rule the docs-page test uses.
- **Browser-verified** in both states: active — the strip quoting the count, the allowance and
  $9.99, held rows badged and offering `Bring back`, and a swap that leaves 3 suspended while the
  flash names both stores; frozen — no `Bring back` anywhere in the DOM, `Resolve on billing` a
  live anchor on every held row, the table still rendering.

---

## Wave 5 — Documentation and the deferred channel

### T-15 — Update `docs/api.md` and `docs/data-model.md`
- **Status**: `complete` · **Priority**: S3 · **Depends on**: T-10
- **Description**: The billing section gains `/notices`, the new invoice fields, the status set
  including `void`, and the renewal timeline so an integrator can see what the merchant
  experiences. The payments error table gains `store_billing_suspended` and says how it differs
  from `store_disabled` — a hold from the platform rather than a switch the merchant threw.
  `data-model.md` gains `plan_invoice_reminders`, the new `plan_invoices` columns, and
  `stores.billing_suspended_at`, described as a platform hold that is orthogonal to `status`.
- **Shipped**: `docs/api.md` — the payments error table and the payment-creation paragraph already
  gained both codes under T-20; this adds a `## Billing and plan renewal` section with the
  `D-7 → D+7` timeline, the three ways out of a freeze, the late-payment rule, the store cap, the
  plan-invoice field table with the full status set including `void`, and `GET /api/v1/billing/notices`
  (its states, that `frozen` outranks every tier and describes the account rather than a row, and
  that the state is derived on read so a worker outage cannot hide a warning). The `## Quota`
  section gained the mid-month deferral rule. `docs/data-model.md` — `stores.billing_suspended_at`
  in the column table plus the lifecycle paragraph, and the `billing / quotas` table corrected and
  extended.
- **The billing/quota table was stale beyond this feature.** It named `subscriptions`,
  `quota_ledger` and `plans.tx_limit / store_limit / price_cents`, none of which exist — the real
  tables are `plan_subscriptions`, `plan_ledger_entries` and `plans.base_payments_included /
  max_stores / monthly_fee_cents`. Adding `plan_invoice_reminders` to that list without fixing the
  names around it would have produced a reference that is half true, so the whole table was
  rewritten against `models.py`. Nothing else in `docs/` referred to the old names.
- **No test**, per the task: the only docs assertions in the suite are T-20's, and they pin the
  refusal codes rather than prose.


### T-16 — Phase B: email reminders
- **Status**: `complete` · **Priority**: S3 · **Depends on**: T-08
- **Description**: `channel = 'email'` rows in `plan_invoice_reminders`, one template per
  tier, same deep link, no embedded QR. Requires a provider, credentials and a
  deliverability story; the unique key already makes adding it non-retroactive.
- **Shipped**: on **Resend**, from `billing@chmaba.com` (D2 resolved — the provider choice was
  the only thing blocking this). `services/resend.py` is the client, shaped like `telegram.py`:
  one module-level `http_post` so a test captures the transport, a typed `ResendError`, and
  `is_configured()` as the single "can this deployment send?" question. `notifications.billing_email`
  builds the message by **calling `billing_notice`** rather than writing a second copy table, so
  the banner and the email cannot drift — the row in `plan_invoice_reminders` is a claim that the
  merchant was told something, and two wordings would make that claim ambiguous. The link is the
  banner's own deep link made absolute by `billing.portal_url` (`_render_notice` keeps handing the
  browser a path, which has an origin to resolve against and an inbox does not).
- **The email phase is a second pass, after the in-app rows are committed.** A provider outage is
  the only failure in the sweep that happens on someone else's infrastructure, so it may cost the
  email and never the record of the warning. A failed send is written to `detail` as
  `{"delivery": "failed", "error": …}` and is **not** retried by a later sweep: an absent email row
  must keep meaning "we never tried", which is the claim a dispute turns on. `detail` carries
  `{"delivery": "sent", "provider_id": …}` on success — "we recorded that we sent it" and "they
  received it" are different claims, and the provider id is the only bridge.
- **The row is claimed before the message is sent.** The unique key on `(invoice_id, tier, channel)`
  can only stop two replicas double-sending if the row exists first. The Resend `Idempotency-Key` is
  derived from the message (`plan-invoice-{invoice_id}-{tier}`) rather than a clock, which covers
  the one case the constraint cannot: a sweep that dies between the send and the commit.
- **Non-retroactivity is asserted, not argued.** A deployment that switches the channel on
  mid-cycle emails the tier the merchant is *at* — one message, not five — because nothing is
  written while the channel is off.
- **The suite was sending real mail, and the guard is why it stopped.** `get_settings` is
  `lru_cache`d, so the first attempt at a guard — `monkeypatch.setenv("RESEND_API_KEY", "")` — did
  nothing, and a test that walked the dunning clock reached the live API with the key from `.env`
  (recipients were `@dunning.test`, a reserved TLD, so nothing was deliverable). `conftest`'s
  `_no_live_email` now takes two locks: the variable *plus* `get_settings.cache_clear()`, and a
  stubbed `http_post` that raises `AssertionError` — so a test that turns the channel on without
  stubbing the transport fails loudly instead of delivering. Verified load-bearing by removing the
  `cache_clear` and watching the suite fail with that `AssertionError` and no network call.
- **No template per tier, deliberately silent when unset.** With no key the phase returns
  immediately: no row, no log, no failure — the state every deployment was in before this existed.
- **A real send was verified** through this exact client (`send_email` → Resend accepted it,
  returning provider id `01a0c951-…`), which is the only way to prove `billing@chmaba.com` is a
  verified sender rather than a string that looks right. Delivery/bounce status lives in the Resend
  dashboard; nothing in the product reads it back.
- **Tests**: `tests/test_billing_dunning.py` — every tier emailed once in the banner's wording
  (subject, recipient, sender, absolute link, idempotency key, `detail`), the mid-cycle switch
  sending only the current tier, a provider outage costing the email and never the in-app record
  (recorded, not retried), and a settled invoice never emailed. `_tiers` gained a `channel`
  argument, because the two channels are independent and a test about the banner must not start
  counting emails.
- **Still not in the release**: merchant Telegram for dunning (§6 keeps it off `store.telegram_chat_id`),
  and any read-back of delivery status.

---

## Rollout

Enforcement is the only irreversible step, so it ships behind a flag and turns on last.

| Step | Ships | Live effect | Rollback |
| --- | --- | --- | --- |
| 1 | Wave 1 + T-13 | Invoices raised in the lead window. Nothing acts on a lapse — but a *voluntary* downgrade now enforces the allowance it chooses (T-18). | Revert the migration, which also drops `billing_suspended_at` and releases every hold. No non-payer's `next_billing_at` has moved. **Time-limited — see below.** |
| 2 | Wave 2 | Tier rows recorded, banner and billing rows show due dates, `frozen` copy renders. `enforce` is not scheduled yet. | Stop the heartbeat. Display is derived from `due_at`, so nothing regresses. |
| 3 | Wave 3 — T-22 and T-23 first, then T-09, then flip `billing_enforce_enabled` | Freezes begin: the account becomes read-only, no store can mint a code, and the billing page offers the three ways out. | Flip the flag off to stop further freezes, then set `status = active` for anyone caught by it. Nothing else needs undoing — the freeze writes no other row. |
| 4 | Wave 4 | Operator surfaces: void, overdue filter, chooser, `store_billing_suspended` and `account_restricted` in the docs. | Independent of steps 1–3. |

**The gate that matters**: `billing_enforce_enabled` stays false until the first real merchants
have been through a complete `D-7 → D+6` cycle with the notices showing, so that the first freeze in
production happens to someone who was demonstrably warned — and until T-23 is live, so the way out
of a freeze exists before anyone is put in one.

### Step 1's rollback is time-limited, and the window closes on its own

`0012` was rehearsed on Postgres 16 against production-shaped rows (a scratch database: `upgrade
0011`, a fixture with one row per status — `open`/`issued`/`draft`, a `paid` row, and an `open` row
with no subscription — then `upgrade head`). It did exactly what its docstring claims: the four
unpaid rows came out `void/pre_lifecycle` with `voided_at` set and the `paid` row untouched; every
`period_start` was backfilled from `created_at` with `period_end`/`due_at` left `NULL`; no
`next_billing_at` moved; the calendar-month constraint was dropped and the replacement landed as a
genuinely *partial* index (`WHERE status <> 'void'`); `plan_invoice_reminders` was created empty and
`stores.billing_suspended_at` came out `NULL`. Two behaviour probes passed as well: a re-issue for a
window the migration had just voided was **accepted** (the void releases it), and a second claim in
one calendar month was **accepted** — which the old key forbade, and which is the drift the whole
revision exists to fix.

Then the rollback. `alembic downgrade 0011` on a database that has **not** drifted is clean:
version back to `0011`, all five new columns dropped, the reminders table and
`stores.billing_suspended_at` gone, the calendar-month constraint restored. But it **fails** on a
database that has: recreating `uq_plan_invoice_period` hits `UniqueViolationError` because 0012 is
what makes two rows sharing a `(account_id, period_month)` possible in the first place. Dues 30 days
apart land in one calendar month whenever the first is the 1st of a 31-day month — Mar 1 and Mar 31,
Jan 1 and Jan 31, and five more a year — which is reachable for any merchant whose cycle anchors on
the 1st, within one billing period of deploying.

So: **the Wave-1 rollback has a deadline rather than a bug.** It is available for as long as no
account has two invoices in one calendar month, and after that it needs a de-duplication step the
migration cannot do for you. What it is *not* is dangerous: Postgres rolls the whole failed
downgrade back, so the attempt leaves the database at `0012` with every column intact rather than
half-reverted — verified, since the failed run reported `version=0012` with `period_start` and
`billing_suspended_at` both still present. Roll back early or not at all; do not treat step 1 as
reversible indefinitely.

---

## Verification before this is called done

- [x] A-01 … A-24 all pass.

      Seventeen are named in a test docstring (`A-01`–`A-08`, `A-10`, `A-11`, `A-15`,
      `A-17`–`A-23`) — `A-15` more than the paragraph here used to claim. `A-20` is named only
      as "part of" it (`test_a_shrinking_plan_holds_the_surplus_and_leaves_a_way_to_earn`, the
      chosen-cap half), and that turned out to be the whole of it: nothing asserted that a
      payment minted *before* a hold or a freeze still settles and still queues its webhook —
      the docstring claimed it, no test did it. Closed while verifying this line, with
      `test_billing_invoices.py::test_an_in_flight_payment_survives_a_hold_and_a_freeze`, which
      mints a code, holds the store, freezes the account, then settles and reads the queued
      `payment.completed` delivery.
      The remaining seven are asserted by tests that do not carry the label, mapped here so the claim
      is checkable rather than assumed:
      `A-09` → `test_account_security.py`: `test_every_mutating_route_refuses_a_frozen_account`,
      `test_a_frozen_account_reads_and_pays_but_cannot_write`,
      `test_an_api_key_is_refused_for_a_frozen_account`; `A-12` → `test_billing_notices.py`: the
      four state tests (`test_the_lead_window_is_an_informational_notice`,
      `test_a_tier_after_the_threshold_is_a_warning_that_cannot_be_dismissed`,
      `test_a_frozen_account_is_told_what_is_kept`, `test_nothing_is_due_so_there_is_no_notice`);
      `A-13` → `test_the_database_refuses_two_live_invoices_for_one_window`,
      `test_a_second_sweep_over_the_same_window_is_inert`; `A-14` →
      `test_legacy_invoices_without_a_window_do_not_occupy_one`; `A-15` →
      `test_the_billing_migration_retires_open_invoices_without_touching_the_schedule` (written
      under T-15 — `pre_lifecycle` was in no test before it); `A-16` → the void-then-re-issue
      half of `test_the_database_refuses_two_live_invoices_for_one_window`; `A-21` →
      `test_an_upgrade_releases_the_holds`, `test_the_cap_never_resurrects_a_store_an_operator_disabled`;
      `A-24` → `test_a_frozen_account_choosing_free_writes_the_debt_off_and_is_unfrozen`,
      `test_a_frozen_account_choosing_a_paid_plan_waits_for_the_money`,
      `test_paying_after_a_freeze_bills_from_the_day_they_paid`,
      `test_admin_actions.py::test_resolving_the_invoice_that_froze_an_account_unfreezes_it`.
- [x] `ruff check src tests` clean.
- [x] `tests/test_billing_invoices.py`, `tests/test_admin_plans.py`,
      `tests/test_admin_actions.py`, `tests/test_openapi_schema.py`,
      `tests/test_migrations.py` green — and the **whole suite green on Postgres 16**
      (`CHMABAPAY_TEST_DATABASE_URL=postgresql+asyncpg://…@localhost:55432/chmabapay_test`). That is
      the database which is authoritative in production and the one this suite's own conftest says
      it is "only trustworthy once it has run against": SQLite is the local default and is lenient
      about naive datetimes, which is most of what this feature is made of. The harness was
      confirmed to be reading the variable by pointing it at a dead port and watching it refuse to
      connect rather than quietly falling back.
      Run it with a clean environment. An ambient `WORKER_TRANSPORT=redis`, which a locally run
      `uvicorn` leaves behind, makes `test_admin_overview`'s in-process-transport test fail — the
      console then reads the Redis signals and the test is asserting the opposite. That failure is
      environmental, not a defect.
- [x] The route-coverage test from T-22 passes, and deliberately fails when a new mutating route is
      added without the gate — **it caught a real one rather than a planted one**. The
      `POST /api/v1/stores/{public_id}/activate` route added in T-19 was never classified in
      `test_audit.py`'s `AUDITED`, so the suite was red from that task onward and the gate named the
      route in its failure message. Classified as `store.slot_moved` (the action
      `move_store_slot` writes) — it decides whether money can be taken, so it is a mutation to be
      attributed rather than a `NOT_A_MUTATION`.
- [x] `0012` rehearsed on **Postgres** against production-shaped rows, both directions — the A-15
      test only ever exercised SQLite's `batch_alter_table` path (a table rebuild), where Postgres
      does real `ALTER TABLE` and creates the partial index directly, so the rehearsal is a different
      code path rather than a repeat. It did what its docstring claims, and it found that step 1's
      rollback is time-limited: see "Step 1's rollback is time-limited" in Rollout. Run in a scratch
      database (`chmabapay_dryrun`, created and dropped), so no existing database was touched.
- [x] Simulated timeline at `D-31 → D+16` recorded as evidence: one invoice raised at `D-7`, six
      tier rows and no more, `next_billing_at` unchanged until payment, one freeze at `D+7` that
      writes no store row, and an unfreeze on a payment at `D+16`.

      The walk is a test rather than a one-off script —
      `test_billing_dunning.py::test_the_whole_timeline_from_d_minus_31_to_d_plus_16` runs 1,129
      hourly heartbeats of issue → remind → enforce and asserts each of those five facts — and the
      transcript it produced is `evidence/timeline-d31-d16.txt`. Observed on Postgres 16, with
      `D = 2026-09-06T15:43:41Z`: the invoice appears at `D-7` (one row, `open`, window
      `[D, D+30d)`), `next_billing_at` still `D`, tiers `due_3, due_1, due_today, overdue_1,
      overdue_3, overdue_final`, `restricted` from `D+7` with the store untouched
      (`billing_suspended_at = NULL`, `status = active`) and exactly one `billing.account_frozen`
      audit row, then a payment at `D+16` leaving the lapsed invoice `void / grace_expired`, a
      granted invoice `paid` for `[paid_at, paid_at + 30d)`, and `next_billing_at` equal to that
      end.
- [x] The 50-store case walked both ways: **frozen** — all 50 stop minting and all 50 come back on
      payment with no restore step (A-10); **chosen Starter** — 5 mint, 45 held, and the chooser
      swaps them (A-18, A-19). Walked against the dev Postgres at the Pro ceiling, every refusal
      read off the HTTP surface with a real key rather than asserted against the service
      functions — the freeze lives in a route dependency and the hold lives in the payment
      service, so only the door a merchant walks through exercises both. Results:

      | phase | result |
      | --- | --- |
      | baseline | minted 50/50 `{201 x50}` |
      | frozen (`enforce_grace`, flag on for that one call) | refused 50/50 `{403 x50}` `account_restricted`; `{"due": 1, "frozen": 1}`; the chooser also answered 403 |
      | released (`lift_billing_hold`, the write settlement makes) | minted 50/50 `{201 x50}`; **0 store rows changed** — nothing to restore per store |
      | Starter cap | plan changed to Starter (allows 5), `apply_store_cap` held 45; live **5/50** minting `{201 x5}`, held 45/50 refused `{400 x45}` `store_billing_suspended`, no other code |
      | chooser | brought back `Branch 05`, displaced `Branch 04`, `moved=true`, still **5/50**; re-picking an already-live store `moved=false`, `displaced=null`, still 5; re-running `apply_store_cap` held 0 more and left the same set |

      Two things the walk taught, recorded so the next one is not misread:
      - **The rate limiter, not the freeze, explains a partial refusal.** `POST /api/v1/payments`
        allows 60 creations per key per minute, so the first attempt at phase 2 read
        "refused 10/50" — the baseline's 50 had already spent the budget and the other 40 came
        back `429 rate_limited: payment_create`. The walk now turns the limiter off for its run
        and prints the status distribution per phase, so a 429 can never be counted as a billing
        refusal. Any future walk that trips a limiter will show it as a status code rather than
        as a number that looks like a bug.
      - **The allowance is read off the live plan** (`plan_max_stores`), not off whatever number
        the last cap happened to use — see T-19. Capping by hand to 5 while the plan still said
        Pro is a state the product never builds, and the chooser is *right* to allow six there.
        A-19's "never six" therefore rests on the plan and the cap agreeing, which is what A-21's
        "an upgrade releases the holds" keeps true.
- [x] The route-coverage test from T-22 passes, and deliberately fails when a new mutating route is
      added without the gate — recorded above, where it caught a **real** unclassified route rather
      than a planted one (`POST /api/v1/stores/{public_id}/activate`, added in T-19, was never
      classified in `test_audit.py`'s `AUDITED` and the suite was red from that task onward). No
      planted route is needed to keep the claim true: the mutating set is enumerated from the app's
      own route table, so a new route enters the comparison the moment it is registered. That bullet
      above and this one were duplicates of the same requirement.
Every box above is green. The three checks that used to sit here as permanently-open boxes —
"production check after Wave 1 / before enforcement / after Wave 3" — are not verification and
cannot be closed from a developer machine: each reads production *after* a wave is live. They are
steps in **Deploy-time checks** below, with no checkboxes, because a box that no local run can
ever tick is not an open task; it is a piece of the deploy, and leaving it in this list made
finished work look unfinished.

---

## Deploy-time checks — run these in production, in this order

**1. After Wave 1 is live, before Wave 3.** No schedule has advanced without money behind it, and
a hold only ever exists where the plan is genuinely smaller than the store count.

```sql
-- must return zero rows: a schedule that moved without a paid invoice paying for the window
SELECT ps.account_id, ps.next_billing_at FROM plan_subscriptions ps
WHERE ps.next_billing_at > ps.started_at + interval '30 days'
  AND NOT EXISTS (SELECT 1 FROM plan_invoices i
                  WHERE i.subscription_id = ps.id AND i.status = 'paid');
```

```sql
-- must return zero rows: a surplus of stores with nothing held is the leak T-18 exists to stop
SELECT s.account_id, count(*) AS stores, max(p.max_stores) AS allowance
FROM stores s
JOIN plan_subscriptions ps ON ps.account_id = s.account_id AND ps.status IN ('trial', 'active')
JOIN plans p ON p.id = ps.plan_id
GROUP BY s.account_id
HAVING count(*) > max(p.max_stores)
   AND count(*) FILTER (WHERE s.billing_suspended_at IS NOT NULL) = 0;
```

**2. Before switching `billing_enforce_enabled` on.** The first freeze has to land on somebody who
was demonstrably warned, so at least one account must have reached the final tier already.

```sql
-- must return at least one row, on an account still `active`
SELECT r.invoice_id, r.tier, r.sent_at, a.id AS account_id, a.status
FROM plan_invoice_reminders r
JOIN plan_invoices i ON i.id = r.invoice_id
JOIN accounts a ON a.id = i.account_id
WHERE r.tier = 'overdue_final' AND r.channel = 'in_app'
ORDER BY r.sent_at DESC LIMIT 5;
```

**3. After Wave 3 is live and the first account freezes.** The freeze followed the notice it claims
to follow, and it took nothing per store — the two facts §7.2 rests on.

```sql
-- details.invoice_id must name an invoice whose `overdue_final` reminder predates frozen_at
SELECT l.created_at AS frozen_at, l.target_id AS account_id, l.details
FROM audit_logs l WHERE l.action = 'billing.account_frozen'
ORDER BY l.created_at LIMIT 1;
```

```sql
-- must return 0 for that account: the freeze is account-level and holds no store row
SELECT count(*) FROM stores
WHERE account_id = <the account above> AND billing_suspended_at IS NOT NULL;
```

### Run 1 — 2026-09-22, immediately after `0012` deployed

Deployed `96e5f17` to `163.245.204.122` (`/opt/chmabapay`). The stack came up healthy
(`api`, `landing`, `admin`, `proxy`, `db` all healthy), the `migrate` one-shot exited **0**,
and Alembic reads **`0012 (head)`**. Check 1 was run at once; checks 2 and 3 stay open by
design, because they need events that have not happened yet.

- **1a — 0 rows**, and **1b — 0 rows**.
- **The voiding was a no-op.** `plan_invoices` held exactly one row — `paid`, `2026-09`,
  `$59.99` (a real Pro purchase) — so nothing was retired as `pre_lifecycle` and no
  `next_billing_at` moved. The destructive half of `0012` had nothing to destroy, which is
  the expected shape of a deploy onto an account that has paid its bill.
- **The HQ store resolves from the console, not the environment.** `ChmabaPay HQ`
  (`stores.id = 1`) belongs to `duke@chmaba.com`, carries `is_internal = true`, and holds one
  `aba_payway` link; `CHMABAPAY_HQ_STORE_ID` is empty in `deploy/.env` while
  `CHMABAPAY_HQ_PAYWAY_LINK` is set but unused — the sign-in bootstrap only seeds a store
  that does not exist. So `resolve_hq_store` answers `source = 'console'`.
- **Enforcement is off and now genuinely switchable.** Inside the `api` container:
  `BILLING_ENFORCE_ENABLED=false`, `BILLING_LEAD_DAYS=7`, `BILLING_GRACE_DAYS=7`,
  `BILLING_EMAIL_FROM=billing@chmaba.com`. Those being present at all is the `deploy/`
  passthrough fix from PR #4 proving itself: before it, the same `docker exec env` would
  have shown none of them.
- **The email channel was switched on later the same day.** `RESEND_API_KEY` was added to
  `deploy/.env` — backed up first as `deploy/.env.bak-20260922-172225`, which is therefore a
  clean rollback point — and the `api` container recreated to pick it up. Verified with a real
  send from `billing@chmaba.com`, accepted by Resend as `01a0ca24-ace0-75d1-aace-2cd40817e130`:
  the key is valid and `chmaba.com` is a **verified** sending domain.
  Be precise about what that proves. It proves the credentials and the domain; it does **not**
  prove the worker path, which is a different half of the same feature. W6 reads
  `settings.resend_api_key`, and with it non-empty every tier it records is also delivered to
  the account's own address — so §4.2's email half stays unexercised until a real invoice
  reaches a tier.
- `plan_invoice_reminders` exists and is empty — the table is new, and nothing has been
  dunned yet. That emptiness is also why the bullet above cannot be proven any sooner.
- **Not yet exercised, and worth watching for:** the platform-admin account holds a comped
  `pro` subscription with no invoice behind it, so W3 will raise a renewal invoice for it in
  its lead window (~2026-10-14) and start dunning the platform's own account. D4 exempts a
  platform admin from the freeze, so this cannot lock the platform out — but the notices will
  appear, and moving that account to Free (or voiding the invoice) is the tidy alternative.

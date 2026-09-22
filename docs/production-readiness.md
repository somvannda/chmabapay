# Production readiness plan

Working path from the current state to a service we can be on the hook for.
Ordered by risk, not by effort. Every item carries its own exit criteria; an item
is done only when its tests pass.

Companion docs: `docs/roadmap.md` (milestone history), `docs/architecture.md`,
`docs/detection.md` (how a payment is confirmed), `docs/pos-provider-integration.md`.

## Where we are

**Working and verified.** KHQR lifecycle end to end at the API and UI level:
dead codes are withdrawn (`410`) rather than redrawn, a merchant can mint a
replacement in one call (`POST /v1/payments/{id}/reissue`, with lineage), both
dashboard detail routes stop showing a dead code and offer "Generate new QR",
and the QR renders correctly in a browser (680px natural scaled to 220px CSS,
unclipped, real module data).

**The money path is proven.** On 2026-09-17 two real 0.10 USD payments were minted
through `POST /v1/payments` on the compose stack, scanned from a real ABA/Bakong
wallet and settled. The second also delivered a **signature-verified**
`payment.completed` — HMAC recomputed from the exact 498 bytes that went over the
wire — 104 ms after the row was written. `P0-2` is closed.

**Verified absent** (checked in source, not assumed): *error tracking* was the last
entry on this list. Audit coverage outside plan/billing, metrics, CI, Alembic, rate
limiting and exception reporting were all on it, and all now exist — see P0-1,
P0-4, P1-1, P1-2 and P1-3.

**Gate in place.** `ruff`, `alembic upgrade head` + `alembic check`, and `pytest`
(184 selected, 2 `live` deselected, on Postgres 16 and Redis 7) run on every push
and pull request. Making the suite offline-runnable is what turned it from
something that only passed on a developer's laptop into a gate. Locally it defaults
to SQLite, which spends ~13 s per test rebuilding the schema — set
`CHMABAPAY_TEST_DATABASE_URL` to a Postgres `chmabapay_test` database (as CI does)
and the same suite goes from ~35 min to ~2.5 min.

---

## P0 — Blocks a safe launch

### P0-1 Schema changes must run themselves

- [x] Adopt Alembic; make migrations the only schema path.

**Why.** `create_all()` at startup creates missing *tables*, never missing
*columns*. Schema changes live in `supabase/migrations/*.sql`, which nothing
executes. This already bit us: the local Postgres had 142 payments and no
`reissued_from_id`, so any restart would have thrown on every payment query.
`docs/roadmap.md` Phase 1 specifies Alembic; we deviated and paid for it.

**Done.** `alembic/` with two revisions:

- `0001` — baseline of the current schema (13 tables, 25 indexes).
- `0002` — converges legacy shapes left by the pre-Alembic era.

Startup runs `ensure_schema()`, which compares `alembic_version` against head and
**refuses to boot** otherwise, naming the command to fix it. Migrations are never
applied at boot. `run_migrations()` is called only by the CLI (`bootstrap`,
`set-password`). Tests set `SCHEMA_CHECK=false` because they build their schema
from the models.

**Verified.** `alembic upgrade head` on an empty database then `alembic check`
reports no new operations (the baseline matches the models exactly). The guard
was exercised in three separate processes: a migrated DB passes, a DB with
tables but no `alembic_version` is refused, and a DB stamped behind head is
refused naming both revisions. Revision 0002 is a no-op on a clean database and
converges a simulated legacy-shaped Postgres to an exact match.

**Known drift in the existing database.** Diffing the local Postgres against the
models found shapes `0002` fixes:

- `api_keys.store_id` and `webhook_endpoints.store_id` still exist — vestigial
  per-store scoping columns dropped from the models when sub-merchants were
  merged into stores.
- `stores` carries its uniqueness as a standalone unique index where the models
  declare a `UniqueConstraint` of the same name.
- `stores` is missing `ix_stores_external_id`.

Adoption sequence for any database created before Alembic:

```bash
alembic upgrade head     # fresh database
# or, for an existing one: verify the schema matches, then adopt it
alembic stamp 0001
alembic upgrade head
```

**Remaining.** `supabase/migrations/README.md` documents the handoff; the old SQL
files there are marked superseded.

**Tests.** `tests/test_migrations.py` runs `alembic upgrade head` then
`alembic check` against a throwaway database, so a model changed without a
matching revision fails the suite. Its second test builds the pre-Alembic legacy
shapes on top of `0001` and asserts `0002` converges them. CI runs the same two
commands against Postgres (P0-4), which is where a column-level mismatch would
also surface.


### P0-2 Prove the live money path

- [x] Mint a real ABA checkout, scan it with a wallet, watch the row settle.
- [x] Watch a **signed webhook** leave the platform for that same settlement.

**Why.** This is the only test that proves the product.

**Done, 2026-09-17 — one real settlement, end to end.** A live-mode key on the
compose stack, a store pointed at the real link `ABAPAYpe518710Y` (merchant
`SOMVANNDA KONG`), and a real 0.10 USD payment from the operator's own wallet:

| | |
|---|---|
| payment | `oYgLRe3DJpvO3cnFidwr8Ynl` |
| minted | `07:05:10.191951Z` |
| `paid` | `07:05:41.365645Z` |
| observed latency | `31.2 s` |
| `bakong_ref` | `1789628740612824` |
| ABA tran id | `1789628839192554` |

Confirmed against ABA rather than only against ourselves: `POST
/v1/khqr/payway/status` for that session returns `action: "approved"`,
`paid: true`, plus ABA's own receipt download URL.

**What this settles.** The QR was *payable* — the half no test could reach. An
ABA-issued code, minted through `POST /v1/payments`, was scanned by a real wallet
and the row settled inside ABA's ~180s window with no human intervention on our
side. It also shows `BAKONG_API_TOKEN` is **not required** for this path: W1
skips its credential guard when a payment carries a hosted session, so the empty
token in `.env` was never the blocker it appeared to be.

**The signed webhook, verified twice.** A second real 0.10 USD payment
(`NQxsXqbS_WW71_pC-NHk-tZn`) against a registered endpoint, with a local sink
receiving on the host:

| | |
|---|---|
| row written `expired` (W4) | `07:44:34` |
| `payment.expired` delivered | `07:44:34.505` |
| row written `paid` | `07:53:34.522` |
| `payment.completed` delivered | `07:53:34.626` |
| delivery lag | **104 ms** |

The signature was checked against the bytes that actually went over the wire, not
against a re-serialisation that might happen to agree. The payload we stored for
the event (`events.payload`) serialised with the sender's own
`json.dumps(..., separators=(",",":"))` reproduces exactly the 498 bytes signed,
and HMAC-SHA256 over `f"{t}." + body` with the endpoint's secret equals the
`v1` in the header the sink received — character for character — and
`security.verify_signature` agrees. The delivered event carries
`data.payment.status: "paid"` and `amount: "0.10"`.

**And it caught a case no test covered.** The customer paid *after* the QR's
window closed, so W4 had already written `expired` and fired
`payment.expired` when the money arrived. The orphan sweep kept polling the
retired row — deliberately, per its own comment — ABA answered `approved`, and
`mark_paid` promoted `expired → paid`, firing `payment.completed` nine minutes
after the expiry event. Two lessons: the promotion path is real and load-bearing,
not a theoretical guard; and **ABA accepted a payment at 07:53 on a checkout we
minted at 07:41 with `expire_in_sec: 180`**, so our mirrored window is stricter
than the rail's actual acceptance. A merchant can therefore see `expired` and
then `paid` for the same sale, which the dashboard has to render as a recovery
rather than a contradiction.

**What is still open.** Nothing for this item. Refunds, reversals and partial
payments remain untested — see §7 of `docs/bakong-gateway-notes.md`.

**Defect this test found, fixed.** `attempt_history` recorded only the **first**
poll of any payment and silently dropped every one after it — three payments,
each swept across a full 180s window, all showed exactly one attempt. The cause
was an in-place `append` followed by assigning the same list object back, which
SQLAlchemy reads as "unchanged" and drops at flush; the first write survived
because `None -> [entry]` *is* a change, which is what hid it. Fixed to build a
new list, and pinned by
`test_every_detection_attempt_is_recorded_not_only_the_first`, which drives the
appends across separate sessions — inside one session the list identity is the
same either way and the bug is invisible. This is the audit field a merchant
would use to argue about a payment, and it was under-recording.

Confirmed on the rail after the fix: that second payment recorded **27**
attempts — one per 30s sweep from mint to settlement — where before the fix the
whole nine-minute polling saga would have collapsed into a single line. The last
entry is the one that actually settled it
(`hosted_action:approved`, `transitioned: true`, `matched_amount_cents: 10`).
A merchant asking "did you ever check this?" now gets an answer.

**A live run cannot be improvised on a fresh stack.** The compose database was
empty: no account, no store, no link. Provisioning is
`docker compose exec -T api python -m chmabapay.cli bootstrap`, then
`PUT /v1/stores/{id}/link` with the real PayWay link — the slug bootstrap invents
(`link.payway.com.kh/sokhaademostorea`) does not exist at ABA, so a hosted mint
against it fails. `CHMABAPAY_HQ_PAYWAY_LINK` seeds the same link on the HQ store,
but only via the OAuth/password sign-in path.

**Tests.** `test_live_aba.py` covers the half a machine can reach: the mint, and
ABA answering for the session it issued. Both of its tests were impossible to pass
until now, for two independent reasons, and neither was noticed because `live` is
excluded from the default run and no live link had ever been configured:

1. The fixture read the hosted session out of the API response body, from a field
   (`gateway_raw`) that `payment_out` never populates — the value only ever existed
   on the row. It now reads the row.
2. The fixture derived the link from the owner name through `make_store`, which
   lowercases it, so `ABAPAYpe518710Y` became `abapaype518710y`. A PayWay slug is
   case-sensitive, so the page carried no `aba_data` and every mint failed with
   `link_page_missing_aba_data` — a message that reads exactly like ABA changing
   its markup, and is actually a broken fixture. It now writes the link verbatim.

Both were fixed and **run green against the real rail** (`2 passed`), so this item
no longer rests on a test nobody had executed. Settlement itself still needs a
human, which is why the suite cannot close the item on its own.

### P0-3 Close the unconfirmable-payment hole

- [x] Refuse an offline QR when nothing in the deployment can confirm it.

**Why.** An offline QR can only be confirmed through Bakong Open API, and
`BAKONG_API_TOKEN` / `BAKONG_DEVELOPER_EMAIL` are empty. W1 then raises
`bakong credentials missing; cannot reconcile in live mode`. A caller could
create a payment that can *never* be marked paid.

**Done.** `create_payment` refuses `hosted_qr: false` for a live request only
when *nothing* can confirm it — no Bakong credentials and no dev fake rail. The
test is a fact about the deployment, not a preference of the caller, so it does
not restrict a correctly configured one: with `BAKONG_API_TOKEN` set, offline
payments are allowed because they can actually be reconciled. Test-mode keys
stay exempt, since W1's test-mode bypass settles them without any ledger at all.

**Blast radius: none, by construction.** The dev integration-test runner
(`/_dev/integration-test`) pins `hosted_qr: false` on its payment cases, and it
is only mounted when `ENABLE_DEV_GATEWAY` is on — the exact condition under
which offline payments *can* be settled. So the 1272-case sweep and the local
dev flow are unaffected, while a production deployment with no Bakong
credentials refuses the request outright.

**Tests.** `test_offline_qr_is_refused_when_nothing_can_confirm_it` sets
production-shaped settings (no dev rail, no credentials) and asserts: live key +
`hosted_qr:false` → 400 with `offline_qr_requires_a_confirmation_source`; a
test-mode key → 201; omitting `hosted_qr` never trips the guard.

### P0-4 A CI gate that actually blocks

- [x] GitHub Actions: ruff + mypy + pytest against Postgres, required on merge.

**Why.** No CI existed. 20 tests lived in 2 files (payments 11, admin plans 9);
billing, keys, webhooks, reports, auth, checkout and store onboarding had none.
Six of the 20 failed without a live ABA connection, so the suite could not be
used as a gate at all.

**Done.** `.github/workflows/ci.yml` runs on every push and pull request:
`ruff check .` → `alembic upgrade head` → `alembic check` → `pytest` → `mypy`,
against a `postgres:16` service.

Making the suite runnable offline was most of the work:

- **The ABA rail is stubbed, not removed.** `tests/conftest.py` replaces the one
  function that reaches the network — `create_hosted_checkout` — with a realistic
  `HostedCheckout` built from a real captured PayWay payload, so anything that
  parses, hashes or re-renders the TLV still sees genuine structure. The hosted path
  stays exercised end to end, and the double carries ABA's own ~180s expiry, which is
  what makes the TTL assertions meaningful. Tests wanting our offline encoder still
  ask for it with `hosted_qr=False`. The same capture is asserted over, structure and
  CRC together, in `tests/test_khqr.py`.
- **Live tests are quarantined, not deleted.** `tests/test_live_aba.py` is marked
  `live`; `addopts` deselects it and it needs `CHMABAPAY_LIVE_PAYWAY_LINK` to do
  anything. `pytest -m live` is the opt-in.
- **Tests cannot be aimed at a real database.** `conftest.py` ignores the app's
  `DATABASE_URL` and reads `CHMABAPAY_TEST_DATABASE_URL`, refusing any non-SQLite
  URL whose database name does not contain `test`.
- 153 tests across 11 files: payments, admin plans, migrations, telegram, ratelimit,
  audit, observability, compliance, khqr, config, queue_redis — plus `test_live_aba.py`,
  which `addopts` deselects (the 2 deselected below). `khqr` and `config` were added by
  P2-2: the encoder had no tests at all, and the session-secret guard needed a way to be
  checked without booting the app. `queue_redis` is P2-3, and it is the one file that
  runs against an external service: `fakeredis` by default so the suite stays runnable
  with nothing installed, and a real `redis:7` in CI via `CHMABAPAY_TEST_REDIS_URL`,
  which is the same shape the database already used.

**The Postgres run found a real bug, which is the point.** The suite had only ever
run on SQLite, where it was green. On Postgres *every* test failed at setup with
`asyncpg: cannot perform operation: another operation is in progress`:
pytest-asyncio gives each test its own event loop while the engine is module-level
(the app owns it), so pooled connections were reused across loops. SQLite
tolerates that; asyncpg does not. `_fresh_db` now disposes the pool per test. That
is precisely the class of backend-specific defect a SQLite-only suite hides, and
the reason the gate must run where production runs.

**Also fixed while wiring it up.** The fixture's teardown dropped the tables but
left `alembic_version` stamped at head — an empty database that `ensure_schema()`
would happily accept, i.e. the "boots onto a schema it would crash on" state P0-1
exists to refuse. The fixture now wipes the stamp along with the schema.

**Scope, stated rather than hidden.** `ruff` now covers the whole repository:
the `extend-exclude = ["scripts"]` escape hatch is gone, because P2-2 deleted the
`scripts/` directory it was there for. `mypy` remains advisory
(`continue-on-error`): 26 pre-existing errors in 9 files.

**Verified.** Against a freshly created `chmabapay_test` on local Postgres 16:
`ruff check .` clean; `alembic upgrade head` applies every revision;
`alembic check` reports "No new upgrade operations detected"; `pytest` →
**98 passed, 2 deselected**. The same suite passes on SQLite with no
environment set, so the offline developer loop still works. After the run the
fixture leaves 0 tables and no stamp, and `alembic upgrade head` + `alembic check`
are clean again.

The counts in this section move as items land — re-run the four commands rather
than trusting the number; the sequence is the claim, not the total.

**Exit criteria.** Every push gated; the suite is green with no network. ✅

**Tests.** The gate must pass from a clean clone. That is the test.

**Remaining.** The repository still has no commits (`master` is unborn), so
nothing is gated until it is pushed. Make `verify` a required check on the default
branch, or it reports without blocking.

### P0-5 Make alerting real, or remove it

- [x] Implement the Telegram send, or delete the endpoint. — **implemented**

**Why.** `POST /v1/stores/{id}/telegram/test` logged `"Would send Telegram test
msg"` and returned `ok: true`. Nothing was sent. That is a false success reported
to a merchant who is checking whether their alerts work.

**Done.** The send is real. `services/telegram.py` posts to
`https://api.telegram.org/bot<token>/sendMessage`, and the endpoint answers `ok`
only once Telegram has accepted the message. Each way of falling short is its own
answer instead of a log line:

| Situation | Answer |
|---|---|
| Send accepted | `200 {ok: true, chat_id}` |
| Store has no `telegram_chat_id` | `400 telegram_chat_id_not_set` |
| Deployment holds no `TELEGRAM_BOT_TOKEN` | `503 telegram_not_configured` |
| Telegram refused or was unreachable | `502 telegram_send_failed: <Telegram's own words>` |

`TELEGRAM_BOT_TOKEN` is new (`config.py`, `.env.example`).

**Note the HTTP 200 that is not a success.** Telegram reports a bad chat id as
`200 {"ok": false, "description": "chat not found"}`, so the status code alone is
not the verdict — `send_message` reads the body too, which is the difference
between "Telegram took it" and "Telegram took the request".

**Tests.** `tests/test_telegram.py`, six cases, with the outbound call captured at
the module-level seam (the same pattern `test_payments.py` uses for webhooks):
the alert sends with the chat id, the bot token in the URL and the store named in
the text; a refusal (`ok:false`, and a `401`) is a `502` carrying Telegram's
description rather than a success; an unreachable Telegram is a `502`; and the two
configuration gaps (`400`, `503`) attempt **no** send at all. The first test is the
one that fails if the handler ever completes without attempting a send.

**Sweep case updated.** The integration case was titled "never 5xx" against an
endpoint that could not fail; it now expects `[200, 400, 404, 503]`. The runner
already governs this — a 5xx listed in `status_in` is treated as a deliberate
answer, not an unhandled crash, exactly as the Bakong `503 bakong_not_configured`
cases do.

**Exit criteria.** Either alerts are delivered, or the surface is gone. No
`ok: true` without a send. ✅ — and delivery is now the only path to `ok: true`.

**Follow-up, not required for this item.** The dashboard renders the raw error body
(`Test failed: {"detail":"telegram_not_configured"}`). Honest, but the copy could
name the fix in plainer words.

---

### P0-6 Late settlement — money that arrives after the code dies

- [x] Detection must outlast the rail, and say when it stops looking.
- [x] One sale must never have two payable codes.
- [x] A refund must be recordable, or revenue is overstated forever.
- [x] Make it impossible for a consumer to mistake `expired` for a lost sale.

**Why.** The live run in P0-2 produced a payment that settled **nine minutes after
its own 180s window had closed**, and ABA accepted it without hesitation. That one
event showed the 180s expiry is a *display* window, not an acceptance window, and
three separate assumptions only held if customers pay promptly.

**What was wrong.**

1. **Detection stopped at a hardcoded 900 s, silently.** Beyond that nothing ever
   polled the row again — so a payment ABA accepted after 15 minutes would exist in
   the merchant's ABA account and nowhere in ours. A worse failure than a false
   paid, and invisible.
2. **A reissued code stayed payable alongside the one that settled.** Merchant sees
   an expiry, taps "generate new QR", the customer pays the original: two live codes
   for one sale, and a customer who pays both has been charged twice.
3. **A refund had nowhere to be recorded.** ABA offers no callback and its status
   endpoint reports no reversal, so nothing could express "paid, then given back". A
   refunded payment read `paid` for the rest of its retention window and every report
   built on it overstated revenue permanently.
4. **`payment.expired` was indistinguishable from a write-off** in the merchant's
   event stream, and could be followed by `payment.completed` for the same payment.

**What shipped.**

- `detection_window_seconds` (1 h default) replaces the 900 s constant. An hour is
  deliberately generous, not tuned: the ceiling of ABA's acceptance window is unknown,
  and a default *below* it is a silent data-loss bug. The cost ceiling is documented
  in the setting itself — 2400 ABA calls/hour, raise the batch before the window.
- Every payment that crosses out of the window gets **one final check**. Three
  outcomes, and only one is escalated: settled (nothing to say), answered-but-unpaid
  (closed quietly — the ordinary fate of an abandoned QR), or **no answer**, which
  alerts an operator rather than dropping the question. `detection_closed_at` records
  the moment we stopped looking; `detection_closed_at IS NULL` is the gate, so a job
  lost to a restart is retried instead of suppressed.
- Settling a payment **retires any live replacement code** (`superseded`), withdraws
  its QR (`410`) and fires `payment.superseded`.
- `POST /v1/payments/{id}/reverse` records a refund: status `reversed`, `paid_at`
  kept, `reversed_at` added, `payment.reversed` fired, audited in the same
  transaction. `mark_paid` treats `reversed` as terminal, so a later poll — ABA's
  session knows nothing about the refund and still says `approved` — cannot resurrect
  a refunded sale.
- Events carry **`financial`** (`true` only for `completed` and `reversed`),
  `settled_late`, and both `created_at` and `paid_at`, with the reporting contract
  written down in `docs/api.md`.
- A suspected **double charge** — two settled payments sharing a store and a
  `reference_id` — raises a discrete operator alert naming both ids.

**Tests.** `tests/test_settlement_lifecycle.py`, 15 cases: late settlement retires
the successor and withdraws its QR; two settled payments on one `reference_id` alert
(both ids in the message) while an absent `reference_id` does not; reversal records,
audits, stays idempotent, is tenant-scoped, refuses unsettled money, survives a late
poll and blocks reissue; `expired` is non-financial while `completed` is financial
and flags `settled_late`; the sweep queues exactly one final check per crossing
payment and stops once closed; an unanswered final check alerts and records both the
stamp and the reason; and the usage ledger appends one row per movement, nets a
reversal to zero, and refuses to count the same movement twice.
`169 passed` on Postgres, `ruff` clean.

**Still open, deliberately.**

- **ABA's real acceptance window is unknown.** Nobody publishes it and the hosted
  status endpoint does not report one. Until someone does, the window is a guess with
  a documented reason rather than a measurement.
- **Reversal is merchant-initiated only.** Auto-detecting a refund needs a signal ABA
  does not currently give us.
- ~~A reversal does not move the plan ledger~~ — **closed.** The ledger writer was
  fixed first (below), and a reversal now appends a negative row, so a refunded payment
  nets to zero for the period. Quota needed no change: it counts `payments` directly,
  and a reversed payment is no longer `paid`.

---

### Fixed: the plan-ledger counters over-counted quadratically

- [x] One row per movement, carrying its own amount.
- [x] The unique key the writer's `ON CONFLICT` always needed.
- [x] A reversal records a negative row instead of being unrepresentable.

`_increment_plan_ledger_counters` (`services/payments.py`) used to run:

```sql
INSERT INTO plan_ledger_entries (...) VALUES (...) ON CONFLICT DO NOTHING;
UPDATE plan_ledger_entries
   SET total_payments_count = total_payments_count + 1,
       total_volume_cents   = total_volume_cents + :amount
 WHERE account_id = :aid AND period_month = :period;
```

Two problems that compound:

1. **The `ON CONFLICT DO NOTHING` can never fire.** Verified against `0001` and the
   model: `plan_ledger_entries` has a primary key on `id` only, plus three *non-unique*
   indexes. There is no unique constraint on `(account_id, period_month, resource_type,
   resource_id)` for the clause to match, so every call inserts a fresh row.
2. **The `UPDATE` has no resource scope.** It matches every row for the account and
   month, not the row just inserted.

So N settled payments in a month create N rows and add `:amount` to *all* of them each
time: the counters inflate as `N(N+1)/2` rather than N. Ten payments leave rows valued
3, 2 and 1 … and a sum of 55.

**Impact today: none, because nothing reads this table.** Stated as a check, not an
assumption — the only references to `plan_ledger_entries` in `src/` are the model and
the two statements above; no code reads `total_payments_count`, `total_volume_cents` or
`amount_cents_delta`. Quota is enforced from a different source entirely:
`check_plan_quota` calls `_count_paid_payments_this_month`, which is a direct
`COUNT(*)` over `payments` joined to `stores`. That count is correct, and the P0-6
reversal work improves it for free — a reversed payment is no longer `paid`, so it
stops counting toward the month's quota.

So this is **wrong data waiting to be trusted**, not a live billing error. It becomes
one the moment the M2 invoice aggregator described in
`ARCHITECTURE_AND_ENGINEERING_DESIGN.md` is built, because that aggregator is specified
to read exactly this table.

**What shipped.** The table was incoherent rather than merely mis-computed: it tried to
be both a per-payment log (`resource_id` names one payment) and a per-period summary
(period-wide counters). The columns describe a log, so the numbers now match them.

- Renamed to `_record_plan_ledger_entry`, because it no longer increments anything. It
  puts `1` and `:amount` in the `INSERT` and the period-wide `UPDATE` is **deleted** —
  each row carries its own movement, and `SUM(amount_cents_delta) GROUP BY period_month`
  is the usage.
- Migration `0006` adds the unique constraint on
  `(period_month, resource_type, resource_id)` that the `ON CONFLICT` clause always
  needed. Without it a retry double-counts, which is how the inflation started.
- A reversal appends a **negative** row (`resource_type='payment_reversal'`) rather than
  editing the original, so a refunded payment nets to zero while the history still shows
  both the credit and the give-back.
- Existing rows are **deleted**, not migrated: their values are sums across rows that
  cannot be un-mixed, and the period totals are wrong in a way the row itself does not
  disclose. Safe because nothing reads the table.

**Tests.** Three cases in `tests/test_settlement_lifecycle.py`: two settlements produce
two rows carrying `100` and `200` — not a shared running total — with each row counting
itself; a reversal appends `+750` and `-750` and the period nets to zero; and three
repeated writes for one movement leave exactly one row, which is the constraint doing
its job. `169 passed` on Postgres, `ruff` clean.

**Still worth deciding, later.** Whether the ledger should be populated from history
(`payments WHERE status='paid'`) so usage reporting has a baseline when the M2 aggregator
lands. That is a reporting decision, not a schema one, and nothing is blocked by it.

---

## P1 — Required for a credible fintech

### P1-1 Rate limiting and CORS lockdown

- [x] Throttle `/v1/*`, `/pay/*`, `/auth/*`; replace the wildcard origin.

**Why.** No rate limiting anywhere, and each unthrottled create can trigger an
outbound ABA call. CORS was `allow_origins=["*"]` with `allow_methods=["*"]` on
an API that authenticates with cookies.

**Done.** `ratelimit.py` — one middleware, five buckets, nothing to remember to
decorate:

| Bucket | Paths | Counted per | Default |
|---|---|---|---|
| `payment_create` | `POST /v1/payments`, `POST /v1/payments/{id}/reissue` | API key | 60/min |
| `khqr` | `/v1/khqr/*` | address | 60/min |
| `auth` | `/auth/*`, `/api/v1/auth/*`, `/user/google/auth/*` | address | 20/min |
| `checkout` | `/pay/*` | address | 120/min |
| `api` | the rest of `/v1/*` | API key | 600/min |

`/health` and `/_dev/*` are exempt. A refusal is `429` with `Retry-After`, a JSON
body naming the bucket, and `X-RateLimit-Limit` / `-Remaining` on every limited
response so an integrator can back off before being refused.

**What is counted, and why it matters.** The two buckets that can spend money are
counted per **API key**, which a caller cannot rotate away — and both require a
key, so a per-key limit is exactly what bounds outbound ABA traffic. The
unauthenticated surfaces are counted per **address** instead. That asymmetry is
the point: `/v1/khqr/*` needs no key, so keying it by whatever credential the
caller presented would let a made-up token per request mint an unlimited supply
of fresh buckets. There is a test for precisely that.

**The unauthenticated ABA surface is the finding here.** `/v1/khqr/from-link`,
`/probe-aba-status`, `/payway/checkout` and `/payway/status` take no key at all
("no auth, no state", as one of their own docstrings puts it) and each reaches out
to ABA or PayWay. Before this, anyone could drive that outbound traffic without
bound. Now the `khqr` bucket caps it per address.

**Honest limits of the mechanism.** Counters are in-process, so this bounds one
replica — the same constraint the in-process workers carry until P2-3, and one
more reason to run exactly one replica until then. Under memory pressure an
evicted counter forgives a caller: it fails *open*, which is the right way round
for a limiter that must not take the API down.

**CORS.** The wildcard is gone. `cors_origins()` resolves, in order: an
operator's `CORS_ALLOWED_ORIGINS`, else `PUBLIC_ORIGIN`, plus localhost when
`ENABLE_DEV_GATEWAY` says this is a dev deployment. An empty result is valid and
secure — no cross-origin browser caller at all, which is the truth when the
frontends are reverse-proxied onto this origin. `allow_credentials` is now
explicitly `true`, which is only safe *because* the origin list is a real
allowlist: browsers reject `*` combined with credentials, which is why the
wildcard had to go for a cookie-authenticated API. Methods are listed explicitly;
request headers stay a wildcard deliberately, since the origin allowlist decides
who may call and a narrow header list only breaks integrators.

**Middleware order is load-bearing.** The limiter is registered *before* CORS so
CORS ends up the outer layer. A `429` a browser cannot read is indistinguishable
from the network dying, so a test asserts the refusal still carries
`Access-Control-Allow-Origin`.

**Tests.** `tests/test_ratelimit.py`, 13 cases: the payment path stops at its
ceiling and reports `Retry-After`; a second key keeps its own allowance (a limit,
not an outage); a rotated Bearer token does not buy a fresh bucket on the
unauthenticated surface; `/auth/*` and `/pay/*` are capped per address; buckets
do not spend each other; `/health` and `/_dev/*` are never limited;
`RATE_LIMIT_ENABLED=false` disables the lot; and on the CORS side, a known origin
is echoed with credentials while `https://evil.example.com` gets no
`Access-Control-Allow-Origin` at all.

**Exit criteria.** No endpoint can be driven unbounded; outbound ABA calls are
capped. ✅

**Operational note.** `/_dev/integration-test` has to run with
`RATE_LIMIT_ENABLED=false` — 1272 cases in a minute is abuse-shaped by
construction. Said in `.env.example` and in the sweep's own footer, because a
half-red sweep from `429`s would otherwise look like a regression.

### P1-2 Audit every privileged mutation

- [x] `AuditLog` on key create/revoke/rotate, webhook secret rotation, account
      suspension, payment reissue; plus an admin audit view.

**Why.** `AuditLog` was written only for plan CRUD and plan changes. The actions a
fintech must be able to attribute — who suspended an account, who rotated a
secret, who reissued a payment — were not recorded.

**Done.** One helper, `audit.record(...)`, staging the row in the caller's
transaction so the change and its record commit together or not at all. An audit
trail with a window where a mutation exists unattributed is worse than none,
because it looks complete. Fifteen call sites:

| Surface | Actions |
|---|---|
| API keys | `key.created`, `key.revoked`, `key.rotated` |
| Webhook endpoints | `webhook.created`, `webhook.updated`, `webhook.deleted`, `webhook.secret_rotated` |
| Stores | `store.created`, `store.updated`, `store.disabled`, `store.link_set` |
| Account standing | `account.suspended`, `account.activated`, `account.updated` |
| Payments | `payment.reissued` |
| *(already present)* | `plan.created`, `plan.updated`, `plan.retired`, `plan.deleted`, `plan.changed` |

No credential is ever written to the trail — not an API key, not a webhook
signing secret — and there is a test asserting exactly that.

**The money invariant.** Any change to where a store's money is sent writes
`store.link_set`: a store created with a link, a link attached to an existing
store, and a link replaced by a plain `PATCH` all produce it. The PATCH path is
the quiet one — `StorePatch.link` reaches the same service call as the dedicated
route — so it is the one worth naming. That link is the destination of every
future payment for the store, which makes it the highest-value thing in the
system to change silently.

**Two things the plan assumed that were not there.**

1. **Nothing could suspend an account.** `Account.status` exists, and `auth.py`
   enforces it on sign-in and on every authenticated request — but no code path
   ever *wrote* it. The platform could enforce a suspension it had no supported
   way to apply, and the BRD's `PATCH /v1/admin/accounts/{id}/suspend` was never
   built. `AdminAccountPatch` now takes `status` and an optional `reason`, so the
   action exists and the audit question "who suspended this account" has an
   answer. `reason` is optional: requiring it is a policy call that belongs with
   the legal review in P1-4.
2. **The actor column was named for the wrong actor.** `audit_logs.admin_account_id`
   already held *tenant* accounts — `billing.py` wrote the plan-changer's own id
   into it — and every merchant-initiated action above would have added more. It
   is now `actor_account_id` (revision `0003`), because the one question the
   column answers must never be read as "an operator did this". Nothing read it
   before, so the rename was free.

**The view.** `GET /v1/admin/audit-logs` — operator-only, filterable by `action`,
`target_type` and `actor_account_id`, newest first, with `actor_email` resolved.
The join to `accounts` is an **outer** join on purpose: a row left by an account
that no longer exists is exactly the row an audit trail must not drop. The
console has a matching page at `/audit` (`web/admin/app/audit/page.tsx`).

**Tests.** `tests/test_audit.py`, 27 cases. The first is the invariant that keeps
the rest honest: it enumerates every mutating route under `/v1/keys`,
`/v1/webhooks` and `/v1/stores` **from the OpenAPI schema** and fails if one is
neither audited nor declared as changing nothing — so a new endpoint cannot be
added without deciding. A second, parametrized test fails if a classified route
disappears, naming it. Then per surface: each action writes a row naming the
merchant as the actor; a revoked-twice key, an unchanged webhook PATCH and an
empty store PATCH write **nothing**; a webhook URL change records both ends of
the change; the three link paths record in order; a reissue records its lineage
while a replay records nothing; suspension records the operator, the from/to and
the reason; and the view is refused to a merchant key.

**Exit criteria.** 100% of privileged mutations attributable to an actor and a
timestamp. ✅ — enforced by the OpenAPI enumeration, not by inspection.

**Found while testing, not fixed here.** `get_hybrid_admin_context` resolves
`get_current_session_account` before it looks at `Authorization`, and that
dependency *raises* 401 when no cookie is present — so the `Bearer ck_` branch
below it is unreachable and `/v1/admin/*` is session-only in practice, whatever
the name says. Left alone deliberately: making the branch reachable would hand
platform-admin powers to API keys, which is an authentication decision, not an
audit one. Worth a separate decision.

**Confirmed at runtime** against the compose stack, after the fact:
`GET /v1/admin/accounts` with `Authorization: Bearer ck_…` and no cookie answers
`401 {"detail":"invalid_session"}` — the session sub-dependency's error, not the
hybrid dependency's own `unauthorized`, which is what proves the branch is
unreachable rather than merely rejected. `web/admin/README.md` used to claim the
key worked; that claim is now corrected. The dead branch itself is still here,
pending the decision above — the fix if machine access is ever wanted is a key
scope, not simply deleting the sub-dependency.

### P1-3 Observability

- [x] Structured logs carrying the trace id; counters and a latency histogram;
      alerts on worker death and webhook backlog.

**Why.** There was one `X-ChmabaPay-Trace` header. No metrics, no error tracking,
no alert when the detection worker dies — and if it dies, payments stop being
confirmed and nobody is told.

**Done.** Three pieces, in `observability.py` and `alerts.py`.

*Logs that carry the id.* The header already existed, so a request could be
traced across *responses*; without the id in the records it could not be traced
*through* them. A `ContextVar` bound by the middleware and read by a logging
filter now puts it on every line. Two defects fell out of doing this properly:
the old `current_trace_id()` minted a **fresh uuid** when unbound, so two calls
in one log line reported two different ids — worse than no id, because it looks
like correlation; and `configure_logging()` was needed at all because
`logging.getLogger(__name__).info()` anywhere in the package produced **nothing**,
the root logger having no handler and Python's last-resort handler dropping
everything below WARNING. Scoped to the `chmabapay` logger (uvicorn configures
its own), one handler, `propagate = False`.

*Metrics at `/metrics`*, Prometheus exposition format:

| Series | Labels | Source |
|---|---|---|
| `chmabapay_http_requests_total` | `method`, `route`, `status` | middleware |
| `chmabapay_http_request_duration_seconds` | `method`, `route` | middleware |
| `chmabapay_payment_events_total` | `event` | `services/payments.py` |
| `chmabapay_payment_settlement_seconds` | — | `mark_paid` |
| `chmabapay_queue_pending` | `queue` | transport, at scrape time |
| `chmabapay_queue_jobs` | `queue`, `outcome` | transport, at scrape time |
| `chmabapay_worker_heartbeat_age_seconds` | `queue` | transport, at scrape time |
| `chmabapay_alerts_raised_total` | `condition` | the watcher |
| `chmabapay_errors_total` | `where`, `type` | `errors.report_exception` |

Three decisions worth keeping:

1. **The `route` label is the template, never the identifier** — `/pay/{public_id}`,
   not `/pay/pay_9f3…`. A label per URL is unbounded cardinality, and a payment id
   in a label is a self-inflicted outage.
2. **The middleware is registered last, so it is outermost**, and therefore sees
   the status the caller actually received — including the `429` the limiter
   short-circuits and the `500` an unhandled exception becomes further in. A
   short-circuited request never reaches a route, so it lands in `<unmatched>` by
   design and has a test saying so.
3. **Queue figures are pulled at scrape time**, not written by a timer. A stalled
   process then shows up as a heartbeat age that *stops advancing*, which is
   visible; a value last written before the stall is not.

*Alerts.* Two conditions, both from the plan: a drain loop that has stopped, and a
webhook queue backing up. Paged over the Telegram transport P0-5 built.

- **Checked against the queues that should exist**, passed in from `workers_map`,
  not against the ones that happen to be reporting. A loop that never started is
  the most urgent case of all and is *invisible* to the other approach — it has no
  heartbeat to be missing.
- **Edge-triggered.** One message when a condition starts firing, one when it
  clears. A worker down for an hour sends one message, not one a minute.
- **Delivery admits its own limits.** With no chat id there is no page, so the
  watcher says so at startup instead of leaving a false impression of coverage,
  and each unsent alert still goes to the log so the reason it existed survives
  losing it.
- `watch()` reschedules with `loop.call_later` rather than looping — this codebase
  allows exactly one `while` in `src/` (NFR-2). The first check is one full
  interval away on purpose: checking at boot would race the drain loops and page
  someone about a worker that is still starting.

**Two things found while building it.**

1. **The queue-depth metric was wrong in the worst possible direction.**
   `InProcessTransport.metrics()` spread the cumulative counters over the result
   *last*, so `pending` reported every job **ever enqueued**. A backlog alert built
   on that fires hardest exactly when the workers are keeping up — an alert that
   trains you to ignore it. Fixed by assigning the live `qsize()` after the spread
   and keeping the lifetime figure under `enqueued`. The test is named for the
   regression.
2. **A worker that had never started was unobservable.** There was no heartbeat at
   all, only queue depth, so "the drain loop never came up" and "the queue is
   empty" were the same observation. `QueueTransport.heartbeats()` is new; it is
   stamped on entry to `dequeue()`, before any waiting, because the point is to
   record that the loop *asked*, not that it got anything.

**What the plan asked for that cannot be measured.** The plan lists "detection
latency p50/p95". Detection latency is not observable from inside the platform:
we never see the moment money moved, only the moment we looked. What *is*
measurable is settlement time — from the customer being handed a code to us
recording it paid — and it includes the customer's own time. The series is named
`chmabapay_payment_settlement_seconds` and documented as such rather than given
the name the plan hoped for. Likewise "ABA call failure rate" and "webhook delivery
failure rate" are not separate series here: both surface as payment events that
never arrive and as `chmabapay_queue_jobs{outcome="dead"}`.

**Tests.** `tests/test_observability.py`, 29 cases, all expressed as *deltas* —
the Prometheus registry is a process-wide singleton, so an absolute value would
depend on which tests ran first. Log format carries the bound trace id; a record
outside a request reports `-` instead of inventing an id; a real request through
the app binds the id it was given; `configure_logging()` installs exactly one
handler and is idempotent. Then a request is counted by route and status, latency
is recorded, the route label is the template and not the two ids used, and a
`429` is counted even though it never reached a route. Payment counters and the
settlement histogram move on a settled payment and on an expired one; a naive
timestamp (what SQLite returns for a tz-aware column) does not raise; an unpaid
payment records nothing. Queue depth is the backlog and not the lifetime total,
a drain loop reports a heartbeat, an empty queue still reports its depth, the
endpoint publishes the queue figures, a scrape without a transport does not
`500`, and a token gates the endpoint. Then the alerts: a queue that never
started is loudest; a stale heartbeat fires and a fresh one does not; the stall
threshold is tested from both sides (59s/61s); a backlog reports its depth; each
condition is sent once on its edge and once on recovery and not again; the alert
counter moves; an undeliverable alert reports failure rather than pretending; and
**the chaos test** cancels a live drain loop and asserts the watcher notices.

**Exit criteria.** Payment outcomes and settlement time are exposed at `/metrics`,
and the alert path is real — it pages over the Telegram transport P0-5 built, and
the chaos test proves the condition fires when a running worker is killed.

✅ **Both configuration parts are closed, and neither was closed by writing code.**

- **Something scrapes it** (2026-09-18). A Prometheus **agent** runs on the VPS behind
  the `monitoring` profile: it scrapes `api:8000/metrics` across the compose network and
  forwards to Grafana Cloud, so `/metrics` stays unpublished and no edge rule,
  certificate or firewall change was needed. Verified on the host rather than inferred:
  `chmabapay-api` reports `health=up` with an empty `lastError` on a 60s interval,
  `prometheus_remote_storage_samples_total` is advancing while
  `samples_failed_total` sits at **0**, and the agent costs **~22 MB RSS at 0.3% CPU**
  against a 256 MB cap. Deliberately agent mode and not full Prometheus: this host has
  one core and 1.9 GB shared with a live POS stack, and it was already swapping. Setup,
  the two silent failure modes it hit, and how to diagnose it are in
  `docs/deploy.md` §13.
- **No alert had reached a person.** `OPS_TELEGRAM_CHAT_ID` was unset when this was
  written, so every alert was a log line; the delivery path itself was proven (P0-5
  sends real messages and reports honestly when it cannot), but "a human is paged"
  was untested. **Since resolved** (2026-09-17): the chat id is set in production and
  the recipient confirmed the messages arrived on their device, so the path from
  `alert_discrete` to a human is observed. What remains untested is narrower — no
  *condition* has ever fired, since no worker has stalled and no double charge has
  occurred. See `docs/deploy.md` §11.

One thing is still worth stating plainly: a dashboard is not a substitute for the
alerting, and the reverse. Paging is what tells you something is wrong now; the
dashboard is what tells you whether the number you are looking at is normal. Only the
first of those existed before this, and only the first is a safety net.

*Error tracking.* Listed as absent when this item was written; it now exists, in
`errors.py`. This is not Sentry and does not pretend to be: no grouping UI, no
stack-trace search, nothing survives a redeploy but the log lines. What it is is
the capture point plus the channel P0-5 built — which is the part that reaches a
human today, and a Sentry-style transport would attach at `_send` rather than
replace any of this. Two properties make it safe to switch on:

- **Reported once, then summarised.** A fingerprint — the site, the exception type,
  and the message with its variable parts collapsed to `#` — is sent the first time
  it is seen. Repeats inside `error_report_interval_seconds` (default 300s) are
  counted, not sent, and the count rides along with the next report. A hot error
  that sends a message per occurrence gets the channel muted, and a muted channel
  loses the *next* genuine alert — worse than no tracking at all. The dashes in a
  uuid have to collapse as one unit for this to hold: the bare hex rule fragments
  `8f3c1d2e-4a5b-…` into `#-#-#-#-#`, which no longer matches the same payment's
  short id, and matching those two spellings is the entire point of the rule.
- **Never raises, never swallows.** The traceback is logged at ERROR
  *unconditionally, before delivery is attempted*, so the log is complete even when
  the channel is not; a failed delivery is logged and dropped rather than allowed
  to replace the failure being reported. The counter is deliberately separate from
  the message: the gap between "counted 400 times" and "sent once" is the severity.

Two attach points. Unhandled **API** exceptions are captured in the trace
middleware rather than in a handler for `Exception` — such a handler is installed
as `ServerErrorMiddleware`, which sits *outside* the middleware, so by the time it
ran the trace id would already be unbound and the report could not name it. The
exception is re-raised, so the response is exactly what Starlette would have sent
without us. **Workers** report on every failed attempt, not only the one that kills
the job: the first retry is the earliest signal that something new is broken, and
the dedup is what makes that free. `where` is a metric label, so it is a small
fixed set — `api <METHOD> <route template>` and `worker:<queue>` — while the
free-text detail (the concrete path, the job's dedup key and attempt) never becomes
one.

**Tests.** `tests/test_errors.py`, 10 cases: a new error is sent and a repeat is
not; the next window carries how many were suppressed; the window is per error, not
global; ids, uuids and amounts collapse to one fingerprint while genuinely
different messages stay distinct; the same message from two sites is two errors;
every occurrence is counted even when it is not sent; a delivery failure does not
become a second exception; an unhandled route answers `500` *and* reports with the
request's trace id still bound — asserted as *not* the unbound `-`, which is what
it would say if the capture sat one layer too far out; and a worker failure reports
its queue and its job. The window is tested with a hand-cranked clock rather than a
sleep, and the helper replaces `errors.time` instead of patching `time.monotonic`:
the latter edits the shared module object and stops the clock for the whole
process.

### P1-4 Compliance baseline

Split by part, because three of the five are done and two are not — and a single
checkbox could only ever say "no" to the whole thing, which hides the three.

- [x] Merchant agreement drafted, with an explicit no-custody clause and a
      restricted-business list (`docs/legal/merchant-agreement.md`).
- [x] Terms and Privacy pages served — they were dead footer links until now — with
      `/contact` alongside them.
- [x] Retention policy for stored credentials, defined and *enforced*: a 90-day
      purge of `payments.gateway_status_raw`, which holds the ABA session token
      verbatim (`docs/legal/data-retention.md`, W5).
- [x] A written answer on on-behalf-of usage (`docs/legal/on-behalf-of.md`).
- [ ] Legal review of the agreement and the retention policy. **Needs a lawyer.**
      Both documents are drafted, explicitly marked unreviewed in-place, and list
      their open questions; no agent can close this one.
- [ ] KYB capture at onboarding. **Not done, by an earlier decision.** See the
      contradiction below — this reverses two recorded decisions rather than
      representing an omission.

**The two open parts, on purpose.** Neither is a defect: one is a scope decision,
the other needs a lawyer. While this was a single checkbox it could only be left
unticked, because ticking it would have claimed both — which is why the parts are
listed separately above, so the three that landed are visible next to the two
that did not.

**Why.** Migration 6 dropped KYC entirely, there is no merchant verification, and
the site carries no legal pages. `docs/roadmap.md` flags the on-behalf-of question
as the single biggest legal/technical risk.

**A contradiction in the item itself, and how it was resolved.** This bullet asks
for "KYB capture at onboarding". That reverses two recorded decisions:
`supabase/migrations/6-drop-kyc.sql` deleted every identity column, and
`docs/roadmap.md` states *"Merchant due diligence (KYC/KYB) is your own legal duty,
**not a platform feature**"*. Asked to choose, the call was **contract only: record
acceptance of the agreement, add no business-category field, block nothing.** That
decision is the reason the "a restricted business cannot be activated" criterion
below cannot be met, and it is recorded rather than quietly dropped.

#### Landed

**Acceptance of the merchant agreement is now recorded and attributable.**

- `accounts.terms_accepted_at` and `accounts.terms_accepted_version`
  (revision `0004`). The version is stored next to the timestamp because the
  question that matters later is *"to which text did they agree"* — a timestamp
  alone cannot tell an account that accepted the current agreement from one that
  accepted a superseded draft. New accounts start **null**: backfilling a
  timestamp would fabricate consent that was never given, which is the one thing
  this record must not contain.
- `POST /v1/me/terms` records it and writes `account.terms_accepted` to the audit
  trail with the actor and the version. The caller must send the version it was
  shown and a mismatch is refused with `409` — recording whichever version the
  server happens to publish would attribute a stale page's acceptance to text the
  merchant never saw. Re-accepting the same version is a no-op and writes no
  second audit row: a reload is not a second event.
- `/v1/me` exposes `terms_accepted_at`, `terms_accepted_version` and
  `terms_required_version`, so a client can tell "accepted" from "accepted
  something we have since replaced" without hardcoding the version.
- `TERMS_VERSION` lives in config. It is the *published* version and must move
  with the text.

**Retention of stored credentials is defined and enforced.** `docs/roadmap.md`
requires exactly that of the per-merchant credentials we hold:

- **90 days** for `payments.gateway_status_raw`, which carries the ABA session
  token verbatim. Enforced by `purge_gateway_payloads` and W5
  (`RetentionSweeperWorker`, queue `payments.retention`), which runs daily **and
  at every boot** — a job that only fired a day after start would never fire at
  all on a service that restarts more often than that.
- A non-positive window is refused rather than obeyed: `0` must not read as
  "delete all history".
- Account history survives. Amounts, references, the QR and `attempt_history` are
  the accounting record of money that moved; only the credential column is
  cleared. Full policy: `docs/legal/data-retention.md`.

**Terms and Privacy pages now exist — they were also dead links.** The landing
footer has linked `/terms` and `/privacy` since it was built, and both 404'd.
`web/landing/app/terms/page.tsx` and `.../privacy/page.tsx` now serve them, with
the data inventory written from the schema rather than a template. `/contact`, the
third dead footer link, followed: `web/landing/app/contact/page.tsx` now serves it
with the three addresses (support, legal, privacy) that the pages above send people
to, and `sitemap.ts` lists all three.

**The on-behalf-of question now has a written position**:
`docs/legal/on-behalf-of.md`. Its substantive finding is that the architecture has
largely *designed the question out* — the platform holds no standing credential
that can read a merchant's transactions, because each ABA confirmation uses a
session minted per payment from the merchant's own share link. The residual is
real and open: the Bakong Open API (the correct, bank-independent source) needs a
developer token, and the NBC declines to register developer emails, so
`verify_receipt` answers `401`. That refusal *is* the on-behalf-of answer so far,
arrived at empirically.

**A bug found while building the retention sweep.** `gateway_status_raw` is a
`JSON` column, and SQLAlchemy stores Python `None` in a JSON column as the JSON
*literal* `null`, not SQL `NULL` — verified directly, not assumed. So a purge that
"cleared" the field left a value that still satisfied `IS NOT NULL`, and a sweep
driven by that predicate would report the same rows as purged every day forever
while looking like it worked. The test caught it. Fixed on both sides: the column
is `JSON(none_as_null=True)`, and the purge writes `null()` explicitly. No DDL
change, so `alembic check` stays clean.

**Tests.** `tests/test_compliance.py`, 15 cases. Nine cover the agreement:
a new account has accepted nothing; accepting records the version and the moment
and persists; the acceptance names the merchant as the actor with the version in
its details; a superseded version is refused and records nothing; accepting the
same version twice writes one audit row; bumping the published version makes the
old acceptance visible as stale and forces a second, separately recorded
acceptance; an anonymous caller cannot accept; and a malformed version is rejected
before it reaches the record. Six cover retention: 91 days purged, 89 days kept,
the accounting record untouched, a `0` window refused, the configured window
honoured, and a second run finding nothing to do.

#### Not done, and why

| Criterion | Status |
|---|---|
| Legal sign-off recorded | ❌ **Needs a lawyer.** Drafts exist and are marked unreviewed in-place (a visible banner on both pages); the open questions for review are listed in `docs/legal/merchant-agreement.md` §7 |
| A restricted business cannot be activated | ❌ **Out of scope by decision.** No category field, no verification, no block — this is contract only, chosen deliberately |
| Retention defined and enforced | ✅ 90-day purge, implemented and tested |

Also drafted, deliberately unreviewed: `docs/legal/merchant-agreement.md` (the
no-custody clause, the restricted-business list, credentials and termination) and
`docs/legal/data-retention.md`. Both state plainly what is a decision and what is
a hole — including that the webhook signing secret is **stored in plaintext and
accepted as a risk for now**, because it must be recoverable to sign with.

#### Resolved after this was written

- **`accounts.account_type` was vestigial and has been dropped.** It held
  `individual`/`business`, the onboarding step that set it was deleted (*"single
  account type"*), and **nothing in `src/` read it to make a decision** — the only
  comparison was a change detection for the diff. The one place that branched on it
  was `web/user/`, the older portal, which **no compose service in either stack built
  or ran**, since `/dashboard` in `web/landing` replaced it. Migration `0008`
  drops it along with `account_type_explicitly_set`, and it is gone from the `/v1/me`
  PATCH body, the `/v1/me` and admin account responses, the session payload and the
  change-plan response.

  `ChangePlanOut.account_type_switched` went with it, and is worth noting: it was
  **hardcoded `False`**, so the BRD's Starter-to-Growth "auto-switch to business" was
  never live either. Nothing replaces the enum, because nothing ever depended on it —
  `whitelabel_enabled` already gated white-label, the plan already gated limits, and
  sub-merchants were never built in *this* schema: no `SubMerchant` model, no
  `routers/platform.py`, and no Alembic revision that creates one.
  `BUSINESS_REQUIREMENTS.md` and the `.trae/specs/` files still describe the concept;
  they are left as records of intent rather than rewritten.

  One artifact reads as though it disagrees, and is worth a sentence so nobody has to
  re-derive this: `supabase/migrations/4-merge-sub-merchants-into-stores.sql` selects
  `FROM sub_merchants` and then drops the table. That is the **pre-Alembic lineage** —
  its own `README.md` says those files "were the schema path before Alembic existed.
  Nothing executes" them now — so the table existed in the Supabase-era database and was
  folded into `stores` there, which is why a grep for `sub_merchants` finds a migration
  and no model. `0002_converge_legacy_schema` is where Alembic took over that schema,
  and `docs/data-model.md` has described the store-only shape throughout — its `stores`
  section is titled *a sub-merchant of the account (the account's own customer)*.

### P1-5 Launch gap closure

A full A-to-Z audit was run against the **deployed** production stack (not the working
tree), and its findings were registered as **50 gaps, `G-01`…`G-50`**, alongside the
decisions that settled them, in `.trae/specs/launch-gap-closure/`. `tasks.md` is the
authoritative record: 36 tasks in 7 waves, each with the reason it was needed and what
shipped. **34 closed; the two that remain are this deploy's own bookkeeping** (T-35) and
this section (T-36).

What the register changed, and why it was worth doing:

| Area | The gap that mattered |
|---|---|
| Access | Four KHQR routes drove an outbound ABA fetch — one of them spends a real ABA checkout session — and answered **422, not 401**, to an anonymous caller. The docs presented a single Bearer surface; the code had a hole in it |
| Money | A self-serve plan change to Pro ($59.99/mo) applied immediately, collected nothing and prorated nothing. Since P1-5 a paid tier is *bought*: the upgrade parks a `pending` subscription, raises an invoice for the period, and activation waits on that invoice being paid |
| Legal | Terms acceptance was collected but **nothing required it**. It is now the chokepoint for a new API key, which is where a merchant starts acting programmatically |
| Portal | Account erasure and key rotation were reachable but not honest about what they did; webhook secrets could not be recovered; the delivery log could not be retried |
| Console | An operator could not resolve a paid-but-expired dispute in-product, could not assign a plan, and could suspend an account without being told it revokes every session and API key at once. All of that is now in the console, each action with an audit row and its blast radius stated |
| Docs | The published `openapi.json` advertised neither credential and hid nothing, the docs page described endpoints that behave differently, the landing page named Bakong as a payment destination while `/api/docs` said ABA PayWay was the only one, and the advertised event list included two events nothing emits |

Decisions that closed a gap by **removing** a claim rather than building behind it —
recorded here because each one is a deliberate reduction in scope:

- **The CSV export gate is deleted, not enforced.** Every plan carried
  `csv_export_enabled = true` (Free included), so the `403` in `routers/reports.py` was
  unreachable — while the admin console let an operator switch it off and watch CSV keep
  working. Migration `0010` drops the column. A gate nobody can trip is worse than no
  gate: it teaches the operator that the toggle does something.
- **The nine Bakong ledger endpoints are documented as unavailable** and their group is
  rendered as such, because Bakong Open API credentials are deliberately not configured.
  The two reconciliation endpoints that *do* work without them
  (`GET /v1/transactions/check-status/{id}`, `POST /v1/transactions/verify-payment/{id}`)
  are now documented separately, which is what an integrator actually needs.
- **Contact stays email-only, with no status page and no response-time commitment.** The
  terms already offered no SLA (section 7); `/contact` now says so out loud instead of
  leaving it to be inferred. Revisit when there is a support rota to publish behind it.

**Deployed to `0010`** (2026-09-21), from `4c4bffa`. This carried the second destructive
migration group — `0009` runs `DELETE FROM plan_invoices` before adding
`uq_plan_invoice_period`, and `0010` drops `plans.csv_export_enabled` — so a snapshot was
verified first, per §12:

```bash
pg_dump -Fc > /root/chmabapay-pre-0010.dump   # 51,254 bytes, PGDMP magic
docker run --rm -v /root:/backup:ro postgres:16-alpine \
  pg_restore -l /backup/chmabapay-pre-0010.dump | grep -c 'TABLE DATA'   # 14
```

`0009`'s `DELETE` was checked to be the no-op it claims to be **before** running it, not
after: `plan_invoices` held 0 rows. Verified afterwards, on the host and from the public
internet:

- schema `0010`; `plans` still holds 3 rows and `accounts` 1; **14 public tables**, and the
  dropped `csv_export_enabled` column is the only one missing from `plans` — the migration
  removed a column, not a table;
- `uq_plan_invoice_period` exists, and `plan_subscriptions` still holds its 1 row;
- `migrate` exited **0**; `db`, `api`, `landing`, `admin` healthy; `proxy` was not recreated
  (no nginx change), so the edge never restarted;
- the **POS stack is untouched**: `deploy-front-1`, `deploy-api-1`, `deploy-db-1` all up 11
  days;
- publicly: `/health` ok, landing/docs/contact/terms/privacy **200**, `admin-pay` **200**
  and not serving the landing page, `/no-such-page` **404** with its own
  `<title>Page not found — ChmabaPay</title>` and `noindex`;
- `/openapi.json` declares `ApiKey` and `SessionCookie` and contains **no** `/v1/admin` or
  `/_dev` path;
- enforced: all four KHQR routes, `/v1/payments`, `/v1/stores`,
  `/v1/reports/payments.csv` and `/v1/transactions/check-status/…` answer **401** without a
  credential; `/v1/billing/plans` answers **200** (public by design);
- refused twice: `/auth/_dev/login`, `/_dev/integration-test` and `/metrics` are **404**
  from the internet — the edge does not route them — and `/metrics` answers **401** from
  inside the network, where `METRICS_TOKEN` is the only thing stopping a scrape.

The one thing P1-5 did **not** close, and did not touch: **P1-4's lawyer item.** The
merchant agreement is still a draft, still marked unreviewed in place, and still needs a
lawyer. This section exists to record what the code closed, not to move that item.

---

### P1-6 Second production audit

P1-5 closed the gaps the first audit found. This section records the **second** audit,
run the same day against production at `02fd387`, because re-running the same four tracks
against a deployed stack is the only way to know the first pass actually landed — and it
found another forty-odd, several of them in surfaces the first audit had already
"fixed". The register for this pass is Wave 8 of
`.trae/specs/launch-gap-closure/tasks.md` (T-37…T-46).

**Method, and its one limitation.** Same as P1-5 — read-only probes of both hostnames
plus a source cross-check of every page against the router that serves it — with extra
probes this time for page metadata, the legal text as rendered, security and cache
headers, `robots.txt`/`sitemap.xml`, a full internal-link crawl from the landing page,
and the unauthenticated status of every money route. The limitation is worth stating
plainly: **the merchant portal and the admin console were audited from source, not
rendered**, because no merchant or admin credentials were used. Everything asserted
about them is code-verified. Interactive behaviour — the new confirmation dialogs, the
failure-vs-empty states — was checked by reading the code and by the type and lint checks
in the Docker builds, not by clicking them.

**What it found, and what closed.** Six themes:

| Theme | The finding that mattered | Closed by |
| --- | --- | --- |
| Docs vs code | `check-status` advertised a `source` value that is never assigned and omitted the one that is emitted for every ABA-hosted payment; `verify-payment` was documented to 404 when it returns `200 {found:false}`; `superseded` was described backwards; five published endpoints appeared in neither document | T-37 |
| Schema accuracy | `POST /v1/payments` returns 201 and the published OpenAPI declared only 200, so a generated client was wrong at runtime | T-38 |
| Edge hardening | **Neither hostname sent any security header at all** — no HSTS, no `X-Frame-Options`, no `nosniff` — on two session-authenticated consoles; and every app page carried Next's `s-maxage=31536000`, a year of shared caching on HTML belonging to one merchant | T-42 |
| Merchant friction | Five pages reported a *failed fetch* as "no records yet", so a merchant with live stores saw an empty workspace; four buttons that destroy a live credential had no confirmation and no in-flight guard; payment history could not be paged past 50 | T-39, T-40, T-41 |
| Legal text | The Terms still said the restricted-business list "is subject to change following legal review" — the last visible trace of the draft state; and the plan-fee clause described a billing flow the code does not implement | T-43 |
| Operator dead ends | A store could be disabled but only the *merchant* could re-enable it; the HQ-store panel rerouted all plan-fee revenue with no confirmation | T-44, T-45 |

Two judgement calls worth recording as decisions rather than omissions. The CSP added at
the edge is `frame-ancestors 'none'` **and nothing else**: a `script-src` would have to
accommodate Next's inline bootstrap, and getting that wrong breaks both applications
instead of tightening them. And HSTS is sent **without `includeSubDomains`**, because
`chmaba.com` and the rest of its subdomains belong to the neighbouring POS stack — one
project's header must not reach into another's domain.

**The deploy.** `f51add1`, committed and pushed to `main`, then fast-forwarded on the
VPS and rebuilt. **No migration was involved** — the schema stayed at Alembic `0010` — so
no `pg_dump` was taken, unlike P1-5's. One new file, `deploy/nginx/security-headers.inc`,
is mounted into the proxy; it is mounted explicitly rather than copied into the image so
that a missing mount makes the proxy refuse to start rather than quietly serve traffic
without the headers.

Verified after the deploy, from outside: `strict-transport-security`,
`x-frame-options: DENY`, `content-security-policy: frame-ancestors 'none'`,
`x-content-type-options: nosniff`, `referrer-policy` and `permissions-policy` all present
on both hostnames **through Cloudflare** (which passes the origin's HSTS through, so the
zone toggle is belt-and-braces rather than required); `/dashboard` and the whole console
answer `Cache-Control: no-store` while public pages answer `s-maxage=300`; hashed static
assets keep `max-age=31536000, immutable`; `/terms`, `/privacy` and `/contact` each have
their own canonical and `og:title`; every public page 200; `POST /v1/khqr/from-link` and
`/v1/khqr/payway/checkout` 401 to an anonymous caller; `/v1/admin/overview` 401 and
`/_dev/integration-test` 404; `offset` and the `201`s are in the live OpenAPI; and the
POS stack was up 11–12 days throughout. In the repository: `ruff check` clean and **274
passed, 2 deselected** against Postgres, with landing and admin built in Docker with
their type and lint checks.

**The finding the method itself could not see.** The audit's stated limitation — the
portal and console were read as source, not rendered, because no credentials were used —
had a consequence worth recording: the review confirmed that the console is
*password-only* as a property of the code, and never asked whether an operator could
actually get in. It could not. Asked plainly, "what is the admin username and password?",
the answer was that **no platform-admin account existed**: `CHMABAPAY_ADMIN_EMAILS` named
`duke@chmaba.com` but no account row for it existed, `CHMABAPAY_ADMIN_PASSWORD` was empty,
the only account in the database was a non-admin merchant with no password, and the audit
log held zero sign-ins. `admin-pay.chmaba.com` was therefore unopenable by anyone, which
made all of T-44 and T-45 and the HQ-store panel unreachable in production.

Closed the same day with `uv run python -m chmabapay.cli grant-admin duke@chmaba.com
--name Duke` on the VPS, the password supplied through `CHMABAPAY_PASSWORD` on
`docker compose run -e` so it never entered the shell history or the process list of an
interactive shell. That created account id=2 with `is_platform_admin`, white-label and a
free subscription (the console's own account needs a billing row, because the HQ store
that self-pay charges against lives on it). Verified end to end rather than assumed:
`POST /auth/login` returns 200 with `is_platform_admin: true`, the session JWT carries
`amr: "password"` — the exact claim the admin gate inspects — and `GET
/v1/admin/overview` answers **200** with that cookie and **401** without it. The env
bootstrap password is deliberately left empty; the credential exists only as a hash.

**The platform was its own tenant, and metered itself.** The most consequential finding
came from a product question rather than a probe: "should we subscribe ourselves to our
own plan?" Checking, the platform already was — and worse. `check_plan_quota` had no
exemption and counted every paid payment on the account's stores, so plan fees paid *by
merchants* counted against the platform's own monthly quota; `_record_plan_ledger_entry`
is called with the paying store's owning account, so every fee wrote a usage and volume
row against the platform's own account; and the console's merchant volume summed paid
payments platform-wide, so platform revenue would have been reported as platform GMV from
the first paying customer. The books would have been wrong from customer #1.

Closed in T-47 by modelling the platform as a separate kind rather than a discount: a
store belonging to the platform is an **internal store** (`stores.is_internal`, migration
`0011`), a first-class category instead of an identity check inside the money path. Quota
enforcement, the usage ledger, month counting and merchant volume all skip an internal
store; the console reports **platform revenue** as its own figure instead of adding it to
merchant GMV; setting the HQ collection link marks the store internal, and an operator can
toggle the flag explicitly through an audited admin route. Verified: 278 tests pass, the
migration applied, downgraded and re-applied with `alembic check` reporting no drift, and
the production deploy proved the row counts unchanged across it.

**Still open, and not closeable here.** Three items, each stated rather than implied:

1. **P1-4's lawyer review.** T-43 removed the last code-visible trace of the draft state
   from the terms; it cannot substitute for the review. Unchanged.
2. **The registered entity's details — supplied, and now stated.** On 2026-09-21 the
   operator gave the contracting entity as **Chmaba**, registered at #62, Street P-10D,
   Sangkat Veal Sbov, Khan Chmbar Ampov, Phnom Penh, Cambodia, and said there is **no
   company number** to state. `/terms`, `/privacy` and the merchant-agreement draft now
   name that entity instead of the placeholder "ChmabaPay Technologies", which was not a
   company at all. `TERMS_VERSION` moved to `2` for the same reason the coupling in
   `config.py` exists: an account that accepted version 1 agreed to a text that did not
   identify its counterparty, so it must be asked again rather than have the new text
   attributed to its old acceptance. **What is still open is the cap:** §8 excludes
   indirect loss but sets no ceiling, and a liability cap is a decision for the lawyer,
   not something to be invented here.
3. **The HQ PayWay link** from P1-5's operator action. Until it is set, a paid plan
   change correctly answers `503 billing_not_open`.

---

## P2 — Scale and polish

### P2-1 Unify the duplicated dashboard routes

- [x] Collapse flat `/dashboard/payments/[id]` and store-scoped
      `/dashboard/[public_id]/payments/[pay_id]` into one page.

**Why.** Payment detail existed twice, maintained independently, and they had
already drifted — the dev "Test: Mark paid" action existed on only one. Every
future change cost twice.

**Which route won, and why.** The flat `/dashboard/payments/{id}`. The deciding
argument is a correctness one, not taste: **the store-scoped page ignored its own
store segment.** It fetched `/v1/payments/{pay_id}`, which takes no store
parameter at all, so `/dashboard/STORE_A/payments/{payment_of_store_b}` would
happily render store B's payment inside store A's switcher and tab bar. The
segment was decorative and could contradict the payment sitting under it. A
payment's id is globally unique, so the store was redundant addressing anyway.

**Done.**

- `next.config.js` gains a `redirects()` entry mapping
  `/dashboard/:public_id/payments/:pay_id` → `/dashboard/payments/:pay_id`. It is
  evaluated by the router **before** any page renders, so an old link resolves in
  a single HTTP hop.
- The duplicate page file is **deleted**, not left as a second redirecting
  implementation — leaving it would have preserved exactly the "maintained twice"
  problem this item exists to remove.
- The three callers now point straight at the canonical URL: the store's payment
  list (two links: the id and the "View" button) and the store overview's recent
  activity rows. `storeBase` in the store payments list became dead and was
  removed.

`permanent: false` (307) rather than 308 on purpose: a 308 is cached hard by
browsers, and if this route ever returns, withdrawing it in the field is painful.
One extra hop is cheaper than a redirect that cannot be recalled.

**Tests.** ⚠️ **No automated test — and that is a gap, not a choice.** The
repository has **no frontend test runner at all**: no jest, vitest or playwright
config under `web/`, and the landing `package.json` has no test script. Adding one
for a single route assertion would mean introducing a test stack as a side effect
of a polish item, so the assertion was made manually and reproducibly instead:

```
# start the landing app, then:
curl -s -o NUL -w "%{http_code} %{redirect_url}" \
  http://localhost:3001/dashboard/st_p21_demo/payments/pay_p21_demo
# -> 307 http://localhost:3001/dashboard/payments/pay_p21_demo
```

**Verified.** Two layers, because one would have been thin:

1. **HTTP.** The old URL answers `307` with the correct `Location`; the canonical
   URL answers `200`; and the sibling routes are *not* swallowed by the pattern —
   `/dashboard/{store}/payments` (list), `/dashboard/{store}` (overview) and
   `/dashboard/payments/new` all still answer `200` with no redirect. That last
   check matters: `:public_id`/`:pay_id` patterns are easy to over-match, and a
   greedy one would have broken the store's payment list.
2. **Browser, signed in, against real data.** Seeded a store and a payment, signed
   in as a platform admin, and drove the real flow: the store's payment list now
   links to `/dashboard/payments/pay_p21_demo` (both links, inspected as raw
   `href`); clicking through lands there and renders the payment; and the header
   shows **"Test: Mark paid"** — the action that previously existed on only one of
   the two pages, which is the drift being closed. Navigating to the old
   store-scoped URL followed a redirect (`redirectCount: 1`) to the canonical URL
   and rendered the same page including that button.

**Exit criteria.** One implementation, and every payment URL resolves to it. ✅

**Left for its own item.** A frontend test runner. Until there is one, every
frontend behaviour is verified by hand, which means it is verified once and can
regress silently. That is worth fixing before the next frontend change, not after.

### P2-2 Shipping packaging

- [x] App + both dashboards in compose behind a proxy, with healthchecks and
      rewritten env docs.
- [x] Move the ~25 scratch `debug_*.py` / `_*.txt` files out of `scripts/` and
      drop the ruff exclusion for it.

**Why.** `docker-compose.yml` shipped Postgres and Redis only — no app container,
no dashboards, no proxy, no deploy doc.

**Tests.** Cold-start test from a clean machine; `/health` wired into compose
`depends_on`.

**What shipped.** One `Dockerfile` for the API (uv, non-root uid 10001, inherited
healthcheck), one parameterised `web/Dockerfile` for both Next apps, and seven
compose services — `db`, `redis`, `migrate`, `api`, `landing`, `admin`, `proxy` —
with `api` gated on `migrate` exiting 0. `ENABLE_DEV_GATEWAY` is hardcoded false
rather than interpolated. `docs/deploy.md` is the operator document.

**Cold start, executed.** From no containers, no volumes and no cached images:
all six long-running services reached `healthy` and `migrate` exited 0. The
routing table in `docs/deploy.md` §5 is observed output, as are all four edge
refusals and the browser-level render of both apps (landing 45KB with its real
title; admin `ChmabaPay Admin`).

**Five defects the cold start found, all fixed.**

1. **The dev rail was live at the edge, and it minted sessions.** Compose read
   `ENABLE_DEV_GATEWAY` from the project's `.env` — which sets it `true` for the
   local dev loop — so `${ENABLE_DEV_GATEWAY:-false}` resolved to `true` and a
   plain `docker compose up` ran the fake rail. nginx refused `/_dev/*` by
   relying on there being no `location` for it, but the route that hands out
   credentials is `GET /auth/_dev/login`, which lives under the `auth` router and
   therefore matched the ordinary `/auth/` rule. It answered **307 with a valid
   `chmabapay_session` for any email supplied**. Confirmed by reproducing it, then
   confirmed closed: 404, no `Set-Cookie`. Fixed in three places — compose
   hardcodes the flag false, `config.py` defaults it to `False` so an omitted
   variable no longer produces the insecure state, and `api-locations.inc`
   refuses `/_dev/` and `/auth/_dev/` with an explicit `return 404`. Absence of a
   `location` is not a refusal.
2. **The stack could not sign anyone in.** The API's environment carried seven
   variables and none of the Google OAuth ones, so the header and hero CTAs both
   pointed at a route answering `400 google_oauth_not_configured`. Bakong,
   admin-bootstrap and HQ-store settings were missing for the same reason.
   `/auth/google/login` now returns a correctly built `accounts.google.com`
   redirect.
3. **Both Next apps 500'd on every rewritten path.** Two independent causes with
   one symptom. First, `NODE_ENV=production` was set before `pnpm install`, and
   pnpm 9 reads it and skips devDependencies — `next` survives as a real
   dependency, but `typescript` does not, and without it Next cannot load
   tsconfig `paths`, so every `@/` import failed as `Can't resolve '@/...'` over
   files that were present. Then `NEXT_PUBLIC_API_URL` was supplied only at run
   time, but Next resolves `rewrites()` during `next build` and bakes the
   destinations into `routes-manifest.json` — and `docker build --build-arg X=`
   silently ignores `X` unless the Dockerfile consumes it with `ARG X`.
4. **Nothing stopped a deployment signing sessions with a published secret.** The
   application defaulted `jwt_secret_key` to
   `generated-dev-secret-change-in-prod`, which is in this repository and in
   `.env.example` — so the documented onboarding step, copy the example file, produced
   a deployment whose session cookies anyone who had read the source could forge, for
   any account including a platform admin. Only compose caught it, via `:?`, which
   covers an operator who forgot the variable and nothing else. Now
   `assert_session_secret_is_chosen` runs in the lifespan before the database is
   touched and **refuses to start** on either the placeholder or an empty value;
   `.env.example` ships blank with the command to generate one. Verified by booting
   uvicorn with the placeholder set: `Application startup failed. Exiting.`, exit 3.

   **And then the guard was defeated within the hour, which is the more useful part.**
   The field default was changed to a real-looking secret (`jwt_secret_key: str =
   "..."`). That is the one place a secret must never live: `config.py` is committed and
   baked into the image, so the value is public, and because it was no longer the known
   placeholder the guard accepted it — the control was silently off precisely because
   someone had "fixed" the thing it complained about. The earlier version of
   `test_config.py` asserted `DEV_JWT_SECRET_KEY == Settings.model_fields[...].default`,
   which caught the change but for an incidental reason. It now asserts the property
   that actually matters — `Settings(_env_file=None)` must be *refused* — so a hardcoded
   secret fails the gate instead of passing it. The default is now empty, which makes
   "no secret configured" the only thing a missing environment produces.
5. **`scripts/` was excluded from `ruff`** — 44 lint errors across 25 files nothing
   referenced. Deleted, and the exclusion with it. One thing worth keeping from that
   review: `pytest`'s `testpaths = ["tests"]` meant the four `scripts/test_*.py` files
   were never collected, so the exclusion hid lint debt only, never a test. Details
   below.

**Environment facts this produced.** Three ways to be misled, all encountered here:

- Run it as **`uv run pytest`**, not bare `python -m pytest`. `test_migrations.py`
  shells out to `python -m alembic` as a subprocess, and `pythonpath = ["src"]`
  applies only to the pytest process — so under bare `python` the subprocess dies
  with `ModuleNotFoundError: No module named 'chmabapay'` and two migration tests
  fail for a reason that has nothing to do with the code.
- **Never kill a run mid-flight and then trust the next one.** Two interrupted
  SQLite runs left `chmabapay_test.db` and a hot journal behind, and the following
  run reported 12 failures and 10 errors of `no such table: accounts`. Deleting the
  file and re-running the same three files gave 38 passed. The local default DB is
  SQLite, and on this disk building the schema costs seconds per test.
- **A leftover shell variable beats `.env`, silently.** A `$env:JWT_SECRET_KEY` set
  during a boot test and then "cleared" was still set, so `Settings()` resolved to the
  placeholder while `.env` held a valid key — and the guard appeared to be rejecting a
  good secret. Environment variables outrank `env_file` in pydantic-settings, so the
  file is not what runs. Same class as the `$env:DATABASE_URL` hazard that already
  bit twice. In this wrapper `Remove-Item Env:\NAME` did not clear it;
  `[Environment]::SetEnvironmentVariable('NAME', $null, 'Process')` did.

**Exit criteria.** Cold start green, `/health` gating `api`, both dashboards
reachable through the proxy. ✅

**The `scripts/` half, reviewed rather than guessed at.** It was labelled "move
some scratch files"; reviewing it first turned up a coverage gap that mattered more
than the tidiness. Findings:

- **Nothing outside `scripts/` referenced any file in it.** Repo-wide search for
  every filename, and for `import scripts` / `from scripts`, returned nothing.
- **Four could not run.** `crc_verify.py`, `smoke_khqr.py`, `debug_crc.py` and
  `test_final_dual_mode.py` all did `from chmabapay.khqr import KHQR`, and no
  `class KHQR` exists anywhere in `src/` any more. Executed to confirm:
  `ImportError: cannot import name 'KHQR' from 'chmabapay.khqr'`.
- **The rest were superseded prototypes.** Every helper the `debug_*` scripts reached
  for — `fetch_link_html`, `_split_top_level_commas`, `_parse_js_arg`,
  `parse_nuxt_fields`, `extract_merchant_fields`, `TRANSACTION_SUMMARY_RE` — is
  imported *from* production, so they were the scaffolding discovering what `src/`
  already contains. `khqr_parser_generator.py`'s `crc16_ccitt_false`, `tlv`,
  `parse_tlv` and payload builder all exist in `src/chmabapay/khqr.py`.
- **Ten files were redundant iterations:** `debug_iife_*` (5, of which `args3` only
  dumped a string), `debug_match_step*` (3, of which 2 and 3 differed only by a
  `traceback` import), `debug_ssr_parser*` (2).
- **`_debug_html.txt` was a captured live checkout page**, including `p.aba_data` and
  a `tran_id`. Gitignored, so never committed, and worthless once expired — but not
  something to keep lying around either.

**The gap.** `crc_verify.py` was the only place in the repository that checked our
CRC-16 against a **real** captured ABA payload, and it had not run in a long time
because it could not. Looking for other coverage of the encoder found **none**:
nothing in the suite referenced `crc16_ccitt_false`, `parse_tlv` or
`build_khqr_payload`. The function that decides whether a customer's QR scans was
covered only incidentally, by whatever the payment tests happened to touch. That is
the real finding, and it was hiding behind a tidy-up item.

So the capture and its assertion were promoted into `tests/test_khqr.py` before
anything was removed — six tests, including that our CRC reproduces ABA's real one
(`630451AA`) rather than merely agreeing with itself. Only then were the 25 files
deleted, along with `extend-exclude = ["scripts"]` in `pyproject.toml`, the two
`.gitignore` entries for the data files, and the `.dockerignore` entry for the
directory. `tests/conftest.py`'s comment, which cited `_crc_qr.txt` as the payload's
provenance, now points at the test that holds it.

**Corrected while doing this.** An earlier version of this section claimed
`docs/bakong-gateway-notes.md` cited `scripts/_crc_qr.txt`. It does not — that file
contains no reference to `crc`, `_crc_qr` or `scripts`. Only `tests/conftest.py`
(in a comment) and this document did.

**A side finding, not acted on.** `src/chmabapay/khqr.py` comments Tag 30 as
carrying three sub-tags "Verified against a captured checkoutData.qr_string", and
emits `02 = "ABA Bank"`. The captured payload in this repository has only `00` and
`01` under Tag 30. Those may be two different captures — a merchant's static link QR
versus the hosted checkout's own dynamic QR — so this is not evidence of a defect,
but the comment's stated evidence is not in the repository and cannot be re-checked.
`tests/conftest.py` had described the same payload as carrying "Tag 30.02"; the `02`
it meant is a sub-tag of 62.50, and that comment has been corrected.

### P2-3 Scale-out path

- [x] Wire `workers/redis.py` behind a flag; move dedup to Redis `SETNX`; run
      workers as a separate process.

**Why.** Workers were `InProcessTransport` inside the API process. One process
means no horizontal scaling, and two replicas would double-run heartbeats
because the dedup cache is per-process.

**Tests.** Two concurrent workers must not double-expire or double-send.

**What shipped.** `RedisTransport` is implemented rather than sketched — it was a
skeleton whose `__init__` raised `NotImplementedError`, and `redis` was not a
dependency at all, so compose had been passing a `REDIS_URL` that nothing read.

* **Streams with a consumer group**, per `chmabapay:queue:{q}`, consumer `cg-{q}`.
  A group hands each entry to exactly one consumer, and the entry stays in the
  group's pending list until acknowledged — so a worker that dies mid-job leaves its
  work recoverable via `XAUTOCLAIM` rather than lost. A list would drop it silently,
  and for this queue that is a payment that was taken and never detected.
* **Dedup via `SET NX EX`**, which is the part that actually fixes the stated bug: a
  per-process table suppresses nothing once two processes share a queue, and both
  would have done the same work.
* **Delayed retries in a sorted set**, promoted back onto the stream when due.
  Promotion removes from the set before adding to the stream, so two workers polling
  at once cannot both promote the same retry.
* **Two switches, not one.** `WORKER_TRANSPORT` (which queue) and `WORKERS_ENABLED`
  (whether this process drains) are independent, because a process can need to
  enqueue without consuming: in the split topology the API must reach the shared
  queue while the worker drains it.
* **The alert watcher starts where the drains are**, not beside the API. It reads
  monotonic heartbeats, so in a process that never drains every queue would look like
  it had never started and it would page continuously.
* **`python -m chmabapay.worker_main`** is the drain process, and compose exposes it
  as a `worker` service behind the `workers` profile.

**Delivery is at-least-once, and that is now written down in the transport.** A
consumer group solves concurrent duplication; it does not solve a slow worker whose
entry is claimed after `QUEUE_CLAIM_IDLE_SECONDS` and then runs alongside its rescuer.
`Worker.process` therefore has to be safe to run twice. The alternative — reclaiming
never — trades a rare duplicate for permanently stuck jobs, which is worse for
payments.

**The main risk was designed out, not just documented.** Turning workers off while
keeping the in-process transport leaves a private queue that no other process can even
see, so every job is dropped in a swallowed exception and the platform looks healthy
while detecting nothing. That is one forgotten variable away, so
`assert_a_queue_will_be_drained` refuses to start — verified by running it: exit 3,
`Application startup failed`.

**Cold start of the split topology, executed.** Eight containers, `api` and `worker`
both healthy; Redis holding the five streams, their counters, a heartbeat stamp per
queue and the dedup keys the worker's own scheduler created; the API logging
`workers are disabled in this process`; and `/metrics` scraped from the API reporting
queue depth **and** a live heartbeat age for all five queues, read from the shared
Redis.

**Two things this surfaced that were not in the plan.**

1. **This machine already served 6379 with a Redis 3.0.504 for Windows.** The compose
   mapping was accepted anyway, so `docker compose port redis 6379` reported
   `0.0.0.0:6379` and the service was healthy while the host asking for
   `localhost:6379` got the 3.0.504 — no streams at all (`XADD` is Redis 5), no
   `HELLO` (Redis 6). The tests failed with `unknown command 'HELLO'`, which names
   nothing. Redis now publishes on **56379**, for the same reason Postgres is on
   55432.
2. **`/metrics` lost the stall signal in the new topology.** It reported no heartbeat
   age at all, because the non-draining API has no local timestamps to compare. That
   is the metric P1-3 exists for, so the transport now also publishes a throttled
   wall-clock stamp per queue and `worker_age_seconds()` exposes it; the metrics
   reader prefers the shared ages and falls back to local heartbeats. Verified live:
   all five queues report an age under a second when scraped from the API.

**Residual, stated rather than hidden.** Two draining processes each run their own
alert watcher, so a condition still pages but is not deduplicated across them. And
`QUEUE_CLAIM_IDLE_SECONDS` is a judgement: set it below the slowest legitimate job and
that job will be run twice.

**Exit criteria.** Two concurrent workers must not double-expire or double-send. ✅
`tests/test_queue_redis.py` drives two consumers against one server: neither receives
the same entry, work is divided when they poll concurrently, a duplicate `dedup_key`
from a *second* transport is suppressed, and an unacknowledged entry is recovered by
another worker. All of it runs against `fakeredis` locally and a real `redis:7` in CI.
The duplicate-heartbeat case is covered there too: two processes posting the same
sweep key collapse to one job, so the sweep fires once.

---

## Verification protocol

Three layers, deliberately different. All three run at the end; none replaces
another.

| Layer | What it proves | What it cannot prove |
|---|---|---|
| **pytest + CI** (P0-4) | Logic and contracts, per commit, offline | Behaviour against live ABA; cross-page UI |
| **`/_dev/integration-test`** | Full API + security sweep against a running instance | The live money path; the dashboards |
| **`/_dev/live-pay` + browser** | A real ABA-issued QR, a real settlement, real rendering | Nothing else — this is the narrow, decisive one |

### Layer 2 — API and security sweep

`http://localhost:8010/_dev/integration-test`

The catalog is `chmabapay/tools/testplan.py`: **1272 cases across 19 groups**
(setup, smoke, auth 154, headers 76, query 156, encoding 45, amount 111,
khqr 77, live-pay 19, payments 133, concurrency 4, transactions 98, reports 211,
webhooks 92, stores 40, keys 12, billing 19, admin 4, checkout 4), with 232
marked critical and 8 destructive. Also exposed machine-readably at
`/_dev/integration-test/manifest`, so the same catalog can run from a headless
httpx script or CI.

Prerequisites: the runner provisions fixtures in its `setup` group, but the plan
expects a store and an API key, and `payway_link` defaults to
`https://link.payway.com.kh/ABAPAYpe518710Y`. 110 cases touch the public internet
(the ABA SSR page); 213 mutate state.

**Run it with `RATE_LIMIT_ENABLED=false`.** The sweep is a load generator —
1272 cases, hundreds of them in a minute — so the P1-1 limiter would start
refusing it with `429` partway through. That is the limiter working, not a
regression, and it is why the sweeper states the requirement in its own footer.
The consequence is worth saying plainly: the sweep verifies the API with limits
off, so rate limiting is proved by `tests/test_ratelimit.py` in the offline gate
instead, not here.

**Read the caveat before trusting a green run.** The runner pins
`hosted_qr: false` on its payment cases, deliberately: it asserts properties of
*our* encoder (CRC-16, Tag 30.02 destination, the 30-day Tag 99 window). So a
passing sweep proves our offline payload and the API contract — it does **not**
prove an ABA-issued QR settles. The hosted path has its own `live-pay` group,
and neither substitutes for Layer 3.

### Layer 3 — Money path

1. Restart the backend so the running process matches the working tree.
2. `/_dev/live-pay` with $0.01 → scan with a real wallet → pay.
3. Confirm the row settles, `bakong_ref` populates, the signed webhook arrives,
   and the dashboard shows paid.
4. Then the UI regression: dashboard QR renders, expired payment shows no QR and
   offers "Generate new QR", and the successor carries its lineage note.

### Hard rule

`ENABLE_DEV_GATEWAY` mounts `/_dev/*`. The fake rail **and** the test runner are
the same flag. It must be `false` everywhere in production and staging, or
`/_dev/payments/{id}/pay` can mark payments paid without money moving.

---

## Environment facts carried forward

- **Schema changes go through Alembic, and only through Alembic.** `alembic
  upgrade head` to apply, `alembic revision --autogenerate -m "..."` to add,
  `alembic check` to prove the models and the DB agree. The app never migrates:
  it verifies at startup and refuses to boot when the database is behind head or
  is not Alembic-managed at all. Set `SCHEMA_CHECK=false` only where the schema
  is built directly from the models (the test suite does this).
- Any database created before Alembic needs adoption once: `alembic stamp 0001`
  then `alembic upgrade head`. Verify the schema matches the models first — a
  blind stamp hides real drift. Revision `0002` exists precisely because the
  local Postgres had drift.
- The backend on **:8010** is running code that predates today's reissue
  endpoint (its OpenAPI has `/pay/{id}/qr.svg` but not
  `/v1/payments/{id}/reissue`). Restart before any verification run.
- Migration 9 (`payments.reissued_from_id`) has been applied to the local
  Postgres (`localhost:5432/chmabapay`).
- Revision `0003` renames `audit_logs.admin_account_id` to `actor_account_id`, and
  `0004` adds the Terms-acceptance columns. The local **app** database
  (`localhost:5432/chmabapay`) was at `0002` and the backend refused to boot until
  `alembic upgrade head` was run — deliberately, with the command in the error.
  It is at `0004 (head)` now. Apply and **restart** in the same step: a process
  still running the old code writes the old column name, so a live edit would break
  its plan-editing path. Both revisions are additive/metadata-only and reversible.
- **Watch the terminal environment, not just `.env`.** Shells in this workspace are
  stateful, and a `$env:DATABASE_URL` left over from an earlier command silently
  overrides `.env`. This bit twice in one session: `alembic current` reported
  `0004 (head)` while it was reading the *test* database, so the app database
  looked upgraded when it was not; and `uvicorn` was pointed at the test database
  without any indication. Set `DATABASE_URL` explicitly in the command when the
  answer matters, and read the URL back rather than assuming.
- `segno` is required for QR rendering and was missing from the local Python
  3.11 environment; it is now installed.
- `/metrics` is served **unauthenticated** unless `METRICS_TOKEN` is set, and the
  app logs a warning at startup when it is not. Either set the token or restrict
  the path at the proxy — the exposition discloses platform-wide volumes.
- **Operator alerts need two variables, and silently do nothing without them.**
  `OPS_TELEGRAM_CHAT_ID` is unset in this environment, so the alert watcher logs
  `ALERT NOT SENT` at the moment a condition fires rather than paging anyone. The
  delivery path is proven; the coverage is not, until a chat id is set.
- **SQLite DDL is pathologically slow on this machine** — a single `CREATE TABLE`
  costs 0.2–1.4s, so `_fresh_db` costs ~6.5s *per test* and the full suite takes
  ~11 minutes on SQLite versus ~90 seconds on Postgres. A SQLite run that looks
  hung is usually just working; check that `chmabapay_test.db` is still being
  written before killing it. CI runs Postgres, which is also where production
  runs.
- Tests run on SQLite while production is Postgres. This is a known source of
  backend-specific bugs (SQLite returns naive datetimes for tz-aware columns)
  and is addressed in P0-4.
- Revision `0004` adds `accounts.terms_accepted_at` / `terms_accepted_version`.
  Run `alembic upgrade head` and restart together, same rule as `0003`: a process
  running the old code cannot see the new columns.
- **`TERMS_VERSION` and the text of `/terms` are a pair.** The recorded acceptance
  stores the version the merchant was shown, so an edit to the agreement without
  bumping `TERMS_VERSION` silently attributes agreement to the new text to
  merchants who never saw it. Change both, or neither.
- **Session-authenticated tests must use `base_url="http://localhost"`.**
  `_set_session_cookie` marks the cookie `Secure` for any non-localhost host, and a
  Secure cookie is not sent back over plain http — so a made-up host makes a
  successful sign-in look like a 401. The shared `client` fixture now uses
  `localhost`, matching `test_admin_plans.py` and `test_audit.py`.
- **`payments.gateway_status_raw` is a `JSON` column with `none_as_null=True`.**
  Without it, Python `None` is stored as the JSON literal `null`, which still
  satisfies `IS NOT NULL` — so a predicate-driven purge reports the same rows as
  purged on every run while appearing to work. If you add another JSON column that
  a sweep is expected to clear, it needs the same setting or an explicit `null()`.

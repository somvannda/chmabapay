# @chmabapay/admin

ChmabaPay platform console. A standalone Next.js 14 App Router app — it is not
part of the landing site, and it shares no runtime code with it.

```bash
# From the repository root
pnpm install
pnpm dev:admin      # http://localhost:3002
```

Ports: landing `3001`, admin `3002`.

## Access

Sign-in is password-only. `/login` posts an email + password to `POST /auth/login`
and the console refuses any other session: Google OAuth is not proxied by this app,
and `GET /v1/me` reports `auth_method` so a Google session is rejected even if it
belongs to an admin address. That rule is enforced **server-side** too —
`get_hybrid_admin_context` refuses any session whose `amr` is not `password` — so a
Google session cannot reach `/v1/admin/*` with curl either.

### First admin on a fresh deployment

`grant-admin` creates the account, grants `is_platform_admin`, and sets a password in
one step. It needs no OAuth round trip, so it is the path to use before anything else
exists:

```bash
# inside the api container (or locally)
uv run python -m chmabapay.cli grant-admin duke@chmaba.com --password '<a real password>'
```

`CHMABAPAY_PASSWORD` is read as a fallback for the password, which is what you want on
a server where a flag would land in the shell history and in `ps`:

```bash
CHMABAPAY_PASSWORD='...' uv run python -m chmabapay.cli grant-admin duke@chmaba.com
```

To change a password later, use `set-password`. To promote an account that already
exists (for example one created by a Google sign-in), `grant-admin` promotes it in
place and leaves the name and existing password alone unless you pass one.

Do not rely on the older two-step route: `CHMABAPAY_ADMIN_EMAILS` only promotes an
account on a Google sign-in, and the console then refuses that Google session — so it
leaves you needing SSH plus this CLI anyway. `CHMABAPAY_ADMIN_PASSWORD` only supplies a
password on an *existing* admin account's first password login; it does not create the
account.

Every other page calls `GET /v1/me` on mount; signed-out visitors are redirected to
`/login`, and signed-in accounts without `is_platform_admin` get a "Platform admin
only" gate. `grant-admin` sets `is_platform_admin` directly; listing an address in
`CHMABAPAY_ADMIN_EMAILS` also grants it, on that account's first Google sign-in.

## Pages

| Route | What it does |
|-------|--------------|
| `/login` | Email + password sign-in. The only way into the console |
| `/` | Platform totals: accounts, stores (total + active), payments paid (all-time + this month), merchant volume paid today, platform revenue (today + this month), MRR. Below the stats, a pointer to **Settings**, because the one control that switches billing on is not a statistic |
| `/settings` | Platform configuration. **Plan fee collection** — the ABA PayWay link ChmabaPay's own subscription invoices are raised against. Saving it creates the platform's own store if none exists and marks that store internal, so it is never metered as a merchant tenant |
| `/accounts` | Every account with plan, subscription status, store/payment counts. One search box, four answers: email, name, a store's public id, or a key prefix (a whole key pasted from a merchant's message is sliced to its first twelve characters, which is the part the platform stores). 25 per page |
| `/accounts/[account_id]` | One account: plan & usage, profile, stores, keys, last 24 invoices. The Profile panel's Whitelabel row toggles the white-label entitlement, and its Status row applies a standing: **Suspend** (signs the merchant out everywhere), **Freeze** (`restricted` — still readable, every write refused) or **Activate**, each behind a confirmation that echoes the account id and email, and each requiring a reason except Activate. The Stores table's **Mark as platform** button flips `is_internal`. The API-keys panel mints (**Mint a key**) and **Rotate**s on a merchant's behalf — both needed because `/v1/keys` is session-only, so a merchant who cannot sign in cannot get a credential any other way. The raw key is shown once in a copy-and-close dialog and never again; the plan's key quota is deliberately not applied to an operator mint. Stores and keys are capped at 50 rows each, with a "showing N of M" note when the cap bites. The Payments count links to `/payments?account_id=` |
| `/payments/[public_id]` | One payment: status, amounts, the ABA session handle, its webhook deliveries, and a **Resolve** panel — re-reconcile, mark paid by hand, **record a refund**, re-deliver webhooks. Mark paid and Record refund both demand a reason, and both echo the payment id and amount before the confirm click |
| `/payments` | Every payment on the platform, newest first, with the account and store that took it. Search by payment id or the merchant's reference, filter by status or account. The "Settled" column carries the reconciliation story — paid, refunded, or the point at which we stopped watching |
| `/deliveries` | Every webhook delivery attempt across all accounts: endpoint, status, tries, last HTTP response, transport error and next attempt. A `retrying` row whose next attempt is in the past is marked overdue, which is what a stalled sender looks like. Per-row **Retry** resets the attempt budget and the clock; for a delivery that already succeeded, the modal's opt-in is what sends `include_successes=true` — without it the API leaves the row untouched, because re-sending an event the merchant already processed can double-process the sale |
| `/plans` | Full plan CRUD — create, edit and delete, including the pricing copy (tagline + feature bullets) the website and user portal render. Deleting a plan that subscriptions reference retires it instead |
| `/invoices` | Plan invoices across every account. Filter by `YYYY-MM` period and status |
| `/audit` | The platform's write trail, newest first, with the actor's email. Filter by action, target type, actor or a `from`/`to` date range, and **Export CSV** / **Export JSON** the filtered view. The export is capped at 10,000 rows and says so — `X-Total-Rows` against `X-Rows-Returned` — because a partial file that looks whole is worse than a refusal |
| `/health` | What the deployment can say about itself: process, database probe, worker transport, dev gateway, whether the metrics scrape is secured, the expected queues and each one's last drain. `/health` on the API answers "the process is up"; this answers "is it working". No connection strings — only whether the scrape needs a token |

## Backend contract

All calls go to `/v1/admin/*` and require an **admin session cookie** carrying
`amr: password`. A `Bearer ck_` key is not a credential here at all: `get_hybrid_admin_context`
has no key branch to reach, so a request carrying one and a password-less session cookie is
refused by the claim check (`403 password_session_required`), and one carrying a key and no
cookie is refused by `get_current_session_account` (`401 invalid_session`).

Since the console now sets the platform's own collection link, `PUT /v1/admin/hq-store/link`
is a route that decides where money lands. It is gated by the same
`get_hybrid_admin_context` as every other admin route — which also means it inherits the
password-session requirement, so a Google session belonging to an admin cannot reach it.
Both it and `GET /v1/admin/hq-store` are gated the same way. The write is audit-logged
(`hq_store.created` when the store is first created, `hq_store.link_set` on every save);
the read is not, because reading where the money goes is not a privileged action.

Setting that link also marks the store **internal**. An internal store is the platform's
own — it is exempt from the monthly quota, writes no usage-ledger row, and its takings
are reported as *platform revenue* on `/` rather than folded into merchant volume.
`PUT /v1/admin/stores/{public_id}/internal` flips that flag on any store by hand
(`{is_internal, reason?}`), with the same `get_hybrid_admin_context` gate, a 404
`store_not_found`, one `store.internal_changed` audit row per change, and no row on a
repeat that changes nothing. It is reached from the account page's Stores table
(**Mark as platform** / **Unmark platform**), which also carries `is_internal` on each
store row, labelled "platform".

That key branch is now **deleted**, not merely unreachable (decision D-6, 2026-09-23). It sat
*above* the claim check, so a platform-admin key plus any session cookie — an SSO one included
— reached every admin route with no password claim, which is the exact guarantee the console
advertises. Reaching it would also have meant any API key belonging to a platform-admin
account acted as a platform admin: a far wider credential than the merchant-scoped key that
was minted. If machine access to this API is ever needed, the answer is a key scope on the
session gate, not a key that skips it.

The dev server proxies them to `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`)
via the rewrites in `next.config.js`.

### The operator's day-one tools

Four routes exist because the console's own gaps were the thing that would have needed a
database session on the first bad day:

- `POST /v1/admin/accounts/{account_id}/keys` and `POST /v1/admin/keys/{key_id}/rotate` share
  `routers/keys.py`'s `mint_key` / `rotate_key_instance` with the merchant routes, so the
  hash-at-rest rule, the account-wide scope, the display prefix and the audit row cannot drift
  between the two callers. The operator's row carries `account_id` and `via: admin_console` on
  top of the merchant's, because "who minted this, and for whom" is the first question asked
  when a key leaks. A rotation leaves the superseded key `suspended` (a revocation leaves it
  `revoked`), which is what tells the two apart in the table; both stop working immediately.
- `GET /v1/admin/health` — the database probe, the worker transport, the expected queues and
  each one's last drain, with no connection strings.
- `GET /v1/admin/audit-logs/export?format=csv|json` — the *same* filters as the list route
  (built by one `_audit_filters`, day boundaries included), capped at 10,000 rows. It answers
  `X-Total-Rows` and `X-Rows-Returned` so a truncated export announces itself instead of
  looking complete.
- `GET /v1/admin/accounts?q=` widened from email-and-name to store public id and key prefix.
  Support is handed a store id or a broken key, not an address; the key comparison slices the
  query to twelve characters and matches it against `key_prefix`, so a pasted key resolves
  without the platform ever reading — or storing — the raw value or its hash.

## Styling

`app/globals.css` is the app's own copy of the shared `cp-*` / `dash-*` design
system; `tailwind.config.ts` reads brand tokens from `../shared/theme.ts`. No
inline styles — every page uses the global class families.

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
| `/` | Platform totals: accounts, stores (total + active), payments paid (all-time + this month), MRR. Below the stats, **Plan fee collection** — where ChmabaPay's own subscription fees are received, with the ABA PayWay link editable in place |
| `/accounts` | Every account with plan, subscription status, store/payment counts. Search by email or name; 25 per page |
| `/accounts/[account_id]` | One account: plan & usage, profile, stores, last 24 invoices. The Profile panel's Whitelabel row toggles the white-label checkout entitlement. The Payments count links to `/payments?account_id=` |
| `/payments` | Every payment on the platform, newest first, with the account and store that took it. Search by payment id or the merchant's reference, filter by status or account. The "Settled" column carries the reconciliation story — paid, refunded, or the point at which we stopped watching |
| `/deliveries` | Every webhook delivery attempt across all accounts: endpoint, status, tries, last HTTP response, transport error and next attempt. A `retrying` row whose next attempt is in the past is marked overdue, which is what a stalled sender looks like |
| `/plans` | Full plan CRUD — create, edit and delete, including the pricing copy (tagline + feature bullets) the website and user portal render. Deleting a plan that subscriptions reference retires it instead |
| `/invoices` | Plan invoices across every account. Filter by `YYYY-MM` period and status |

## Backend contract

All calls go to `/v1/admin/*` and require an **admin session cookie**. A `Bearer ck_`
platform-admin key is *not* accepted, despite the `Bearer ck_` branch inside
`get_hybrid_admin_context`: that dependency takes `get_current_session_account` as a
sub-dependency, and it raises 401 when no cookie is present, so FastAPI aborts before the
key branch is ever reached. `/v1/admin/*` answers `{"detail":"invalid_session"}` to a
cookie-less request no matter which key it carries.

Since the console now sets the platform's own collection link, `PUT /v1/admin/hq-store/link`
is a route that decides where money lands. It is gated by the same
`get_hybrid_admin_context` as every other admin route — which also means it inherits the
password-session requirement, so a Google session belonging to an admin cannot reach it.
Both it and `GET /v1/admin/hq-store` are gated the same way. The write is audit-logged
(`hq_store.created` when the store is first created, `hq_store.link_set` on every save);
the read is not, because reading where the money goes is not a privileged action.

Leaving the branch unreachable is deliberate, and pending a decision (P1-2 in
`docs/production-readiness.md`): reaching it would let any API key belonging to a
platform-admin account act as a platform admin, which is a much wider credential than the
merchant-scoped key it was minted as. If machine access to this API is ever needed, the
answer is a key scope, not simply deleting the sub-dependency.

The dev server proxies them to `NEXT_PUBLIC_API_URL` (default `http://127.0.0.1:8000`)
via the rewrites in `next.config.js`.

## Styling

`app/globals.css` is the app's own copy of the shared `cp-*` / `dash-*` design
system; `tailwind.config.ts` reads brand tokens from `../shared/theme.ts`. No
inline styles — every page uses the global class families.

# @chmabapay/admin

ChmabaPay platform console. A standalone Next.js 14 App Router app — it is not
part of the landing site, and it shares no runtime code with it.

```bash
# From the repository root
pnpm install
pnpm dev:admin      # http://localhost:3002
```

Ports: landing `3001`, user portal `3000`, admin `3002`.

## Access

Sign-in is password-only. `/login` posts an email + password to `POST /auth/login`
and the console refuses any other session: Google OAuth is not proxied by this app,
and `GET /v1/me` reports `auth_method` so a Google session is rejected even if it
belongs to an admin address. Set a password with:

```bash
uv run python -m chmabapay.cli set-password duke@chmaba.com
```

Every other page calls `GET /v1/me` on mount; signed-out visitors are redirected to
`/login`, and signed-in accounts without `is_platform_admin` get a "Platform admin
only" gate. `is_platform_admin` is granted by listing the address in
`CHMABAPAY_ADMIN_EMAILS`.

## Pages

| Route | What it does |
|-------|--------------|
| `/login` | Email + password sign-in. The only way into the console |
| `/` | Platform totals: accounts, stores (total + active), payments paid (all-time + this month), MRR |
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

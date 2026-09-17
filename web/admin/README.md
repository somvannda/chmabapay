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
| `/accounts/[account_id]` | One account: plan & usage, profile, stores, last 24 invoices. The Profile panel's Whitelabel row toggles the white-label checkout entitlement |
| `/plans` | Full plan CRUD — create, edit and delete, including the pricing copy (tagline + feature bullets) the website and user portal render. Deleting a plan that subscriptions reference retires it instead |
| `/invoices` | Plan invoices across every account. Filter by `YYYY-MM` period and status |

## Backend contract

All calls go to `/v1/admin/*` and require an admin session cookie or a `Bearer ck_`
platform-admin key. The dev server proxies them to `NEXT_PUBLIC_API_URL`
(default `http://127.0.0.1:8000`) via the rewrites in `next.config.js`.

## Styling

`app/globals.css` is the app's own copy of the shared `cp-*` / `dash-*` design
system; `tailwind.config.ts` reads brand tokens from `../shared/theme.ts`. No
inline styles — every page uses the global class families.

# @chmabapay/user — ChmabaPay User Portal (M1)

Next.js 14 App Router portal for Individual (Sokha) and Business (KhmerPOS) merchant accounts.

## Stack
- Next.js 14 App Router with Server Components + RSC fetch pattern for `/v1/me`
- TypeScript strict, Tailwind with brand tokens
- Shared components + theme imported from `../shared` through the `@shared/*` path alias

## Fonts
Noto Sans Khmer preloaded first (per L1 Khmer-first), then Inter, then JetBrains Mono (code/API keys).
Loaded via `next/font/google` with preconnect in `<head>`.

## Pages (App Router)
1. `/login` — Google OAuth via `/api/auth/google/login` (rewrites). Dev mode: `/api/auth/_dev/login?email=sokha@example.com`.
2. `/dashboard` — Summary stats + Create Store CTA.
3. `/stores` + `/stores/new` — 3-step wizard (business info, ABA/Bakong destination, Done) with L4 explicit step progress bars.
4. `/payments` + `/payments/[id]` — Payments DataTable + detail with attempt history, KHQR card, L5 bank chips.
5. `/keys` — API keys list. Individual = no account-scoped keys (L6). Business = full.
6. `/settings/{profile,billing}` — 2 tabs: profile (me PATCH), billing (4-plan matrix, change-plan auto flips account_type=business).

## L6 Dark Mode Toggle
DOM node rendered ONLY for `account_type === "business" || is_platform_admin`.
For Individual accounts: `shouldRenderDarkToggle() = false` -> toggle is not in DOM tree
(not merely `display: none`).

## Backend Proxy
`next.config.js` rewrites `/api/:path*` -> `NEXT_PUBLIC_API_URL` (default `http://localhost:8000`).

## Run
```
pnpm install
pnpm dev:user   # http://localhost:3000
```

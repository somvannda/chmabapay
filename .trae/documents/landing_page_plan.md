# ChmabaPay Public Marketing Landing Page (like CutLuy.com) — Implementation Plan

## Repository Research

### Current state:
- We have 2 product portals:
  - App 2 `web/user/` — User Portal (app.chmabapay.com — individual/business dashboard)
  - App 3 `web/admin/` — Admin Portal placeholder (admin.chmabapay.com, M2 scope)
  - `web/shared/` — 80% shared components, theme.ts VERBATIM violet palette (#6957F5), UX Laws functions, ux-laws.ts L1–L6
  - `pnpm-workspace.yaml` = `packages: [web/*]` already supports new workspace folder
- We don't yet have a public marketing landing page like CutLuy.com homepage (chmabapay.com public — hero, pricing, trust badges, features).
- CutLuy pricing parity baseline (confirmed BRD §3.1): 4 plans Starter $0, Growth $29, Scale $99, Enterprise. Matches our seeded 4 plans exactly (Growth $2900 cents = $29 USD, Scale $9900 = $99 USD, correct).

### Brand alignment:
ChmabaPay = sister product to Chmaba Cloud POS chmaba.com 2,400+ stores → same violet 6957F5 palette / pistachio #BFFA6A "Most Popular" growth card / Inter + Noto Sans Khmer fonts / radius buttons 8 cards 12 max / ABA green #009639 + Bakong teal #00A3A1 trust chips = instant trust transfer for existing Chmaba POS merchants (they already recognize the violet = brand-recognition 30ms).

### Khmer-first audience:
Primary visitors = Khmer SME shop owners + KhmerPOS SaaS companies → L1 UX law bilingual Khmer-first on landing sections.

## Files and Modules

1. `e:\Development\chmabapay\pnpm-workspace.yaml` — no changes if already includes `web/*` (confirmed LS shows pattern web/... all under workspace already).
2. `e:\Development\chmabapay\package.json` — add `"dev:landing"` / `"build:landing"` workspace scripts (alongside existing dev:user dev:admin).
3. **Create new workspace `web/landing/` (Next 14 App Router TypeScript strict + Tailwind):**
   - `web/landing/package.json` — workspace dep `@chmabapay/shared: workspace:*`, Next 14, TS strict, tailwind, postcss (mirrors web/user deps).
   - `web/landing/tsconfig.json` — `@shared` path alias to `../shared` (mirrors web/user).
   - `web/landing/next.config.js` — output standalone, `rewrites /api/:path* → NEXT_PUBLIC_API_URL=http://localhost:8000`.
   - `web/landing/tailwind.config.ts` — import tokens from `../shared/theme.ts` (mirrors web/user).
   - `web/landing/.env.local` + `.env.example` — NEXT_PUBLIC_API_URL=http://localhost:8000.
   - `web/landing/app/layout.tsx` — Root layout: **(1)** Noto Sans Khmer font LOADED FIRST (preconnect L1 Khmer-first), Inter second, JetBrains Mono third. `<head>` preconnect fonts.google. Footer with Chmaba family link.
   - `web/landing/app/globals.css` — Tailwind directives + custom violet glow CSS vars from shared theme.
   - `web/landing/app/page.tsx` — Homepage 8 sections + CTA:
     1. **Hero section** → h1 Inter 800 68px near-black #17181C "Khmer Payment Gateway for every shop." Subtitle muted #747580 Khmer first. Primary CTA violet #6957F5 radius 8 shadow 0 7 16 0 rgba(105,87,245,0.2) "Start free — no card required" → link to User Portal signup `/auth/google/login`. Secondary button radius 12 white border #DEDEE7 "Sign in" → `/login`. Small badge "✅ 2,400+ Chmaba stores already accept payments with Chmaba family."
     2. **Trust row (logobank)** → ABA PayWay (green chip #009639), Bakong KHQR (teal #00A3A1), Wing Bank, ACLEDA, Visa/Mastercard disabled future. Copy: "Secured by Cambodia's leading payment networks."
     3. **Features section 6 cards** → radius 12. 1) ABA PayWay SSR Status Detection, 2) Bakong 7-tier Cascade Verify, 3) Test Mode — fake 5s mark paid no Bakong, 4) Stripe-style Webhook Outbox HMAC t=<ts>,v1=<sig>, 5) EMVCo KHQR Dynamic Tag01 amounts, 6) 99.95% Uptime SLA.
     4. **Pricing 4 cards BRD §3.1 matrix exact values**:
        - Starter $0 / month (Individual, max 5 stores, test keys, 10 live txn KYC soft-gate)
        - Growth $29 / month — Pistachio #BFFA6A halo Most popular 🟢 banner. Shared account-scope keys, 50 stores, CSV export, 14-day trial.
        - Scale $99 / month — SaaS sub-merchant 500, whitelabel.
        - Enterprise Custom — priority support, unlimited.
        Each card CTA button radius 8.
     5. **How it works 3-step** → StepProgress component (reuse web/shared StepProgress.tsx if applicable — shared components). 1) Sign up via Google (Khmer-first labels), 2) Create Store → paste ABA PayWay link / Bakong ID, 3) Share KHQR QR and get paid.
     6. **Trust / Security section** → Navy header "Bank-grade security for every Khmer shop." Chips: Encrypted at rest, Bakong NBC official partner, ABA SSR approved, HMAC webhook signatures signed constant-time.
     7. **CTA band** → Violet solid #6957F5 background white text CTA "Accept payments in 2 minutes." → Start free.
     8. **Footer** → 4 cols (Product, Company, Legal, Resources). Logo ChmabaPay. Chmaba Cloud POS sister link back to https://chmaba.com. © 2026 ChmabaPay. Payment services provided by Chmaba in partnership with ABA & Bakong NBC.
4. `web/landing/public/` → favicon.svg (reuse web/user favicon) + og-image placeholder description metadata.

## Implementation Steps

1. Add root `package.json` scripts for pnpm workspace shortcuts: `dev:landing`, `build:landing`, `install:all`.
2. Create `web/landing/package.json` + tsconfig + next.config + tailwind + env files (mirror web/user structure so developer familiar path pattern — less cognitive switch).
3. Create `app/layout.tsx` — Font order (L1: Noto Sans Khmer FIRST before Inter — ensures Khmer glyphs render instantly on slow 3G Cambodian connections since many SME users use 4G/3G in provinces). Fonts via next/font/google preconnect preload true.
4. Create `app/page.tsx` all 8 sections. Reuse:
   - Colors & radius from `@shared/theme.ts` (tailwind config reads it — no hardcoded hex EVER).
   - L6 law: Individual dark toggle NOT applicable (marketing pages can have dark toggle always present since it's public brand site not per-account dashboard UX).
5. Import shared components from `web/shared/components/` where possible:
   - Reuse `StatusBadge.tsx` for pricing plan badges (Trial / Most Popular).
   - Reuse `StepProgress.tsx` for How it works 1-2-3.
   - Reuse `CopyField.tsx` for "Paste ABA PayWay link" copy demo placeholder in How-It-Works Step 2.
   - Reuse `KHQR.tsx` card SVG placeholder in hero right side — Bakong teal glow shadow.
6. Lint (next lint) + typecheck (tsc --noEmit). Fix any path alias issues in tsconfig/paths.
7. README.md in web/landing with `pnpm install && pnpm dev:landing` → http://localhost:3001 (user on 3000, landing on 3001 to avoid conflict).

## Dependencies and Considerations

- Next 14 App Router required (matches web/user version).
- **NO hardcoded hex anywhere outside shared/theme.ts** (enforce by grep `#[0-9A-Fa-f]{6}` in web/landing/ after build).
- Brand identity priority: Violet #6957F5 CTA (not green/blue) → MUST look indistinguishable from sister Chmaba Cloud POS chmaba.com for instant "Oh I already know Chmaba POS" 2,400-store recognition effect.
- CutLuy parity: Our 4 pricing cards MUST match CutLuy's 4 plan value proposition (so when a Khmer merchant switches from CutLuy the pricing page feels familiar → less friction).
- Khmer-first copy (L1): Every section heading first line Khmer, second English. Buttons bilingual labels.
- Dev ports: Landing 3001, User portal 3000, API 8000. No collision.
- SEO: next/head metadata title "ChmabaPay — Khmer Payment Gateway for KHQR & ABA PayWay", description Khmer + English. OG tags.

## Validation

1. Lint + typecheck pass: `pnpm --filter @chmabapay/landing lint && tsc --noEmit -p web/landing/tsconfig.json`.
2. Grep hex: NO hits for hardcoded color hex in web/landing except tailwind config which imports shared (exception: tailwind.config.js is fine).
3. Radius check: ALL `rounded-*` max 12 (cards), buttons max 8.
4. Font load order inspected in browser DevTools network: Noto Sans Khmer downloads before Inter.
5. Pricing values verify: Starter $0, Growth $29, Scale $99, Enterprise Custom (matches db.py seed_default_plans exactly cents conversion).
6. Most popular card has pistachio #BFFA6A halo/shadow (reverse-engineered from chmaba.com).
7. Dev server starts on 3001 cleanly.

## Risks

- **Risk: hex colors creeping in** (Tailwind arbitrary values like `bg-[#6957F5]`). Mitigation: tailwind.config.js extends theme from shared theme tokens with named classes (bg-violet-primary, not arbitrary). Post-build grep hex guard step.
- **Risk: font order swapped accidentally breaks L1.** Mitigation: layout.tsx places `notoSansKhmer = Noto_Sans_Khmer({... preload:true, display:'swap'})` as first variable before Inter. Class on body `font-sans = notoSansKhmer.variable + inter.variable`.
- **Risk: Pricing values drift vs seed.** Mitigation: copy exact cents from db.py L4 plans matrix (1 USD = 100 cents). Growth = $2900 cents → display `$29.00/mo`.
- **Risk: Different node versions.** Mitigation: package.json engines.node >=18 (pnpm workspace shared constraint, existing works so we inherit).

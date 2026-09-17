# ChmabaPay Landing Page

Public marketing landing page for ChmabaPay — Khmer-first payment gateway for KHQR Bakong & ABA PayWay.

## Getting Started

```bash
# From repository root
pnpm install
pnpm dev:landing
```

Open [http://localhost:3001](http://localhost:3001) in your browser.

## Scripts

| Command | Description |
|---------|-------------|
| `pnpm dev:landing` | Start dev server on port 3001 |
| `pnpm build:landing` | Production build |
| `pnpm lint:landing` | Run Next.js lint |

## Brand Compliance

All design tokens are imported from `../shared/theme.ts` through the `@shared/*` path alias. No hardcoded hex values in application code.

- Primary CTA violet: from shared tokens (radius 8px, violet drop shadow)
- Pistachio lime: Most Popular plan card halo
- Trust chips: ABA green, Bakong teal, Wing red, ACLEDA blue
- Font load order: Noto Sans Khmer first, Inter second, JetBrains Mono third
- Max radius: buttons 8px, cards 12px

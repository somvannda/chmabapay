# ChmabaPay Design Tokens & Khmer Market UX Laws

> **Source of truth:** Exact color palette, typography, radius, spacing, shadows reverse-engineered from chmaba.com (Chmaba Cloud POS) on 2026-09-10 to keep visual family unity between Chmaba (POS) and ChmabaPay (payment gateway) — so merchants recognize "this is part of the same Chmaba ecosystem".

---

## Table of Contents

1. [Design Philosophy (Same Chmaba Brand, Two Products)](#1-design-philosophy-same-chmaba-brand-two-products)
2. [Color System (Exact Values from chmaba.com Reverse-Engineered)](#2-color-system-exact-values-from-chmbabacom-reverse-engineered)
3. [Typography (Font Load Order)](#3-typography-font-load-order)
4. [Border Radius, Spacing, Shadows](#4-border-radius-spacing-shadows)
5. [Component Presets (Buttons, Cards, Badges, Statuses)](#5-component-presets-buttons-cards-badges-statuses)
6. [Dark Mode (Business Only, Hidden for Individual Users)](#6-dark-mode-business-only-hidden-for-individual-users)
7. [Six Khmer-Market UX Laws (Non-Negotiable)](#7-six-khmer-market-ux-laws-non-negotiable)
8. [Per-Page Personality Matrix (Trust-Shell + Techy-Inners Pattern)](#8-per-page-personality-matrix-trust-shell--techy-inners-pattern)
9. [ChmabaPay-Brand vs. Chmaba-POS Brand — Where We Diverge Intentionally](#9-chmbapay-brand-vs-chmaba-pos-brand--where-we-diverge-intentionally)
10. [theme.ts Code (Copy-Paste Ready for Frontend)](#10-themets-code-copy-paste-ready-for-frontend)

---

## 1. Design Philosophy (Same Chmaba Brand, Two Products)

```
Chmaba (POS — chmaba.com, today)         ChmabaPay (Gateway, our new product)
───────────────────────────────────        ───────────────────────────────────────────
Product: Record sales / inventory         Product: Accept Bakong KHQR payments,
          / teams dashboard                         webhooks, Sub-Merchant SaaS

Visual DNA → SHARED between BOTH:               INHERIT same visual DNA  ←  (this doc)
 - Purple violet accent #6957F5                  - Same Chmaba violet #6957F5 (primary CTAs)
 - Pistachio lime green #BFFA6A                   - Same lime green (most-popular plan cards,
   (most-popular, tab accents)                                "approved / verified" badges)
 - Inter + Noto Sans Khmer fonts                  - Same fonts
 - Rounded-8px buttons, card 12px                  - Same radii
 - Light: Near-white off-background               - Same background

Where we DIVERGE intentionally:
 - ChmabaPay ADDS trust colors (Charter)
   + ABA Bank green (#009639), Bakong Teal (#00A3A1)
   Why? Payment gateway = money = trust.
   Merchants recognize ABA green from their ABA app → see it → trust.

 - ChmabaPay REMOVES POS-only accents
   (calendar orange, coffee brown)
   Why? Not relevant to accepting payments.

 - ChmabaPay = MORE CAUTIOUS on Individual pages
   (no dark mode toggle for Sokha the vendor — avoids confusion)
```

---

## 2. Color System (Exact Values from chmaba.com Reverse-Engineered)

### 2.1 Brand Colors (1:1 Copy from chmaba.com)

| Token Name | Hex | RGB Approx | Where It Appears On chmaba.com | Use In ChmabaPay Portal For |
|---|---|---|---|---|
| **brandViolet** (Primary) | `#6957F5` | (105, 87, 245) | "Start free", "Create workspace" CTAs; h1 highlight word "in a better **flow.**" | Primary CTA buttons, h1 accent words, primary progress, links, plan-upgrade CTAs, create-store wizard active step |
| **brandLime** (Secondary Accent) | `#BFFA6A`  (SaaS `#C4F27C` from evaluate, blend → we use brighter) | (191, 250, 106) | "Starter $0.99/mo" most-popular plan card bg; pricing Month tab active bg; dark-mode logo emoji bg | Plan "Most Popular" highlights, Bakong DLT confirmed success icon, dark-mode neon code accents |
| **brandInk** (Dark Headings) | `#17181C` | (23, 24, 28) | h1 "Your store, in a better flow."; logo text; h2 "Less admin." | Headings, strong text, logo wordmark, data-table headers, status "PAID" in big numerical display, CTA button icons |
| **textBody** (Paragraph text) | `#747580` | (116, 117, 128) | "Chmaba brings sales, stock, and your whole team..." paragraph | Description copy, placeholder text, secondary info, muted column values |
| **subtleText** (Chips, nav links) | `#60616C` (sign-in links) → extend to `#9A9AA4` (dark nav) | Light: #60616C, Dark: #9A9AA4 | "Sign in" text link; dark-mode Product/How nav links | Unemphasized nav links, secondary labels, timestamp column values |
| **whitePure** | `#FFFFFF` | (255, 255, 255) | "See live demo" button bg, card inner content surfaces | Button backgrounds (ghost), dialogs, input fields |
| **surfaceLight** (Body Background) | `#FAFBFC` (near-white, screenshot color) | Slight off-white | Landing main page subtle gradient | Body / app background (default) |
| **surfaceCard** (Card Background) | `#FFFFFF` (pure white 95% + `#1E1E24` dark) | Pure white | Pricing cards (Free / Pro) | All dashboard DataTable card containers, summary card, KHQR card |

### 2.2 Trust Colors (ChmabaPay ADDITIONS, Intentionally Not On Chmaba POS Homepage — Added Because Gateway = Banking Trust)

| Token Name | Hex | Source | Use For |
|---|---|---|---|
| **trustAbaGreen** | `#009639` | Official ABA Bank brand color (Khmer merchants see this 10×/day in their ABA mobile app) | ABA PayWay link destination chip, hosted checkout `/pay/{id}` header stripe (green header so customer thinks "ABA app safe = this page safe too") |
| **trustBakongTeal** | `#00A3A1` | Official Bakong NBC brand color (teal) | Bakong ID badge, Tag 30/62 KHQR destination label, "Settlement on Bakong DLT" verified line item, Sub-Merchant whitelabel watermark |
| **trustNavy** (Headers) | `#0F2747` | Standard fintech navy | Legal footer privacy |
| **goldVerified** | `#C9A227` | Gold Khmer premium | Enterprise plan chip, Platinum merchant tier |

### 2.3 Status Colors (1:1 Payment Gateway Industry Standard)

| Token Name | Hex | Use For |
|---|---|---|
| **statusPaid** | `#10B981` | Payment status badge PAID, green timeline checkmark, `mark_paid` event |
| **statusPending** | `#F59E0B` (Amber) | PENDING badge, countdown timer on `/pay/{id}` not yet paid |
| **statusScanned** | `#3B82F6` (Blue) | SCANNED badge (customer scanned QR but not yet settled — rare, Stage 2 of 3) |
| **statusFailed** | `#EF4444` | FAILED badge, error banner on payment, webhook 5xx |
| **statusExpired** | `#6B7280` | EXPIRED badge (QR past TTL), grayed out row on Payments list |

### 2.4 Bank Brand Swatches (Destination Chooser Chips)

Merchants recognize these instantly — "Wing = red" is faster than reading "Wing Bank" text. Matches khqr.py BANK_PROFILES registry codes.

| Bank Code | Bank Name | Hex |
|---|---|---|
| `ABA` | ABA Bank | `#009639` |
| `ACLB` / `ACLEDA` | ACLEDA Bank | `#D7282F` |
| `CADI` / `CANADIA` | Canadia Bank | `#0067B1` |
| `WING` | Wing Bank | `#E31937` |
| `SAPA` / `SATHAPANA` | Sathapana | `#F68B1F` |
| `PHIL` / `PHILLIP` | Phillip Bank | `#00AA9B` |
| `CMB` / `CHIPMONG` | Chip Mong Bank | `#B51E24` |
| `PRINCE` | Prince Bank | `#1F3A93` |
| `BAKONG` | Generic Bakong ID | `#00A3A1` (Bakong teal) |

---

## 3. Typography (Font Load Order)

### 3.1 Font Stack (Exact from chmaba.com evaluate: `Inter, "Noto Sans Khmer Web", system-ui`)

```typescript
// Order matters — Noto Sans Khmer SECOND so Khmer glyphs substitute perfectly
// for Khmer script without FOIT (flash of invisible text) for Khmer-first users.
export const fonts = {
  sans: `'Inter', 'Noto Sans Khmer', ui-sans-serif, system-ui, -apple-system,
         BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`,
  mono: `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`,
};
```

Load in `<head>` on EVERY page (both User Portal + Admin Portal):

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=Noto+Sans+Khmer:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
  rel="stylesheet"
/>
```

### 3.2 Typography Scale (From Evaluate + Screenshot)

| Heading Level | Size | Weight | Line Height | Example Usage |
|---|---|---|---|---|
| **Display / Hero h1** | 68px | 800 (ExtraBold) | 1.05 | Marketing hero "Your payments flow, simplified." (purple accent word inside using brandViolet) |
| **Page h1** | 36px | 700 (Bold) | 1.15 | Dashboard, Payments "Payment History", Settings "Account Settings" |
| **Section h2** | 28px | 700 | 1.25 | "Create your first store" wizard header, Plan comparison title |
| **Card h3** | 20px | 600 (Semibold) | 1.35 | Summary 4 cards top row ("This Month Paid", "Success Rate"), KHQR card header, Store detail name |
| **Body base** | 16px | 400 | 28px (1.75) | Paragraph descriptions, table cell body copy |
| **Small / captions** | 14px | 400 | 20px (1.4) | Table secondary info, timestamps, "Last used 2d ago" on keys list |
| **Code / curl snippets** | 13.5px | 500 (JetBrains Mono) | 1.55 | Signature playground code blocks, Webhook HMAC compute code examples, curl API examples |

### 3.3 Khmer-First Layout Rule (INDIVIDUAL STORE / CHECKOUT PAGES ONLY)

On trust-first pages for non-technical Khmer end users:

```
Khmer script on top line BOLD / larger
English translation on 2nd line smaller, muted gray
──────────────────────────────────────────
បង្កើតហាងថ្មី + Create new store       ← correct
Create new store                        ← WRONG (English-first for Sokha = closes tab immediately)
```

English-first allowed ONLY on: Business dev pages (Keys/Webhooks/API SDK playground/Admin console) where user is explicitly technical / account_type=business.

---

## 4. Border Radius, Spacing, Shadows

### 4.1 Border Radius (Exact from evaluate)

| Token | Value | Usage |
|---|---|---|
| `radius.sm` | 4px | Chips/badges, input border, toast corner |
| `radius.md` | 6px | Small button (table action), inline link badge |
| `radius.lg` | **8px** | **PRIMARY BUTTONS** — exact from chmaba.com `cta.radius = "8px"` |
| `radius.card` | **12px** | **CARDS** — exact from evaluate `secondary button.radius=12px` → matches pricing Free/Pro cards |
| `radius.pill` | 9999px | Tag badges, nav dot status indicators, "MOST POPULAR" tag |

### NON-NEGOTIABLE LAW: No button/card radius EVER exceeds 12px

Reason: > 12px = "fun gaming app" aesthetic. Khmer merchants = "I want this to look like my bank app" (ABA app = buttons radius 4-8px). Match that.

### 4.2 Spacing Scale (8px Base Grid)

Everything aligned to multiples of 8px — like Chmaba POS.

```
4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128
```

Use 8px as minimum. Never use `3px`, `5px`, `7px`.

### 4.3 Elevation / Shadows (From Evaluate `cta.shadow = rgba(105,87,245,0.2) 0 7px 16px`)

| Token | Value | Usage |
|---|---|---|
| `shadow.card` | `0 1px 2px 0 rgba(15, 23, 42, 0.04), 0 4px 12px -4px rgba(15, 23, 42, 0.06)` | Summary cards, tables wrapped in cards, modal backgrounds |
| `shadow.primaryButton` | `0 7px 16px 0 rgba(105, 87, 245, 0.20)` (VIOLET GLOW) | **All primary CTA buttons** — purple glow EXACTLY like "Start free" on chmaba.com → signals "this is the Chmaba brand action". |
| `shadow.khqrCard` | `0 0 0 10px rgba(0, 163, 161, 0.05), 0 12px 40px -10px rgba(0, 163, 161, 0.18)` | KHQR display card on `/pay/{id}` and payment detail (BAKONG TEAL glow = customer thinks "this is official Bakong QR — safe") |
| `shadow.popularCard` | `0 0 0 10px rgba(191, 250, 106, 0.12), 0 10px 25px -10px rgba(191, 250, 106, 0.35)` | Plans "Starter" most popular card lime green halo glow — visible 10px away from other cards so user knows where to look first |

---

## 5. Component Presets (Buttons, Cards, Badges, Statuses)

### 5.1 Buttons (4 Variants)

```tsx
// ——— Variant 1: PRIMARY = brandViolet bg, white fg, 8px radius, violet glow shadow
//    Use for: Create store, Mark paid, Choose Starter, Start free, Generate API key
const PrimaryButton = styled.button`
  background: #6957F5; color: #FFFFFF;
  padding: 12px 20px; border-radius: 8px;
  border: 1px solid rgba(255,255,255,0.08);
  font-weight: 600; font-size: 15px;
  box-shadow: 0 7px 16px rgba(105,87,245,0.20);
  &:hover { background: #5A47E0; transform: translateY(-1px); }
  &:active { transform: translateY(0); }
`;

// ——— Variant 2: SECONDARY (outline/ghost) — white bg, dark ink, gray border, 12px radius
//    Use for: See live demo, Cancel wizard, View CSV download, Rotate key, Re-send webhook
const SecondaryButton = styled.button`
  background: #FFFFFF; color: #282930;
  padding: 12px 20px; border-radius: 12px;
  border: 1px solid #DEDEE7; font-weight: 600;
  &:hover { background: #FAFBFC; border-color: #CACAD6; }
`;

// ——— Variant 3: ABA-GREEN TRUST (ChmabaPay-only) for ABA-linked actions
//    Use for: ABA PayWay paste "Verify & Link", Checkout page header CTA,
//             "Download ABA-style receipt"
const TrustButtonABA = styled.button`
  background: #009639; color: #FFFFFF; padding: 12px 20px;
  border-radius: 8px; font-weight: 600;
  box-shadow: 0 7px 16px rgba(0, 150, 57, 0.18);
`;

// ——— Variant 4: PISTACHIO-LIME (Most-popular plan CTAs inside lime card)
//    Use for: "Choose Starter" inside green card
const LimeCta = styled.button`
  background: #17181C; color: #FFFFFF; padding: 12px 24px;
  border-radius: 10px; font-weight: 700;
  border: 2px solid #17181C;
`;
```

### 5.2 Status Badges (Payments / Plans)

```
Format: 8px pill, font 12px/500, left 6px dot, padding 6px 12px 6px 10px

PAID    → GREEN:  bg #ECFDF5, fg #065F46, dot #10B981
PENDING → AMBER:  bg #FFFBEB, fg #92400E, dot #F59E0B
SCANNED → BLUE:   bg #EFF6FF, fg #1E40AF, dot #3B82F6
EXPIRED → GRAY:   bg #F3F4F6, fg #374151, dot #6B7280
FAILED  → RED:    bg #FEF2F2, fg #991B1B, dot #EF4444

Plan tiers badge:
Starter  → VIOLET #6957F5 bg white fg pill
Growth   → BLUE pill
Scale    → TEAL Bakong pill
Enterprise → GOLD #C9A227 gradient bg white fg star badge
```

### 5.3 Destination Chips (Bank colors — Store wizard, Sub-Merchant add)

```
Format: 16px border-radius chip, 12px text, 6px colored dot before label
Examples:
  🟢 ABA PayWay — st_sokha_noodles
  🔴 ACLEDA #001…4567
  🟣 Bakong ID 12607…3081
  🟠 Wing 200…9876
```

---

## 6. Dark Mode (Business Only, Hidden for Individual Users)

### 6.1 Toggling Rule

```typescript
// Layout component — showThemeToggle()
function showDarkToggle(account: Account): boolean {
  // Law: Individual users NEVER see dark mode. Why?
  // Confusion for non-technical: "Why did everything turn black? Is the site broken?"
  return account.account_type === "business" || account.is_platform_admin;
}
```

### 6.2 Dark Palette (From chmaba.com Dark Mode Evaluate)

| Token | Hex | Source/Reason |
|---|---|---|
| `darkBodyBg` | `#0B0B0E` (near-black, screenshot shows #0b0b0e) | Actual chmaba dark mode body |
| `darkSurfaceCard` | `#1E1E24` (deep gray not quite black) | Pricing cards in dark mode |
| `darkHeaderBg` | `#09090B` (slightly darker than body) | Nav bar background |
| `darkTextPrimary` | `#E4E4E8` | h1 evaluated `fg: #E4E4E8` |
| `darkTextSecondary` | `#B2B2BA` | Body p evaluated |
| `darkTextNav` | `#9A9AA4` | signInFg evaluated |
| `darkBorder` | `#2A2A33` | 5% white border |
| `darkAccent` (terminal/code/copy field bg) | `#05060A` | techy curl snippets background, neon code blocks |
| `brandViolet` | `#6957F5` — SAME, no dark variant | Brand doesn't shift between modes |
| `brandLime` | `#BFFA6A` — SAME | Neon green pops on dark, perfect for "verified/approved" |
| `statusPaid`/`statusPending`/… | ALL SAME hexes across modes | Status colors = invariant, always readable on both card surfaces |

**Techy-only permitted when dark mode = active:** Neon outline around DataTable selected rows, terminal-style code blocks, violet glow around API key reveal animation. On light mode these techy accents are OFF to avoid "too startup" vibe on trust-first pages.

---

## 7. Six Khmer-Market UX Laws (Non-Negotiable)

These rules are not "design preferences". They are derived from Chmaba (2400+ stores running today) product decisions (we inherit the lessons, no need to re-test):

| Law ID | Law | Why It Matters | How To Enforce |
|---|---|---|---|
| **L1** | **Khmer script FIRST on Individual + public checkout pages** | 60% of Individual users don't read English fluently. English-first = instant tab close. | On pages with `page.personality === "trust"`, wrap every CTA/header in: `<div class="km-first"><p class="km-bold">បង្កើតហាង</p><p class="en-muted">Create a store</p></div>` |
| **L2** | **No display fonts on money pages** | Creative fonts on finance pages = "looks like scam". ABA app uses Inter/Noto Sans Khmer only — match that. | Branded/display font allowed: NONE anywhere on checkout /pay/{id}, payment detail, webhook signing. JetBrains Mono ONLY in code/curl blocks. |
| **L3** | **Corner radius <= 12px everywhere** | > 12px = gaming/e-commerce aesthetic, not payment gateway. ABA app buttons are radius 4-8px. We don't look like TikTok; we look like a bank. | Code lint rule in `theme.ts` — `radius.card=12px` max; no component can override to > 12. Design review enforces. |
| **L4** | **Progress = explicit steps, not infinite spinners** | Khmer users hate "loading..." with no ETA (seen too many failed CutLuy/ABA payment spinners that never resolve). | Wizard: `ជំហាន 2 នៃ 3 · Step 2 of 3` progress bar with green ✅ filled on done. Bakong polling on checkout: 3-step timeline: ⏳QR shown → 👁️QR Scanned → ✅Bakong Confirmed. |
| **L5** | **Bank brand color chips** | Wing-red badge registers in 30ms. "Wing Bank 200…9876" text registeres in 800ms. Speed = trust. | Store destination list MUST show bank color dots + short mask. Sub-Merchant overview MUST have this chip on every row. |
| **L6** | **Individual = no dark mode, no power-user toggles** | Non-technical users accidentally switched to dark mode = they think the site broke. They call you. Don't give them the toggle. | `showDarkToggle()` type guard in theme switcher code. Same applies: no "Developer options" exposed for Individual users. |

---

## 8. Per-Page Personality Matrix (Trust-Shell + Techy-Inners Pattern)

We earn the right to be cool/techy ONLY after the trust shell proves we're legitimate. Same rule applies to dark mode availability.

```
Legend:
  TRUST-ONLY  = 100% Chmaba light palette, bank colors chips on destinations,
                Khmer-first text, NO techy accents, dark mode toggle HIDDEN
  BALANCED    = Trust colors shell (cards/buttons/badges) + techy accents
                INSIDE collapsed panels that users can optionally open
  TECHY       = Techy personality encouraged: code blocks, neon outlines dark mode,
                curl snippets, JSON viewers — BALANCED in light, TECHY in dark
  (B)  = Business-only feature (Individual sees 404 or upgrade banner)
  (A)  = Admin-only
```

| Page URL | Personality | Audience | Notes |
|---|---|---|---|
| **/login** + **/onboarding** → account type picker | TRUST ONLY | All | Google sign-in button matches Chmaba POS exactly. Brand violet CTA + ABA/BAKONG logos trust-row at bottom "Secured by ABA & Bakong Open API". |
| **/dashboard** | BALANCED | All | Summary cards trust-style; chart can have violet/lime techy colors (balanced). |
| **/stores/new** → Wizard 3 screens | TRUST ONLY | All | Khmer-first steps, bank color swatches, no gradient CTAs (solid violet/ABA green only). |
| **/stores/[id]** | BALANCED | All | Destination chip L5 + summary trust. "Edit link" modal TRUST. Optional expandable: "KHQR Tag breakdown (EMVCo)" → techy panel, collapsed by default. |
| **/payments** | BALANCED | All | Data rows trust. Badges status. Filter drawer balanced. |
| **/payments/[id]** | BALANCED | All | KHQR card with Bakong teal glow (trust). Status timeline explicit L4. `gateway_status_raw` JSON viewer: collapsed, techy, only if Business OR Admin (hidden for Individual). |
| **/keys** | TECHY 🔒 (Business only on dark; balanced light) | BUSINESS ONLY (L6) | Ind: 🔒 upgrade banner. Bus: reveal-key animation (dots → full string, Stripe-style), copy field, curl example below (TECHY), scope radio (shared account-wide badge violet). |
| **/webhooks** | TECHY 🔒 | BUSINESS ONLY | Endpoint list balanced. Signature playground = 5-language code tab panel (Python/PHP/JS/C#/Java), terminal dark mode background, live compute signature. |
| **/sub-merchants** (B) | TECHY 🔒 | BUSINESS SCALE/ENT | CSV import progress bars techy (percent complete, line-by-line error expandable). Whitelabel editor: live preview on right. |
| **/billing** | BALANCED → TECHY (plans matrix, invoice Pay-Now KHQR) | All | Current card: info-heavy trust. Plans table balanced. Invoices → inline mini KHQR popup (techy + functional). |
| **/settings/profile** | TRUST ONLY | All | No animations — forms look like bank account settings. |
| **/pay/[id]** (PUBLIC CHECKOUT) | TRUST ONLY (100% — MOST IMPORTANT PAGE OF ALL) | End customer | ABA-green header stripe (looks like ABA app). 34px big amount L1. Bakong teal KHQR glow card. Countdown timer explicit mm:ss. Status update messages match ABA app exact wording: `បង្ហាញថាបានបង់ / Payment Successful` — green check when done. `កំពុងរង់ចាំ / Waiting for scan` — amber. 12px no fancy animations. CSS only, no heavy JS library. |
| **/admin/* (A)** | TECHY 🔒🔒 (Admin only, trust secondary) | Platform owner only | Fraud revoke key: terminal-style red confirm `TYPE KEY PREFIX to revoke` → like `aws iam` delete. |

---

## 9. ChmabaPay-Brand vs. Chmaba-POS Brand — Where We Diverge Intentionally

| Aspect | Chmaba POS (chmaba.com) | ChmabaPay (Gateway, us) | Why Diverge? |
|---|---|---|---|
| Primary CTA accent | Violet `#6957F5` | SAME `#6957F5` (shared brand) | Keep unity. |
| Lime accents | Yes (popular cards, pricing tabs) | YES + expanded to: Bakong DLT verified, dark mode neon | Keep unity + extend meaning (verified = lime). |
| Trust badges on hero | No (POS not bank) | YES mandatory hero trust badges: Licensed Bakong Open API Integration • ABA PayWay Partner • Money never touches ChmabaPay (sent direct to merchant account) | Payment page = 10× more trust questions than POS. Merchants need to know: "Am I giving my ABA link to a scammer?" |
| Header theme | Logo on black (dark in dark mode, white logo on light) | SAME shared logo BUT header allowed to add "ChmabaPay" subtitle wordmark below "Chmaba" in violet tiny 12px ("Part of Chmaba family") | Clarifies product split for Chmaba POS users who land on ChmabaPay |
| CTA on empty state | "Open your workspace" | 2 variants: **Ind → "បង្កើតហាង + Create Store (big green ABA/purple CTA)"**; **Bus → "Set up shared account key for stores"** | Different user intent. |
| Plans page CTAs | Lime most-popular card CTAs black bg | SAME plus: Plans column header adds "Max Sub-Merchants (SaaS)" row (our gateway-only feature) | Different plan matrix. |

---

## 10. theme.ts Code (Copy-Paste Ready for Frontend)

### 10.1 TypeScript: `web/shared/styles/theme.ts` (Drop in)

```typescript
// web/shared/styles/theme.ts
export type AccountType = "individual" | "business";

export const brandColors = {
  // — Core Chmaba shared (1:1 chmaba.com reverse-engineered) —
  brandViolet: "#6957F5",        // Primary CTAs, h1 accent flow, primary progress
  brandLime:   "#BFFA6A",        // Most-popular plan cards
  brandInk:    "#17181C",        // Headings, logo wordmark, status PAID display
  textBody:    "#747580",        // Paragraphs, placeholder
  subtleText:  "#60616C",        // Light mode nav links, timestamps
  subtleTextDark: "#9A9AA4",     // Dark mode nav links
  white:       "#FFFFFF",
  surfaceLight: "#FAFBFC",      // Body bg light
  surfaceCard:  "#FFFFFF",      // Card surface light

  // — ChmabaPay TRUST ADDITIONS (gateway = banking trust) —
  trustAbaGreen:   "#009639",   // ABA PayWay badges, checkout header, verify CTA
  trustBakongTeal: "#00A3A1",   // Bakong ID chips, KHQR card glow, settlement verified
  trustNavy:       "#0F2747",   // legal
  goldVerified:    "#C9A227",   // Enterprise tier

  // — Status colors (payment gateway industry standard) —
  statusPaid:     "#10B981",
  statusPending:  "#F59E0B",
  statusScanned:  "#3B82F6",
  statusFailed:   "#EF4444",
  statusExpired:  "#6B7280",

  // — Bank brand swatches (destination chips L5 UX law)
  bank: {
    ABA: "#009639",
    ACLB: "#D7282F",
    CADI: "#0067B1",
    WING: "#E31937",
    SAPA: "#F68B1F",
    PHIL: "#00AA9B",
    CMB:  "#B51E24",
    PRINCE: "#1F3A93",
    BAKONG: "#00A3A1",
  },

  // — Dark mode palette (reverse-engineered from chmaba.com dark)
  dark: {
    bodyBg:       "#0B0B0E",
    surfaceCard:  "#1E1E24",
    headerBg:     "#09090B",
    textPrimary:  "#E4E4E8",
    textSecondary:"#B2B2BA",
    navLink:      "#9A9AA4",
    border:       "#2A2A33",
    terminalBg:   "#05060A",    // Techy code blocks in dark mode
    accentNeon:   "#22D3EE",    // Secondary outline glow (techy only)
  },
};

export const radius = {
  sm: 4,
  md: 6,
  lg: 8,          // PRIMARY BUTTONS (1:1 chmaba.com evaluate)
  card: 12,       // CARDS (1:1 chmaba.com evaluate)
  pill: 9999,
};

export const spacing8Grid = [4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 96, 128];

export const shadows = {
  card: "0 1px 2px 0 rgba(15,23,42,0.04), 0 4px 12px -4px rgba(15,23,42,0.06)",
  // 1:1 from chmaba.com evaluate.cta.shadow
  primaryButton: "0 7px 16px 0 rgba(105,87,245,0.20)",
  khqrCard: "0 0 0 10px rgba(0,163,161,0.05), 0 12px 40px -10px rgba(0,163,161,0.18)",
  popularPlan: "0 0 0 10px rgba(191,250,106,0.12), 0 10px 25px -10px rgba(191,250,106,0.35)",
};

export const fonts = {
  sans: `'Inter', 'Noto Sans Khmer', ui-sans-serif, system-ui, -apple-system, sans-serif`,
  mono: `'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace`,
};

// —————————————————————————————————————————————————————————
// UX Law type guards (L6: no dark mode for Individual, etc.)
// —————————————————————————————————————————————————————————
export function showThemeToggle(accountType: AccountType, isPlatformAdmin = false): boolean {
  // UX LAW #6: INDIVIDUAL USERS NEVER SEE DARK MODE TOGGLE
  return accountType === "business" || isPlatformAdmin;
}

export function showTechyPanels(accountType: AccountType): boolean {
  // JSON viewer, KHQR tag dump, curl examples — hidden on Individual unless expanded
  // by explicit user click (collapse by default so techy stuff doesn't intimidate)
  return accountType === "business";
}

export function khmerFirst(isIndividualPageOrPublicCheckout = true): boolean {
  // UX LAW #1: KHMER FIRST on individual pages + public checkout
  return isIndividualPageOrPublicCheckout;
}

export const i18nDefaults = {
  supportedLocales: ["en", "km"],
  defaultForIndividual: "km",   // Individual users default to Khmer
  defaultForBusiness: "en",     // Business default to English (they can switch)
};
```

### 10.2 React Hook for Applying (Next.js Tailwind / shadcn/ui)

If using Tailwind `tailwind.config.ts`:

```typescript
// tailwind.config.ts  →  drop our brandViolet/lime/abaGreen/bakongTeal tokens directly
export default {
  content: ["./web/**/*.{ts,tsx,js,jsx}"],
  theme: {
    extend: {
      colors: {
        violet: brandColors.brandViolet,
        violetHover: "#5A47E0",
        lime: brandColors.brandLime,
        aba: brandColors.trustAbaGreen,
        bakong: brandColors.trustBakongTeal,
        navy: brandColors.trustNavy,
        gold: brandColors.goldVerified,
        ink: brandColors.brandInk,
      },
      borderRadius: {
        lg: `${radius.lg}px`,      // Primary buttons
        card: `${radius.card}px`,  // Cards
      },
      boxShadow: {
        "btn-primary": shadows.primaryButton,
        "card-khqr": shadows.khqrCard,
        "plan-popular": shadows.popularPlan,
      },
      fontFamily: { sans: fonts.sans.split(",").map(s=>s.trim()), mono: fonts.mono.split(",") },
    },
  },
};
```

---

**END OF DESIGN TOKENS & UX LAWS DOCUMENT.**

Single source of truth — if a frontend engineer wonders "what radius should the CTA button be?", the answer is not "I think 10px". It is: `radius.lg = 8px → exact from chmaba.com evaluate`. Always open this document first; never guess colors/sizes.

# @chmabapay/shared

Shared design tokens + TypeScript components used by both User Portal (`@chmabapay/user`)
and Admin Portal (`@chmabapay/admin`).

## Contents
- `theme.ts` — VERBATIM copy of DESIGN_TOKENS_AND_UX_LAWS.md §10.1. Single source of truth for
  brand colors (`brandViolet: #6957F5`, `brandLime: #BFFA6A`, ABA/Bakong trust colors),
  radius (`lg=8px` buttons, `card=12px` cards MAX), shadows (`primaryButton` violet glow),
  font stacks (Khmer-first), and the `showThemeToggle()` type guard for L6.
- `ux-laws.ts` — Khmer-market UX laws as FUNCTION type guards:
  - `showKhmerFirst(pageKey)` — L1 Individual trust pages Khmer-first.
  - `enforceMaxRadius(8|12)` — L3 radius <= 12px everywhere.
  - `explicitStepProgressBars(pageKey)` — L4 steps over spinners on wizard/checkout.
  - `bankBrandChips(code)` — L5 ABA green `#009639`, Bakong teal, Wing red etc.
  - `shouldRenderDarkToggle(accountType)` — L6 Individual = false.
- `components/` — 80% shareable components:
  `StatusBadge`, `CopyField`, `DataTable<T>`, `StepProgress`, `ModalProvider/useModal`,
  `KHQR`.

## Notes
NO hardcoded hex anywhere except inside `theme.ts`. Components consume the exported tokens
at runtime (inline styles) + Tailwind tokens (tailwind.config.ts imports `theme.ts`).

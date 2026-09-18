# @chmabapay/shared

Shared design tokens, plus the Khmer-market UX laws and a small component set.

`theme.ts` is the live part: the landing app (`@chmabapay/landing`) imports it through the
`@shared/*` path alias. Nothing imports `ux-laws.ts` or `components/` any more — the landing
app uses only `theme.ts`, the admin console carries its own copy of the design system, and
the user portal that did use them has been deleted. They are kept as the working
implementation of `web/DESIGN_TOKENS_AND_UX_LAWS.md`; delete them if that Khmer-first
direction is abandoned.

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

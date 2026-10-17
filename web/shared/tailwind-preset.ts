/**
 * ChmabaPay shared Tailwind preset — the ONE style template.
 *
 * Both `web/landing` and `web/admin` used to inline an identical copy of this
 * `theme.extend` block in their own `tailwind.config.ts`. Copies drift: the
 * landing copy quietly grew a token the admin copy did not have, and nothing
 * failed, so "the two portals share a style template" was a claim held together
 * by review rather than by the build. This file makes it a fact — there is one
 * definition of the Chmaba palette, radii, shadows and fonts, and a portal that
 * does not inherit from it is the thing you have to do on purpose.
 *
 * The values themselves are still owned by `web/shared/theme.ts` (the 1:1
 * reverse-engineer of chmaba.com documented in
 * `web/DESIGN_TOKENS_AND_UX_LAWS.md`); this preset only maps them to Tailwind.
 *
 * Deliberately typed as a plain object rather than `import type { Config }`:
 * this file is resolved from the app's own config context, and dropping the
 * type import keeps `web/shared` free of a Tailwind build-time dependency.
 */

import { brandColors, radius, shadows, fonts } from "./theme";

const parseFonts = (stack: string) => stack.split(",").map((s) => s.trim());

export const chmabaTailwindPreset = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        violet: brandColors.brandViolet,
        "violet-hover": "#5A47E0",
        lime: brandColors.brandLime,
        aba: brandColors.trustAbaGreen,
        bakong: brandColors.trustBakongTeal,
        navy: brandColors.trustNavy,
        gold: brandColors.goldVerified,
        ink: brandColors.brandInk,
        body: brandColors.textBody,
        subtle: brandColors.subtleText,
        surface: brandColors.surfaceLight,
        card: brandColors.surfaceCard,
        "border-light": brandColors.borderLight,
        "border-muted": brandColors.borderMuted,
        "border-soft": brandColors.borderSoft,
        status: {
          paid: brandColors.statusPaid,
          pending: brandColors.statusPending,
          scanned: brandColors.statusScanned,
          failed: brandColors.statusFailed,
          expired: brandColors.statusExpired,
        },
        util: brandColors.util,
        bank: brandColors.bank,
        dark: {
          bg: brandColors.dark.bodyBg,
          card: brandColors.dark.surfaceCard,
          header: brandColors.dark.headerBg,
          "text-primary": brandColors.dark.textPrimary,
          "text-secondary": brandColors.dark.textSecondary,
          nav: brandColors.dark.navLink,
          border: brandColors.dark.border,
          terminal: brandColors.dark.terminalBg,
          neon: brandColors.dark.accentNeon,
        },
      },
      borderRadius: {
        lg: `${radius.lg}px`,
        card: `${radius.card}px`,
      },
      boxShadow: {
        "btn-primary": shadows.primaryButton,
        "card-khqr": shadows.khqrCard,
        "plan-popular": shadows.popularPlan,
        card: shadows.card,
      },
      fontFamily: {
        sans: parseFonts(fonts.sans),
        mono: parseFonts(fonts.mono),
      },
    },
  },
} as const;

/**
 * The `content` globs every ChmabaPay portal must scan. Kept beside the preset
 * because a portal that inherits the theme but forgets to scan the shared
 * component tree drops those utilities from its stylesheet — the classic
 * "the shared component renders unstyled" bug.
 */
export const chmabaContentGlobs = [
  "./app/**/*.{ts,tsx}",
  "./components/**/*.{ts,tsx}",
  "../shared/components/**/*.{ts,tsx}",
  "../shared/theme.ts",
  "../shared/ux-laws.ts",
];

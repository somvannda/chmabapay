/**
 * ChmabaPay shared Tailwind preset — the ONE style template.
 *
 * Ported onto the real Chmaba design tokens (`web/shared/tokens.css`, a 1:1 copy
 * of `Chmaba/packages/ui/tokens.css`) rather than the old reverse-engineered
 * approximation. Colours resolve through CSS custom properties, so the light and
 * `.dark` token sets both work and a scoped `--color-brand` override rebrands a
 * subtree.
 *
 * Both `web/landing` and `web/admin` inherit this preset, and a portal that does
 * not is the thing you have to do on purpose.
 *
 * The ChmabaPay-only additions stay: ABA green, Bakong teal, the bank swatches and
 * the payment status colours, because a gateway carries trust colours a POS does
 * not need.
 */

import { brandColors, radius, shadows, fonts } from "./theme";

const parseFonts = (stack: string) => stack.split(",").map((s) => s.trim());

/** A token from `tokens.css` as a Tailwind colour that supports `/opacity`. */
const channel = (name: string) => `rgb(var(--color-${name}) / <alpha-value>)`;

export const chmabaTailwindPreset = {
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        // — Chmaba token names (CSS vars → dark mode aware) —
        brand: {
          DEFAULT: channel("brand"),
          hover: channel("brand-hover"),
          soft: channel("brand-soft"),
          tint: channel("brand-tint"),
        },
        ink: {
          DEFAULT: channel("ink"),
          soft: channel("ink-soft"),
        },
        muted: channel("muted"),
        page: channel("page"),
        line: {
          DEFAULT: channel("line"),
          strong: channel("line-strong"),
        },
        success: {
          DEFAULT: channel("success"),
          soft: channel("success-soft"),
        },
        danger: {
          DEFAULT: channel("danger"),
          soft: channel("danger-soft"),
        },

        // — ChmabaPay aliases kept so existing classes keep their meaning —
        // `surface` stays the page background and `card` the raised surface, which
        // is how the portal already reads them; `page`/`brand`/`line` above are the
        // Chmaba-native names for new code.
        violet: channel("brand"),
        "violet-hover": channel("brand-hover"),
        lime: brandColors.brandLime,
        aba: brandColors.trustAbaGreen,
        bakong: brandColors.trustBakongTeal,
        navy: brandColors.trustNavy,
        gold: brandColors.goldVerified,
        body: channel("ink-soft"),
        subtle: channel("muted"),
        surface: channel("page"),
        card: channel("surface"),
        "surface-muted": channel("surface-muted"),
        "surface-sunken": channel("surface-sunken"),
        "border-light": channel("line-strong"),
        "border-muted": brandColors.borderMuted,
        "border-soft": brandColors.borderSoft,
        status: {
          paid: channel("success"),
          pending: brandColors.statusPending,
          scanned: brandColors.statusScanned,
          failed: channel("danger"),
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
        // — Chmaba's own elevation names —
        soft: "0 14px 40px rgba(28, 31, 42, .07)",
        panel: "0 28px 70px rgba(28, 31, 42, .12)",
      },
      keyframes: {
        fadeUp: {
          "0%": { opacity: "0", transform: "translateY(12px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
        fadeIn: {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
        pulseSoft: {
          "0%, 100%": { opacity: ".55" },
          "50%": { opacity: "1" },
        },
      },
      animation: {
        "fade-up": "fadeUp .5s ease-out both",
        "fade-in": "fadeIn .35s ease-out both",
        "pulse-soft": "pulseSoft 2.3s ease-in-out infinite",
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
 * component tree drops those utilities from its stylesheet.
 */
export const chmabaContentGlobs = [
  "./app/**/*.{ts,tsx}",
  "./components/**/*.{ts,tsx}",
  "../shared/components/**/*.{ts,tsx}",
  "../shared/theme.ts",
  "../shared/ux-laws.ts",
  "../shared/chrome.tsx",
  "../shared/ui.tsx",
];

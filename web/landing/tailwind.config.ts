import type { Config } from "tailwindcss";
import { brandColors, radius, shadows, fonts } from "../shared/theme";

const parseFonts = (stack: string) => stack.split(",").map((s) => s.trim());

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "../shared/components/**/*.{ts,tsx}",
    "../shared/theme.ts",
    "../shared/ux-laws.ts",
  ],
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
  plugins: [],
};

export default config;

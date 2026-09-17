// web/shared/styles/theme.ts
export type AccountType = "individual" | "business";

export const brandColors = {
  // — Core Chmaba shared (1:1 chmaba.com reverse-engineered) —
  brandViolet: "#6957F5",        // Primary CTAs, h1 accent flow, primary progress
  brandLime:   "#BFFA6A",        // Most-popular plan cards, approved badges
  brandInk:    "#17181C",        // Headings, logo wordmark, status PAID display
  textBody:    "#747580",        // Paragraphs, placeholder
  subtleText:  "#60616C",        // Light mode nav links, timestamps
  subtleTextDark: "#9A9AA4",     // Dark mode nav links
  white:       "#FFFFFF",
  surfaceLight: "#FAFBFC",      // Body bg light
  surfaceCard:  "#FFFFFF",      // Card surface light
  borderLight:  "#DEDEE7",      // Navbar/card borders (reverse-engineered chmaba.com divider)
  borderMuted:  "#CACAD6",      // Hovered secondary button border
  borderSoft:   "#F0F0F4",      // Inner QR card padding border

  // — Utility neutral surfaces for badges/chips (reverse-engineered from StatusBadge styleMap) —
  util: {
    successSoftBg: "#ECFDF5",
    successSoftFg: "#065F46",
    tealSoftBg:    "#CCFBF1",
    tealSoftFg:    "#134E4A",
    amberSoftBg:   "#FFFBEB",
    amberSoftFg:   "#92400E",
    blueSoftBg:    "#EFF6FF",
    blueSoftFg:    "#1E40AF",
    redSoftBg:     "#FEF2F2",
    redSoftFg:     "#991B1B",
    graySoftBg:    "#F3F4F6",
    graySoftFg:    "#374151",
    grayBorder:    "#E5E7EB",
    grayChipText:  "#9CA3AF",
  },

  // — ChmabaPay TRUST ADDITIONS (gateway = banking trust) —
  trustAbaGreen:   "#009639",   // ABA PayWay badges, checkout header, verify CTA
  trustBakongTeal: "#00A3A1",   // Bakong ID chips, KHQR card glow, settlement verified
  trustNavy:       "#0F2747",   // Legal + policy surfaces
  goldVerified:    "#C9A227",   // Enterprise tier accent

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

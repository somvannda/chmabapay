import { brandColors, radius, showThemeToggle } from "./theme";

export type AccountType = "individual" | "business";
export type TrustPageKey = "onboarding" | "checkout" | "stores-new";
export type TechPageKey = "keys" | "webhooks" | "admin";

/** Every page the UX laws can classify. */
export type PageKey =
  | TrustPageKey
  | TechPageKey
  | "dashboard"
  | "payments"
  | "payments-detail"
  | "stores-detail"
  | "settings-profile"
  | "settings-billing"
  | "login";
export type BankCode = keyof typeof brandColors.bank;

export const TRUST_PAGES: TrustPageKey[] = ["onboarding", "checkout", "stores-new"];
export const TECH_PAGES: TechPageKey[] = ["keys", "webhooks", "admin"];

export function showKhmerFirst(pageKey: PageKey): boolean {
  if (TRUST_PAGES.includes(pageKey as TrustPageKey)) {
    return true;
  }
  if (TECH_PAGES.includes(pageKey as TechPageKey)) {
    return false;
  }
  return true;
}

export function enforceMaxRadius(value: 8 | 12): boolean {
  return value <= radius.card;
}

export function explicitStepProgressBars(pageKey: TrustPageKey | TechPageKey): boolean {
  if (pageKey === "onboarding" || pageKey === "stores-new" || pageKey === "checkout") {
    return true;
  }
  return false;
}

export function bankBrandChips(bankCode: BankCode): { color: string; label: string } {
  const color = brandColors.bank[bankCode] ?? brandColors.trustBakongTeal;
  const labels: Record<BankCode, string> = {
    ABA: "ABA Bank",
    ACLB: "ACLEDA Bank",
    CADI: "Canadia Bank",
    WING: "Wing Bank",
    SAPA: "Sathapana Bank",
    PHIL: "Phillip Bank",
    CMB: "Chip Mong Bank",
    PRINCE: "Prince Bank",
    BAKONG: "Bakong",
  };
  return { color, label: labels[bankCode] ?? bankCode };
}

export function shouldRenderDarkToggle(accountType: AccountType, isPlatformAdmin = false): boolean {
  return showThemeToggle(accountType, isPlatformAdmin);
}

export type PagePersonality = "trust" | "balanced" | "techy";

export function getPagePersonality(pageKey: PageKey): PagePersonality {
  const trustOnly: string[] = ["onboarding", "checkout", "stores-new", "login", "settings-profile"];
  const techy: string[] = ["keys", "webhooks", "admin"];
  if (trustOnly.includes(pageKey)) return "trust";
  if (techy.includes(pageKey)) return "techy";
  return "balanced";
}

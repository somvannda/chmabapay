/**
 * One money formatter for both portals.
 *
 * The API sends the amount as a plain decimal string (`amount`) *and* the currency
 * it was charged in, and the currency is what decides how the number reads:
 *
 *   USD   $4,728.29   two decimals, thousands grouped
 *   KHR   ៛4,000      no decimals, thousands grouped
 *
 * The riel has no subunit in KHQR — Tag 54 carries whole riel — so `៛4,000.00` is
 * not a figure anyone writes, and the payer is never shown one either.
 *
 * The USD output is deliberately byte-identical to what the portal printed before
 * this module existed, so adding it changed nothing until a KHR store appeared;
 * that is also what makes it safe to land ahead of the flag.
 */

export type CurrencyCode = string | null | undefined;

/** The symbol a currency is written with. Anything unknown falls back to dollars. */
export function moneySymbol(currency: CurrencyCode): string {
  return String(currency ?? "").toUpperCase() === "KHR" ? "៛" : "$";
}

/** Whether amounts in this currency are written with a fractional part. */
export function hasFraction(currency: CurrencyCode): boolean {
  return String(currency ?? "").toUpperCase() !== "KHR";
}

/** `$0.00` / `៛0` — the zero to show when there is no figure to show. */
export function moneyZero(currency: CurrencyCode): string {
  return `${moneySymbol(currency)}0${hasFraction(currency) ? ".00" : ""}`;
}

/**
 * Format an amount the API sent as a decimal string or a number.
 *
 * A value that will not parse is returned untouched: the API is the authority on
 * what it sent, and turning `"—"` into `$0.00` would invent a figure.
 */
export function formatMoney(
  value: string | number | null | undefined,
  currency: CurrencyCode,
): string {
  if (value === null || value === undefined || value === "") {
    return moneyZero(currency);
  }
  const n = typeof value === "number" ? value : parseFloat(value);
  if (Number.isNaN(n)) return String(value);
  const decimals = hasFraction(currency) ? 2 : 0;
  return `${moneySymbol(currency)}${n.toLocaleString("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`;
}

/**
 * Format a minor-unit amount — cents for USD, riel × 100 for KHR, the convention
 * every money column in this codebase uses.
 */
export function formatMoneyCents(
  cents: number | null | undefined,
  currency: CurrencyCode,
): string {
  if (cents === null || cents === undefined) return moneyZero(currency);
  return formatMoney(cents / 100, currency);
}

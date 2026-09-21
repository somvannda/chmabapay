/**
 * Turn a failed response into something a human can act on.
 *
 * Mirrors how chmaba.com normalises API failures: prefer the API's `detail`
 * (FastAPI HTTPException / validation errors) and fall back to the status code,
 * never the raw response body.
 *
 * It also translates the machine codes the API answers with. Those codes are stable
 * on purpose — an integrator can branch on `quota_exceeded` — but they are not what a
 * merchant should be shown, and every panel that rendered one read as a bug rather
 * than a limit.
 */

export type ApiErrorInfo = {
  /** Copy for the person reading the screen, without the machine code. */
  message: string;
  /** The remedy is a plan upgrade, so the caller can render an upgrade link. */
  upgrade: boolean;
  /** Set when one field is at fault rather than the whole form. */
  field: "link" | null;
};

const ERROR_COPY: Record<string, string> = {
  quota_exceeded:
    "You have reached this month's payment limit on your plan. New payments are blocked until you upgrade or the period resets.",
  amount_too_low: "That amount is below the minimum this store can charge.",
  amount_too_high: "That amount is above the maximum allowed for this store.",
  store_disabled:
    "This store is disabled, so it cannot take payments. Re-enable it in the store's settings first.",
  merchant_store_disabled:
    "This store is disabled, so it cannot take payments. Re-enable it in the store's settings first.",
  whitelabel_not_enabled:
    "White-label checkout is not enabled for this account. Contact ChmabaPay to turn it on.",
  offline_qr_requires_a_confirmation_source:
    "An offline QR code is refused here: this deployment has no way to confirm the payment later, and an unverifiable code is worse than none. Ask for a hosted code instead.",
};

function detailOf(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) return String(first.msg);
  }
  return null;
}

/**
 * Describe an API `detail` string: the copy to show, whether it is a plan limit, and
 * whether one field is to blame.
 *
 * The link checks answer with a prefix — `payway_link_not_found: ABA PayWay has no
 * link at …` — so a caller can tell a rejected *link* from a rejected store and put
 * the message on the field that is actually wrong.
 */
export function describeApiError(detail: string): ApiErrorInfo {
  if (detail.startsWith("payway_link_")) {
    return {
      message: detail.replace(/^payway_link_[a-z]+:\s*/, ""),
      upgrade: false,
      field: "link",
    };
  }

  const code = detail.split(":", 1)[0].trim();
  if (ERROR_COPY[code]) {
    return { message: ERROR_COPY[code], upgrade: code === "quota_exceeded", field: null };
  }

  // The store cap answers with prose rather than a code. Kept as-is rather than
  // rewritten so the number it names ("Max stores (5)") stays accurate to the plan,
  // but it is still an upgrade: that is the only way past it.
  if (detail.startsWith("Max stores")) {
    return { message: detail, upgrade: true, field: null };
  }

  return { message: detail, upgrade: false, field: null };
}

/** The display message for a failed response, plus what kind of failure it was. */
export async function readApiErrorInfo(res: Response): Promise<ApiErrorInfo> {
  const body = await res.json().catch(() => null);
  const detail = detailOf(body);
  if (detail === null) {
    return {
      message: `Request failed (${res.status})`,
      upgrade: res.status === 402,
      field: null,
    };
  }
  return describeApiError(detail);
}

export async function readApiError(res: Response): Promise<string> {
  return (await readApiErrorInfo(res)).message;
}

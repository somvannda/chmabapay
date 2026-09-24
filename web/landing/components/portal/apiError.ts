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
  // Raised when a plan change is refused because an invoice for the period is still
  // unpaid (billing.py, `open_invoice_unpaid`). The API appends the invoice id and the
  // period, which the copy does not repeat — the account only ever has one open.
  open_invoice_unpaid:
    "This account already has an unpaid invoice for this period, so another one cannot be raised. Settle the existing invoice on the billing page, or move to the free plan, before changing plan again.",
  // Raised when the platform's own collection destination is not configured, so a
  // plan invoice cannot be paid at all (billing.py, `billing_not_open`).
  billing_not_open:
    "Plan payments are not switched on yet, so this invoice cannot be paid. Contact support and we will settle it with you.",
  // Raised when a new password exceeds bcrypt's 72-byte limit (routers/account.py).
  password_too_long:
    "That password is too long — 72 bytes at most (fewer characters if you use accented or Khmer text). Choose a shorter one.",
  // Raised when the chosen plan exists but is not public or not active (billing.py).
  plan_not_available:
    "That plan is not available to switch to right now. Choose another plan.",
  // Raised when a store has no active payment link to mint a QR against
  // (services/payments.py).
  payment_link_disabled:
    "This store has no active payment link, so it cannot take payments. Add or re-enable one in the store's settings first.",
  // Raised when a request has no store and the account has more than one active store
  // (routers/payments.py): there is no single store to fall back on.
  store_required_or_merchant_required:
    "This account has more than one active store, so the request has to name one: pass the store's id or its merchant id.",
  // Raised when a `merchant=` lookup finds no store on this account
  // (routers/payments.py).
  merchant_not_found:
    "No store on this account uses that merchant id. Check the id, or pass the store id instead.",
  // Raised on every write while the account is on hold for non-payment
  // (routers/auth.py, `restricted_may_reach`). The dashboard disables its own controls in this
  // state, so this message is what a keyboard-driven or stale-tab attempt sees — and it has to
  // say which screen fixes it, or the merchant has no idea where to go.
  account_restricted:
    "Your account is on hold because a plan invoice is unpaid, so this change was not saved. Settle the invoice or move to a smaller plan on the billing page, and everything starts working again.",
};

function detailOf(body: unknown): string | null {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  return null;
}

/**
 * A 422 answers with FastAPI's validation array instead of a `detail` code. Those
 * entries describe our own schema — "Extra inputs are not permitted", "Input should
 * be a valid integer" — so showing one tells the merchant about the request we
 * built, not about the mistake they made, and reads as a bug rather than a
 * rejection. Recognised here so the caller can say something actionable instead.
 */
function isValidationDetail(body: unknown): boolean {
  const detail = (body as { detail?: unknown } | null)?.detail;
  return Array.isArray(detail) && detail.length > 0;
}

/**
 * The codes whose own prose is better copy than anything rewritten here, because it
 * names the next action. Deliberately a short list rather than "show the prose for
 * every prefix code": `bakong_error: <upstream text>` and `qr_render_failed: <exc>`
 * would put exception text in front of a merchant.
 */
const PROSE_CODES = new Set([
  "email_change_requires_password",
  "platform_admin_cannot_self_delete",
]);

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

  if (PROSE_CODES.has(code)) {
    return { message: detail.slice(code.length + 1).trim(), upgrade: false, field: null };
  }

  // Answers with `tx_not_found_yet; signals=…` — a semicolon, not a colon, so it never
  // reaches the code lookup above and the signals list is a diagnostic, not copy.
  if (detail.startsWith("tx_not_found_yet")) {
    return {
      message: "No transaction confirms this payment yet. Check again in a moment.",
      upgrade: false,
      field: null,
    };
  }

  // Anything still unmapped is a machine token: a bare `snake_case` code, or one
  // followed by prose. Neither belongs on screen — the token exposes our vocabulary
  // and the prose can carry upstream exception detail. A refusal that deserves its own
  // wording belongs in ERROR_COPY, which is where user-facing copy for a code lives.
  if (/^[a-z0-9_]+:/.test(detail) || /^[a-z0-9_]+$/.test(detail.trim())) {
    return {
      message:
        "That request was refused. Check the values you sent, and contact support if it keeps happening.",
      upgrade: false,
      field: null,
    };
  }

  return { message: detail, upgrade: false, field: null };
}

/** The display message for a failed response, plus what kind of failure it was. */
export async function readApiErrorInfo(res: Response): Promise<ApiErrorInfo> {
  const body = await res.json().catch(() => null);

  if (isValidationDetail(body)) {
    return {
      message:
        "Some of the details in that request were not accepted. Check the values and try again.",
      upgrade: false,
      field: null,
    };
  }

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

/**
 * Turn a failed response into a message a human can read.
 *
 * Prefer the API's `detail` (FastAPI HTTPException / validation errors) and fall
 * back to the status code, never the raw response body.
 *
 * The operator actions (plan override, invoice resolution, key revoke) refuse with
 * machine codes, and several of those refusals are *expected* — "this invoice was
 * already waived" is a normal answer, not a failure. Printing the code at an operator
 * makes a working guard look like a bug, so the ones that can realistically be hit
 * from the console are translated here.
 */

const ERROR_COPY: Record<string, string> = {
  last_platform_admin:
    "This is the only active platform admin. Suspending it would lock everyone out of the console — make another admin first.",
  plan_unchanged: "The account is already on that plan, so nothing was changed.",
  plan_not_found: "That plan no longer exists. Reload the page and pick another.",
  plan_code_exists:
    "A plan with that code already exists. Pick a different code — the code is what the API and checkout use, so it has to be unique.",
  invoice_already_paid:
    "This invoice is already settled. Nothing was changed, and the audit trail is untouched.",
  invoice_already_waived:
    "This invoice was already waived. Nothing was changed, and the audit trail is untouched.",
  invoice_already_credited:
    "This invoice was already credited. Nothing was changed, and the audit trail is untouched.",
  invoice_already_void:
    "This invoice was already voided. Nothing was changed, and the audit trail is untouched.",
  invoice_not_found: "That invoice no longer exists. Reload the page to see the current invoices.",
  key_not_found: "That API key no longer exists. Reload the page to see the current keys.",
  store_not_found: "That store no longer exists. Reload the page to see the current stores.",
  payment_already_paid:
    "This payment is already marked paid. Nothing was changed, and the audit trail is untouched.",
  payment_is_reversed:
    "This payment was refunded, so it cannot be marked paid — a refund cannot be undone by a status edit.",
  payment_is_failed:
    "This payment failed, so it cannot be marked paid. Issue a fresh payment code instead.",
  payment_not_markable:
    "This payment reached a terminal state while you were deciding, so it could not be marked paid. Reload and check its current status.",
  no_deliveries_for_payment:
    "This payment has no webhook deliveries to re-send. Nothing was queued.",
  delivery_not_found:
    "That delivery no longer exists. Reload the page to see the current deliveries.",
  invalid_assignee:
    "That account is not a platform admin, so it cannot hold a support request. Check the account ID — only an admin can be assigned one.",
  too_many_features:
    "That plan has too many feature bullets. Use at most 12.",
  feature_too_long:
    "One of the feature bullets is too long. Keep each to 80 characters or fewer.",
};

/** Strip a validation error's "Value error, " prefix so the machine code is visible. */
function codeFromValidationMessage(message: string): string | null {
  const stripped = message.replace(/^Value error,\s*/, "").trim();
  for (const code of Object.keys(ERROR_COPY)) {
    if (stripped === code || stripped.startsWith(`${code}:`)) return code;
  }
  return null;
}

export async function readApiError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = body?.detail;

  if (typeof detail === "string" && detail.trim()) {
    return ERROR_COPY[detail] ?? detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) {
      const message = String(first.msg);
      // Pydantic wraps a validator's ValueError as "Value error, <code>: ...", so the
      // code is not the whole `detail`. Translate it rather than printing the wrapper.
      const code = codeFromValidationMessage(message);
      return code ? ERROR_COPY[code] : message;
    }
  }

  return `Request failed (${res.status})`;
}

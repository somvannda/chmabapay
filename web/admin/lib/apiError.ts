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
  invoice_already_paid:
    "This invoice is already settled. Nothing was changed, and the audit trail is untouched.",
  invoice_already_waived:
    "This invoice was already waived. Nothing was changed, and the audit trail is untouched.",
  invoice_already_credited:
    "This invoice was already credited. Nothing was changed, and the audit trail is untouched.",
  key_not_found: "That API key no longer exists. Reload the page to see the current keys.",
  store_not_found: "That store no longer exists. Reload the page to see the current stores.",
};

export async function readApiError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = body?.detail;

  if (typeof detail === "string" && detail.trim()) {
    return ERROR_COPY[detail] ?? detail;
  }

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) return String(first.msg);
  }

  return `Request failed (${res.status})`;
}

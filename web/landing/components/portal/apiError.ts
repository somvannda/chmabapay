/**
 * Turn a failed response into a message a human can read.
 *
 * Mirrors how chmaba.com normalises API failures: prefer the API's `detail`
 * (FastAPI HTTPException / validation errors) and fall back to the status code,
 * never the raw response body.
 */
export async function readApiError(res: Response): Promise<string> {
  const body = (await res.json().catch(() => null)) as { detail?: unknown } | null;
  const detail = body?.detail;

  if (typeof detail === "string" && detail.trim()) return detail;

  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    if (first?.msg) return String(first.msg);
  }

  return `Request failed (${res.status})`;
}

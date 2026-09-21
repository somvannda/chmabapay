/**
 * `fetch` for the platform console: same-origin, cookie-carrying, session-aware.
 *
 * Every page used to call `fetch` directly and render whatever came back, so an
 * expired session showed up as a warn box containing the literal string
 * `invalid_session` — which reads as a broken page rather than "you are signed
 * out". The operator's only clue was to guess that reloading might help.
 *
 * Two answers put the operator on the login screen instead, preserving where they
 * were:
 *
 *   - `401` — the session cookie is gone or expired.
 *   - `403 password_session_required` — a *Google* session. It is a valid session
 *     for the merchant portal, and the console deliberately refuses it (see
 *     `get_hybrid_admin_context`), so the remedy is the same: sign in with a
 *     password.
 *
 * The response is returned either way, so callers keep their own error handling —
 * this only adds the redirect.
 */

let redirecting = false;

function redirectToLogin(): void {
  if (redirecting) return;
  // Guard against a loop: the login page must never redirect to itself.
  if (typeof window === "undefined") return;
  if (window.location.pathname.startsWith("/login")) return;
  redirecting = true;
  const next = encodeURIComponent(
    window.location.pathname + window.location.search,
  );
  window.location.replace(`/login?next=${next}`);
}

export async function apiFetch(
  input: string,
  init?: RequestInit,
): Promise<Response> {
  const res = await fetch(input, { credentials: "include", ...init });

  if (res.status === 401) {
    redirectToLogin();
    return res;
  }

  if (res.status === 403) {
    // `clone()` so the caller can still read the body it is about to handle.
    const body = (await res
      .clone()
      .json()
      .catch(() => null)) as { detail?: unknown } | null;
    if (body?.detail === "password_session_required") {
      redirectToLogin();
    }
  }

  return res;
}

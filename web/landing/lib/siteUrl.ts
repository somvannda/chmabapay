import { headers } from "next/headers";

/**
 * The origin this app is being served from.
 *
 * The API is on the same origin — the edge routes `/api/v1/*`, `/pay/*` and `/auth/*`
 * to the API and everything else to this app — so the request's own host is the
 * public base URL in every environment: `https://pay.chmaba.com` in production,
 * the local port during development.
 *
 * `NEXT_PUBLIC_API_URL` is deliberately *not* the source: in the production image
 * it is the compose-internal `http://api:8000`, a name that resolves only inside
 * the Docker network. It used to be printed on the API docs page as the "Base URL"
 * and baked into every code sample.
 */
export function resolveSiteUrl(): string {
  const h = headers();
  const host = h.get("x-forwarded-host") ?? h.get("host") ?? "";
  if (!host) return "http://localhost:3001";
  const isLocal = host.startsWith("localhost") || host.startsWith("127.");
  const proto = h.get("x-forwarded-proto") ?? (isLocal ? "http" : "https");
  return `${proto}://${host}`;
}

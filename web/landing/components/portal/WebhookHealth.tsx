"use client";

import Link from "next/link";

/**
 * Merchant-facing webhook delivery health, as `GET /api/v1/webhooks/health` returns it.
 *
 * Counts are terminal failures inside `window_hours` — the same window and status the
 * operator console counts — so the number a merchant sees on their dashboard and the
 * number an operator sees are one number. `retrying` is kept separate: a delivery still
 * being retried is at risk, not yet lost.
 */
export type WebhookFailureStore = {
  store_id: number;
  store_name: string;
  failed_count: number;
  retrying_count: number;
  last_failure_at: string | null;
};

export type WebhookHealthEndpoint = {
  endpoint_id: number;
  url: string;
  status: string;
  failed_count: number;
  retrying_count: number;
  affected_store_count: number;
  last_failure_at: string | null;
  last_error: string | null;
  last_response_status: number | null;
  stores: WebhookFailureStore[];
};

export type WebhookHealth = {
  window_hours: number;
  failed_count: number;
  retrying_count: number;
  affected_endpoint_count: number;
  affected_store_count: number;
  generated_at: string;
  endpoints: WebhookHealthEndpoint[];
};

export function healthByEndpoint(
  health: WebhookHealth | null | undefined,
): Record<string, WebhookHealthEndpoint> {
  const map: Record<string, WebhookHealthEndpoint> = {};
  for (const endpoint of health?.endpoints ?? []) {
    map[String(endpoint.endpoint_id)] = endpoint;
  }
  return map;
}

function affectedStoreNames(health: WebhookHealth, limit = 3): string {
  const names = Array.from(
    new Set(
      health.endpoints.flatMap((endpoint) =>
        endpoint.stores.map((store) => store.store_name),
      ),
    ),
  ).filter(Boolean);
  if (names.length === 0) return "";
  if (names.length <= limit) return names.join(", ");
  const remaining = names.length - limit;
  return `${names.slice(0, limit).join(", ")} +${remaining} more`;
}

/**
 * The dashboard-wide warning. Rendered by the shell so a merchant sees it on whatever
 * page they open, not only on Webhooks — the whole point is that they did not think to
 * look there. The Webhooks page suppresses it and shows the per-endpoint breakdown
 * instead, because two copies of the same warning on one screen is noise.
 */
export function WebhookHealthNotice({
  health,
  hidden = false,
}: {
  health: WebhookHealth | null;
  hidden?: boolean;
}) {
  if (hidden || !health || health.failed_count === 0) return null;

  const stores = affectedStoreNames(health);
  const endpointWord =
    health.affected_endpoint_count === 1 ? "One endpoint" : `${health.affected_endpoint_count} endpoints`;
  const deliveryWord = health.failed_count === 1 ? "delivery" : "deliveries";

  return (
    <div
      className={`cp-notice ${
        health.failed_count >= 5 ? "cp-notice-critical" : "cp-notice-warning"
      }`}
      role="alert"
    >
      <div className="cp-notice-inner">
        <div className="cp-notice-body">
          <div className="cp-notice-title">
            {health.failed_count} webhook {deliveryWord} failed in the last{" "}
            {health.window_hours} hours
          </div>
          <p className="cp-notice-text">
            {endpointWord} {health.affected_endpoint_count === 1 ? "is" : "are"}{" "}
            rejecting payment events
            {stores ? ` affecting ${stores}` : ""}. Payments are still recorded here,
            but they are not reaching your store&rsquo;s system. Check the endpoint URL
            and the HTTP response your server returns.
          </p>
        </div>
        <div className="cp-notice-actions">
          <Link className="dash-btn dash-btn-sm dash-btn-primary" href="/dashboard/webhooks">
            Review failures
          </Link>
        </div>
      </div>
    </div>
  );
}

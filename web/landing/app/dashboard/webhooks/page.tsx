"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";

type WebhookEndpoint = {
  id: string | number;
  url: string;
  events: string[];
  status: "active" | "disabled";
  // Returned by the API and derived from `status`. It used to be absent, which made
  // the edit form's "enabled" checkbox default to true and re-enable a disabled
  // endpoint on save.
  enabled: boolean;
  created_at?: string | null;
  signing_secret?: string;
  [k: string]: unknown;
};

// The events the platform actually raises in production. `payment.scanned` is
// emitted only by the development gateway and `payment.failed` has no producer at
// all, so offering either here would let a merchant subscribe to something that
// never arrives — and then build a handler for it.
const KNOWN_EVENTS = [
  "payment.completed",
  "payment.expired",
  "payment.superseded",
  "payment.reversed",
];

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function eventsDisplay(events: string[]): string {
  if (!events || events.length === 0 || events.includes("*")) {
    return "All events";
  }
  if (events.length <= 3) {
    return events.join(", ");
  }
  return `${events.length} events`;
}

function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
    }
  }, [value]);
  return (
    <div className="dash-copy-field">
      <div className="dash-copy-field-value">{value}</div>
      <button type="button" className="dash-copy-btn" onClick={onCopy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function RevealSecretModal({
  secret,
  onClose,
}: {
  secret: string;
  onClose: () => void;
}) {
  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">Webhook signing secret — save it now</h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <div className="dash-note">
            Verify webhook signatures with this secret. Only shown once per create or rotate.
          </div>
          <CopyField value={secret} />
          <div className="dash-toolbar">
            <div />
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={onClose}
            >
              Saved
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * Confirmation for the two actions that destroy a live credential: rotating the
 * signing secret (the deployed one stops verifying immediately) and deleting an
 * endpoint (which also removes its delivery log). Neither had a confirmation or an
 * in-flight guard, so a stray click — or a second click on a slow request — could
 * rotate twice and lose the first secret, or delete a row that had already gone.
 */
function ConfirmActionModal({
  title,
  body,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
}: {
  title: string;
  body: string;
  confirmLabel: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="dash-modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">{title}</h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onCancel}
            aria-label="Close"
            disabled={busy}
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <div className="dash-warn">{body}</div>
          <div className="dash-toolbar">
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </button>
            <button
              type="button"
              className="dash-btn dash-btn-danger"
              onClick={onConfirm}
              disabled={busy}
            >
              {busy ? "Working…" : confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

type WebhookTestResult = {
  http_status: number | null;
  response_body_preview: string | null;
  signature_valid_vs_local: boolean;
  headers_sent: Record<string, string>;
};

type WebhookDelivery = {
  delivery_id: number;
  event_id: string;
  event_type: string;
  http_status: number | null;
  attempt_count: number;
  response_body_preview: string | null;
  created_at: string;
  completed_at: string | null;
};

/**
 * Sends one event and shows exactly what came back.
 *
 * The point is the round trip: a merchant whose endpoint is silently rejecting
 * events otherwise has no way to tell whether the problem is the URL, the method,
 * a firewall or their own handler. This is the one button that answers that, and it
 * reports the raw status and body rather than a pass/fail verdict.
 */
function TestWebhookModal({
  endpoint,
  onClose,
}: {
  endpoint: WebhookEndpoint;
  onClose: () => void;
}) {
  const [sending, setSending] = useState(true);
  const [result, setResult] = useState<WebhookTestResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  const send = useCallback(async () => {
    setSending(true);
    setError(null);
    try {
      const res = await fetch(`/api/v1/webhooks/${endpoint.id}/test`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setResult((await res.json()) as WebhookTestResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSending(false);
    }
  }, [endpoint.id]);

  useEffect(() => {
    void send();
  }, [send]);

  const status = result?.http_status ?? null;
  const reached = result !== null && status !== null;
  const accepted = reached && status >= 200 && status < 300;

  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div className="dash-modal dash-modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">Test delivery</h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <div className="dash-note">
            We POST a sample <code>payment.completed</code> event to{" "}
            <code>{hostOf(endpoint.url)}</code>, signed with this endpoint&rsquo;s
            secret and sent the same way a real event is. No payment is created and
            nothing is charged.
          </div>

          {sending && <div className="dash-info">Sending…</div>}
          {error && <div className="dash-warn">{error}</div>}

          {result && !sending && (
            <>
              {accepted && (
                <div className="dash-info">
                  Your endpoint answered HTTP <strong>{status}</strong>. A real event
                  delivered this way is marked delivered and never retried.
                </div>
              )}
              {!reached && (
                <div className="dash-warn">
                  We could not reach {hostOf(endpoint.url)} at all — no HTTP response
                  came back. The usual causes are an address that is not public, a port
                  that is closed, or a destination that refuses POST.
                </div>
              )}
              {reached && !accepted && (
                <div className="dash-warn">
                  Your endpoint answered HTTP <strong>{status}</strong>. Only a 2xx
                  counts as delivered — a redirect, a 4xx and a 5xx all count as
                  failures, and a real event would be retried with a widening delay
                  before we gave up on it.
                </div>
              )}

              <table className="dash-table">
                <tbody>
                  <tr>
                    <td>
                      <div className="dash-stat-label">HTTP status</div>
                    </td>
                    <td>{status ?? "No response"}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Signature</div>
                    </td>
                    <td>
                      {result.signature_valid_vs_local
                        ? "Verified against this endpoint's secret before sending"
                        : "Did not verify — a fault on our side, please contact support"}
                    </td>
                  </tr>
                </tbody>
              </table>

              <div>
                <div className="dash-stat-label">Headers we sent</div>
                <pre className="dash-code-block">
                  {Object.entries(result.headers_sent)
                    .map(([key, value]) => `${key}: ${value}`)
                    .join("\n")}
                </pre>
              </div>

              <div>
                <div className="dash-stat-label">Your response body</div>
                {result.response_body_preview ? (
                  <pre className="dash-code-block">
                    {result.response_body_preview}
                  </pre>
                ) : (
                  <div className="dash-note">
                    No body came back. That is normal for a handler that only returns a
                    status code.
                  </div>
                )}
              </div>
            </>
          )}

          <div className="dash-toolbar">
            <div />
            <div className="dash-toolbar-filters">
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={onClose}
                disabled={sending}
              >
                Close
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-primary"
                onClick={() => void send()}
                disabled={sending}
              >
                {sending ? "Sending…" : "Send again"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * A delivery whose `completed_at` is still null is one we have not finished with —
 * it failed and is waiting for the next attempt. Only a 2xx is success, which is the
 * same rule the sender applies, so the pill cannot disagree with the retry behaviour.
 */
function deliveryState(delivery: WebhookDelivery): {
  className: string;
  label: string;
} {
  if (delivery.completed_at === null) {
    return { className: "dash-pill dash-pill-retrying", label: "retrying" };
  }
  if (
    delivery.http_status !== null &&
    delivery.http_status >= 200 &&
    delivery.http_status < 300
  ) {
    return { className: "dash-pill dash-pill-paid", label: "delivered" };
  }
  return { className: "dash-pill dash-pill-failed", label: "failed" };
}

function DeliveriesModal({
  endpoint,
  onClose,
}: {
  endpoint: WebhookEndpoint;
  onClose: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<WebhookDelivery[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/v1/webhooks/${endpoint.id}/deliveries?limit=50&page=1`,
        { credentials: "include" },
      );
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => []);
      setRows(Array.isArray(data) ? data : []);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [endpoint.id]);

  useEffect(() => {
    void load();
  }, [load]);

  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div className="dash-modal dash-modal-wide" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">Recent deliveries</h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <div className="dash-note">
            The last 50 events we tried to send to{" "}
            <code>{hostOf(endpoint.url)}</code>, newest first. A test send is not
            listed here — it goes straight out rather than through the queue that this
            log reads.
          </div>

          {error && <div className="dash-warn">{error}</div>}

          {loading ? (
            <div className="dash-info">Loading deliveries…</div>
          ) : rows.length === 0 ? (
            <div className="dash-empty">
              Nothing has been sent to this endpoint yet.
              <div className="dash-empty-desc">
                Events appear here once they happen. If you expected one, check that
                this endpoint is enabled and that the event type is in its list.
              </div>
            </div>
          ) : (
            <table className="dash-table">
              <thead>
                <tr>
                  <th>Event</th>
                  <th>Status</th>
                  <th>HTTP</th>
                  <th>Tries</th>
                  <th>Sent</th>
                  <th>Detail</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((delivery) => {
                  const state = deliveryState(delivery);
                  return (
                    <tr key={delivery.delivery_id}>
                      <td>
                        {delivery.event_type}
                        <div className="dash-code-mono">{delivery.event_id}</div>
                      </td>
                      <td>
                        <span className={state.className}>{state.label}</span>
                      </td>
                      <td>{delivery.http_status ?? "—"}</td>
                      <td>{delivery.attempt_count}</td>
                      <td>{formatDateTime(delivery.created_at)}</td>
                      <td>
                        {delivery.response_body_preview ? (
                          <span className="dash-code-mono">
                            {delivery.response_body_preview}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}

          <div className="dash-toolbar">
            <div />
            <div className="dash-toolbar-filters">
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={onClose}
              >
                Close
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => void load()}
                disabled={loading}
              >
                {loading ? "Refreshing…" : "Refresh"}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function EditEndpointModal({
  endpoint,
  onClose,
  onSaved,
}: {
  endpoint: WebhookEndpoint | null;
  onClose: () => void;
  onSaved: (signingSecret?: string) => void;
}) {
  const isEditing = endpoint !== null;

  const [url, setUrl] = useState(endpoint?.url || "");
  const [events, setEvents] = useState<string[]>(
    endpoint && endpoint.events && !endpoint.events.includes("*")
      ? endpoint.events
      : [],
  );
  const [enabled, setEnabled] = useState<boolean>(endpoint?.enabled ?? true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggleEvent = (ev: string) => {
    setEvents((prev) =>
      prev.includes(ev) ? prev.filter((e) => e !== ev) : [...prev, ev],
    );
  };

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      if (!url.trim()) {
        setError("URL is required.");
        return;
      }
      setSubmitting(true);
      try {
        // `enabled` is a PATCH field only. Creating always produces an active
        // endpoint, and `WebhookCreate` forbids extra fields, so sending it here is
        // not merely redundant — it is a 422 that stopped every create from the
        // portal, with the offending field never named in the message the merchant
        // sees.
        const body: Record<string, unknown> = {
          url: url.trim(),
        };
        if (events.length > 0) {
          body.events = events;
        } else {
          body.events = ["*"];
        }
        if (isEditing) {
          body.enabled = enabled;
        }

        let res: Response;
        if (isEditing) {
          res = await fetch(`/api/v1/webhooks/${endpoint!.id}`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
        } else {
          res = await fetch("/api/v1/webhooks", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
        }
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as WebhookEndpoint;
        if (!isEditing && data.signing_secret) {
          onSaved(data.signing_secret);
        } else {
          onSaved();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [url, events, enabled, isEditing, endpoint, onSaved],
  );

  return (
    <div className="dash-modal-backdrop" onClick={onClose}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">
            {isEditing ? "Edit endpoint" : "Add endpoint"}
          </h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <form className="dash-form" onSubmit={onSubmit}>
            <div className="dash-field">
              <label htmlFor="wh-url">Endpoint URL</label>
              <input
                id="wh-url"
                type="url"
                className="dash-input"
                placeholder="https://your-app.com/webhooks/chmabapay"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                required
              />
            </div>

            <div className="dash-field">
              <label>Events to receive</label>
              <div className="dash-form-row">
                {KNOWN_EVENTS.map((ev) => {
                  const checked = events.includes(ev);
                  return (
                    <label key={ev} className="dash-field">
                      <div className="dash-toolbar-filters">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleEvent(ev)}
                        />
                        <span>{ev}</span>
                      </div>
                    </label>
                  );
                })}
              </div>
              <div className="dash-note">
                If no events are selected, all events (*) will be sent.
              </div>
            </div>

            {/* Creating always produces an active endpoint, so this toggle only has
                an effect when editing an existing one. */}
            {isEditing && (
              <div className="dash-field">
                <div className="dash-toolbar-filters">
                  <input
                    id="wh-enabled"
                    type="checkbox"
                    checked={enabled}
                    onChange={(e) => setEnabled(e.target.checked)}
                  />
                  <label htmlFor="wh-enabled">Enabled</label>
                </div>
              </div>
            )}

            {error && <div className="dash-form-alert dash-form-alert-error">{error}</div>}

            <div className="dash-toolbar">
              <div />
              <div className="dash-toolbar-filters">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={onClose}
                  disabled={submitting}
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="dash-btn dash-btn-primary"
                  disabled={submitting}
                >
                  {isEditing ? "Save changes" : "Create endpoint"}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

export default function DashboardWebhooksPage() {
  const [loading, setLoading] = useState(true);
  const [endpoints, setEndpoints] = useState<WebhookEndpoint[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [editing, setEditing] = useState<WebhookEndpoint | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [revealSecret, setRevealSecret] = useState<string | null>(null);
  const [testing, setTesting] = useState<WebhookEndpoint | null>(null);
  const [viewingDeliveries, setViewingDeliveries] =
    useState<WebhookEndpoint | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<{
    kind: "rotate" | "delete";
    endpoint: WebhookEndpoint;
  } | null>(null);

  const fetchEndpoints = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/api/v1/webhooks", { credentials: "include" });
      // A failed read used to leave `endpoints` empty, which the page then rendered as
      // "No webhook endpoints yet." — the same screen as a fresh account.
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      const items: WebhookEndpoint[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.items)
          ? data.items
          : Array.isArray(data?.data)
            ? data.data
            : [];
      setEndpoints(items);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchEndpoints();
  }, [fetchEndpoints]);

  const handleSaved = useCallback(
    (signingSecret?: string) => {
      setShowAdd(false);
      setEditing(null);
      void fetchEndpoints();
      if (signingSecret) {
        setRevealSecret(signingSecret);
      }
    },
    [fetchEndpoints],
  );

  const handleDelete = useCallback(
    async (id: string | number) => {
      setActionError(null);
      setBusyId(String(id));
      try {
        const res = await fetch(`/api/v1/webhooks/${id}`, {
          method: "DELETE",
          credentials: "include",
        });
        if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
        void fetchEndpoints();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyId(null);
      }
    },
    [fetchEndpoints],
  );

  const handleToggleEnabled = useCallback(
    async (ep: WebhookEndpoint) => {
      setActionError(null);
      try {
        const nextEnabled = ep.status === "active" ? false : true;
        const res = await fetch(`/api/v1/webhooks/${ep.id}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ enabled: nextEnabled }),
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        void fetchEndpoints();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      }
    },
    [fetchEndpoints],
  );

  const handleRotateSecret = useCallback(
    async (id: string | number) => {
      setActionError(null);
      setBusyId(String(id));
      try {
        const res = await fetch(`/api/v1/webhooks/${id}/rotate-secret`, {
          method: "POST",
          credentials: "include",
        });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as { signing_secret?: string };
        if (data.signing_secret) {
          setRevealSecret(data.signing_secret);
        }
        void fetchEndpoints();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusyId(null);
      }
    },
    [fetchEndpoints],
  );

  const sortedEndpoints = [...endpoints].sort((a, b) => {
    const ta = new Date(a.created_at || 0).getTime();
    const tb = new Date(b.created_at || 0).getTime();
    return tb - ta;
  });

  return (
    <>
      <div>
        <div className="dash-page-head">
          <div>
            <h2 className="dash-page-title">Webhooks</h2>
            <div className="dash-page-subtitle">
              Listen for payment events in real time
            </div>
          </div>
        </div>

        {actionError && (
          <div className="dash-form-alert dash-form-alert-error">{actionError}</div>
        )}

        {!loading && sortedEndpoints.length > 0 && (
          <div className="dash-toolbar">
            <div />
            <button
              type="button"
              className="dash-btn dash-btn-primary"
              onClick={() => setShowAdd(true)}
            >
              + Add endpoint
            </button>
          </div>
        )}

        {loading ? (
          <div className="dash-info">Loading webhooks…</div>
        ) : loadError ? (
          <div className="dash-warn">
            Your webhook endpoints could not be loaded, so this list is unknown
            rather than empty. Every endpoint you already created is still
            delivering.
            <div className="dash-empty-cta-row">
              <button
                type="button"
                className="dash-btn dash-btn-secondary dash-btn-sm"
                onClick={() => void fetchEndpoints()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : sortedEndpoints.length === 0 ? (
          <div className="dash-empty">
            No webhook endpoints yet.
            <div className="dash-empty-desc">
              Create an endpoint to receive payment events as they happen.
              <div className="dash-empty-cta-row">
                <button
                  type="button"
                  className="dash-btn dash-btn-primary dash-btn-sm"
                  onClick={() => setShowAdd(true)}
                >
                  + Add endpoint
                </button>
              </div>
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>URL</th>
                <th>Events</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {sortedEndpoints.map((ep) => {
                const isActive = ep.status === "active";
                const rowBusy = busyId === String(ep.id);
                return (
                  <tr key={String(ep.id)}>
                    <td>
                      <code>{ep.url}</code>
                    </td>
                    <td>
                      <span className="dash-badge dash-badge-muted">
                        {eventsDisplay(ep.events)}
                      </span>
                    </td>
                    <td>
                      <span
                        className={
                          isActive
                            ? "dash-pill dash-pill-paid"
                            : "dash-pill dash-pill-pending"
                        }
                      >
                        {isActive ? "active" : "disabled"}
                      </span>
                    </td>
                    <td>{formatDate(ep.created_at)}</td>
                    <td>
                      <div className="dash-toolbar-filters">
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => setEditing(ep)}
                        >
                          Edit
                        </button>
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => setTesting(ep)}
                        >
                          Send test
                        </button>
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => setViewingDeliveries(ep)}
                        >
                          Deliveries
                        </button>
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => setPendingAction({ kind: "rotate", endpoint: ep })}
                          disabled={rowBusy}
                        >
                          {rowBusy ? "…" : "Rotate secret"}
                        </button>
                        <button
                          type="button"
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          onClick={() => handleToggleEnabled(ep)}
                        >
                          {isActive ? "Disable" : "Enable"}
                        </button>
                        <button
                          type="button"
                          className="dash-btn dash-btn-danger dash-btn-sm"
                          onClick={() => setPendingAction({ kind: "delete", endpoint: ep })}
                          disabled={rowBusy}
                        >
                          {rowBusy ? "…" : "Delete"}
                        </button>
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {(showAdd || editing !== null) && (
        <EditEndpointModal
          endpoint={editing}
          onClose={() => {
            setShowAdd(false);
            setEditing(null);
          }}
          onSaved={handleSaved}
        />
      )}

      {revealSecret !== null && (
        <RevealSecretModal
          secret={revealSecret}
          onClose={() => setRevealSecret(null)}
        />
      )}

      {pendingAction !== null && (
        <ConfirmActionModal
          title={
            pendingAction.kind === "rotate"
              ? "Rotate signing secret?"
              : "Delete this endpoint?"
          }
          body={
            pendingAction.kind === "rotate"
              ? "Rotating invalidates the secret this endpoint signs deliveries with. Every webhook will fail signature verification until you deploy the new secret to your server."
              : "Deleting this endpoint stops its deliveries and removes its delivery log. This cannot be undone."
          }
          confirmLabel={
            pendingAction.kind === "rotate" ? "Rotate secret" : "Delete endpoint"
          }
          busy={busyId === String(pendingAction.endpoint.id)}
          onCancel={() => setPendingAction(null)}
          onConfirm={async () => {
            if (pendingAction.kind === "rotate") {
              await handleRotateSecret(pendingAction.endpoint.id);
            } else {
              await handleDelete(pendingAction.endpoint.id);
            }
            setPendingAction(null);
          }}
        />
      )}

      {testing !== null && (
        <TestWebhookModal endpoint={testing} onClose={() => setTesting(null)} />
      )}

      {viewingDeliveries !== null && (
        <DeliveriesModal
          endpoint={viewingDeliveries}
          onClose={() => setViewingDeliveries(null)}
        />
      )}
    </>
  );
}

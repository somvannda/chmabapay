"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";

type WebhookEndpoint = {
  id: string | number;
  url: string;
  events: string[];
  status: "active" | "disabled";
  enabled?: boolean;
  created_at?: string | null;
  signing_secret?: string;
  [k: string]: unknown;
};

const KNOWN_EVENTS = [
  "payment.completed",
  "payment.scanned",
  "payment.expired",
  "payment.failed",
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
  const [enabled, setEnabled] = useState<boolean>(endpoint?.enabled !== false);
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
        const body: Record<string, unknown> = {
          url: url.trim(),
          enabled,
        };
        if (events.length > 0) {
          body.events = events;
        } else {
          body.events = ["*"];
        }

        let res: Response;
        if (isEditing) {
          res = await fetch(`/v1/webhooks/${endpoint!.id}`, {
            method: "PATCH",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          });
        } else {
          res = await fetch("/v1/webhooks", {
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

  const [editing, setEditing] = useState<WebhookEndpoint | null>(null);
  const [showAdd, setShowAdd] = useState(false);
  const [revealSecret, setRevealSecret] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const fetchEndpoints = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/v1/webhooks", { credentials: "include" });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        const items: WebhookEndpoint[] = Array.isArray(data)
          ? data
          : Array.isArray(data?.items)
            ? data.items
            : Array.isArray(data?.data)
              ? data.data
              : [];
        setEndpoints(items);
      }
    } catch {
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
      try {
        const res = await fetch(`/v1/webhooks/${id}`, {
          method: "DELETE",
          credentials: "include",
        });
        if (!res.ok && res.status !== 204) throw new Error(`HTTP ${res.status}`);
        void fetchEndpoints();
      } catch (err) {
        setActionError(err instanceof Error ? err.message : String(err));
      }
    },
    [fetchEndpoints],
  );

  const handleToggleEnabled = useCallback(
    async (ep: WebhookEndpoint) => {
      setActionError(null);
      try {
        const nextEnabled = ep.status === "active" ? false : true;
        const res = await fetch(`/v1/webhooks/${ep.id}`, {
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
      try {
        const res = await fetch(`/v1/webhooks/${id}/rotate-secret`, {
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
                          onClick={() => handleRotateSecret(ep.id)}
                        >
                          Rotate secret
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
                          onClick={() => handleDelete(ep.id)}
                        >
                          Delete
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
    </>
  );
}

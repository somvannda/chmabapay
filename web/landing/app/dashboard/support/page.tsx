"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";

/* ------------------------------------------------------------------ *
 * The contract, from `src/chmabapay/routers/support.py`
 * ------------------------------------------------------------------ */

type SupportMessage = {
  id: number;
  // "merchant" | "operator" (`models.SUPPORT_AUTHOR_*`).
  author_kind: string;
  body: string;
  created_at: string;
};

type SupportRequest = {
  // The model's `public_id`, exposed as `id` by `SupportRequestOut`.
  id: string;
  subject: string;
  category: string;
  // "open" | "pending" | "resolved" (`models.SUPPORT_STATUSES`).
  status: string;
  // "standard" | "priority" (`models.SUPPORT_PRIORITIES`), copied from the plan at open time.
  priority: string;
  first_response_at: string | null;
  resolved_at: string | null;
  created_at: string;
  /**
   * The first-response target the account's plan promises, published by the API so this
   * page states the number the platform measures against instead of a hardcoded one.
   * `null` is a best-effort plan, not an error.
   */
  response_target_hours: number | null;
  messages: SupportMessage[];
};

/**
 * The closed set `SupportRequestCreate.category` accepts (`models.SUPPORT_CATEGORIES`),
 * in the order the API lists them. Labels are the one bit of copy that is not the API's.
 */
const CATEGORY_OPTIONS = [
  { value: "billing", label: "Billing" },
  { value: "payment", label: "Payments" },
  { value: "integration", label: "Integration" },
  { value: "account", label: "Account" },
  { value: "other", label: "Other" },
];

const CATEGORY_LABELS: Record<string, string> = {
  billing: "Billing",
  payment: "Payments",
  integration: "Integration",
  account: "Account",
  other: "Other",
};

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
  });
}

function categoryLabel(value: string): string {
  return CATEGORY_LABELS[value] ?? value;
}

function priorityBadge(priority: string): { className: string; label: string } {
  return priority === "priority"
    ? { className: "dash-badge dash-badge-violet", label: "Priority" }
    : { className: "dash-badge dash-badge-muted", label: "Standard" };
}

/**
 * `pending` means an operator has answered and the thread is back with the merchant;
 * `open` means the platform owes an answer. `resolved` owes nothing either way, so the
 * same green the rest of the portal uses for "done" fits it.
 */
function statusPill(status: string): { className: string; label: string } {
  if (status === "resolved") {
    return { className: "dash-pill dash-pill-paid", label: "resolved" };
  }
  if (status === "pending") {
    return { className: "dash-pill dash-pill-scanned", label: "pending" };
  }
  return { className: "dash-pill dash-pill-pending", label: "open" };
}

/** Whose turn it is — the merchant's question the list is really answering. */
function waitingOn(status: string): string {
  if (status === "resolved") return "—";
  if (status === "pending") return "You";
  return "Platform";
}

function authorLabel(authorKind: string): string {
  return authorKind === "operator" ? "Support" : "You";
}

/**
 * The response-time target, stated from the API's own field. A `null` target is a plan
 * with no stated first-response time, so the page promises best effort rather than
 * inventing a number.
 */
function responseTargetCopy(hours: number | null): string {
  if (hours === null) {
    return "We answer every request as soon as we can. Your plan does not state a first-response time.";
  }
  const unit = hours === 1 ? "hour" : "hours";
  return `We aim to give a first response within ${hours} ${unit} — the target on your plan.`;
}

function NewRequestModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: () => void;
}) {
  const [subject, setSubject] = useState("");
  // Mirrors `SupportRequestCreate.category`'s own default, so the pre-selected value is
  // the one the server would have chosen had the field been omitted.
  const [category, setCategory] = useState("other");
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const onSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setError(null);
      if (!subject.trim() || !body.trim()) {
        setError("Add a subject and a message so we know what to look at.");
        return;
      }
      setSubmitting(true);
      try {
        const res = await fetch("/api/v1/support/requests", {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            subject: subject.trim(),
            category,
            body: body.trim(),
          }),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        onCreated();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSubmitting(false);
      }
    },
    [subject, category, body, onCreated],
  );

  return (
    <div className="dash-modal-backdrop" onClick={submitting ? undefined : onClose}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">New support request</h3>
          <button
            type="button"
            className="dash-modal-close"
            onClick={onClose}
            aria-label="Close"
            disabled={submitting}
          >
            ×
          </button>
        </div>
        <div className="dash-modal-body">
          <form className="dash-form" onSubmit={onSubmit}>
            <div className="dash-field">
              <label htmlFor="sup-subject">Subject</label>
              <input
                id="sup-subject"
                className="dash-input"
                type="text"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                maxLength={200}
                placeholder="A payment I cannot match"
                required
              />
            </div>

            <div className="dash-field">
              <label htmlFor="sup-category">Category</label>
              <select
                id="sup-category"
                className="dash-select"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                {CATEGORY_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <div className="dash-hint">
                Pick the closest match — it routes your request to the right queue.
              </div>
            </div>

            <div className="dash-field">
              <label htmlFor="sup-body">Message</label>
              <textarea
                id="sup-body"
                className="dash-textarea"
                value={body}
                onChange={(e) => setBody(e.target.value)}
                maxLength={8000}
                rows={6}
                placeholder="What happened, and which payment or store it concerns."
                required
              />
              <div className="dash-hint">
                Include a payment id or store reference if you have one.
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
                  {submitting ? "Sending…" : "Open request"}
                </button>
              </div>
            </div>
          </form>
        </div>
      </div>
    </div>
  );
}

/**
 * Closing carries the merchant's side of the thread to a terminal state it cannot come
 * back from, so it is confirmed rather than fired from a stray click on a row action.
 */
function CloseRequestModal({
  subject,
  busy,
  onConfirm,
  onCancel,
}: {
  subject: string;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="dash-modal-backdrop" onClick={busy ? undefined : onCancel}>
      <div className="dash-modal" onClick={(e) => e.stopPropagation()}>
        <div className="dash-modal-head">
          <h3 className="dash-modal-title">Close this request?</h3>
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
          <div className="dash-warn">
            Closing &ldquo;{subject}&rdquo; resolves it from your side and it cannot be
            reopened from here — the thread stays readable, but talking to us again means
            a new request. If you are still waiting on an answer, reply instead.
          </div>
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
              {busy ? "Closing…" : "Close request"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

function RequestThreadModal({
  publicId,
  onClose,
  onChanged,
}: {
  publicId: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [request, setRequest] = useState<SupportRequest | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [replyBody, setReplyBody] = useState("");
  const [replyBusy, setReplyBusy] = useState(false);
  const [replyError, setReplyError] = useState<string | null>(null);

  const [confirmingClose, setConfirmingClose] = useState(false);
  const [closeBusy, setCloseBusy] = useState(false);
  const [closeError, setCloseError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch(`/api/v1/support/requests/${publicId}`, {
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setRequest((await res.json()) as SupportRequest);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [publicId]);

  useEffect(() => {
    void load();
  }, [load]);

  const sendReply = useCallback(async () => {
    if (!replyBody.trim()) return;
    setReplyError(null);
    setReplyBusy(true);
    try {
      const res = await fetch(`/api/v1/support/requests/${publicId}/reply`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: replyBody.trim() }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      // The reply route answers with the whole updated thread, so the modal redraws from
      // the server's record rather than appending locally and hoping it matches.
      setRequest((await res.json()) as SupportRequest);
      setReplyBody("");
      onChanged();
    } catch (e) {
      setReplyError(e instanceof Error ? e.message : String(e));
    } finally {
      setReplyBusy(false);
    }
  }, [publicId, replyBody, onChanged]);

  const closeRequest = useCallback(async () => {
    setCloseError(null);
    setCloseBusy(true);
    try {
      const res = await fetch(`/api/v1/support/requests/${publicId}/close`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setRequest((await res.json()) as SupportRequest);
      setConfirmingClose(false);
      onChanged();
    } catch (e) {
      setCloseError(e instanceof Error ? e.message : String(e));
    } finally {
      setCloseBusy(false);
    }
  }, [publicId, onChanged]);

  const resolved = request?.status === "resolved";
  const status = request ? statusPill(request.status) : null;
  const priority = request ? priorityBadge(request.priority) : null;

  return (
    <>
      <div className="dash-modal-backdrop" onClick={onClose}>
        <div className="dash-modal dash-modal-wide" onClick={(e) => e.stopPropagation()}>
          <div className="dash-modal-head">
            <h3 className="dash-modal-title">
              {request ? request.subject : "Support request"}
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
            {loading ? (
              <div className="dash-info">Loading the thread…</div>
            ) : loadError ? (
              <div className="dash-warn">
                This request could not be loaded, so what is on screen is unknown rather
                than empty.
                <div className="dash-empty-cta-row">
                  <button
                    type="button"
                    className="dash-btn dash-btn-secondary dash-btn-sm"
                    onClick={() => void load()}
                  >
                    Retry
                  </button>
                </div>
              </div>
            ) : request && status && priority ? (
              <>
                <div className="dash-toolbar-filters">
                  <span className={status.className}>{status.label}</span>
                  <span className={priority.className}>{priority.label}</span>
                  <span className="dash-badge dash-badge-muted">
                    {categoryLabel(request.category)}
                  </span>
                </div>

                <div className="dash-note">
                  Opened {formatDateTime(request.created_at)} · waiting on{" "}
                  {waitingOn(request.status)}.{" "}
                  {request.first_response_at
                    ? `First answered ${formatDateTime(request.first_response_at)}. `
                    : ""}
                  {responseTargetCopy(request.response_target_hours)}
                </div>

                <div className="dash-thread">
                  {request.messages.map((message) => {
                    const operator = message.author_kind === "operator";
                    return (
                      <div
                        key={message.id}
                        className={
                          operator
                            ? "dash-thread-msg dash-thread-msg-operator"
                            : "dash-thread-msg"
                        }
                      >
                        <div className="dash-thread-meta">
                          <span className="dash-thread-author">
                            {authorLabel(message.author_kind)}
                          </span>
                          <span className="dash-thread-time">
                            {formatDateTime(message.created_at)}
                          </span>
                        </div>
                        <p className="dash-thread-body">{message.body}</p>
                      </div>
                    );
                  })}
                </div>

                {resolved ? (
                  <div className="dash-info">
                    This request is resolved
                    {request.resolved_at
                      ? ` on ${formatDateTime(request.resolved_at)}`
                      : ""}
                    , so the thread is read-only. Open a new request if you need
                    anything else.
                  </div>
                ) : (
                  <div className="dash-thread-form">
                    <div className="dash-field">
                      <label htmlFor="sup-reply">Reply</label>
                      <textarea
                        id="sup-reply"
                        className="dash-textarea"
                        value={replyBody}
                        onChange={(e) => setReplyBody(e.target.value)}
                        maxLength={8000}
                        rows={4}
                        placeholder="Add anything that helps us answer."
                      />
                    </div>

                    {replyError && (
                      <div className="dash-form-alert dash-form-alert-error">
                        {replyError}
                      </div>
                    )}
                    {closeError && (
                      <div className="dash-form-alert dash-form-alert-error">
                        {closeError}
                      </div>
                    )}

                    <div className="dash-toolbar">
                      <button
                        type="button"
                        className="dash-btn dash-btn-danger"
                        onClick={() => setConfirmingClose(true)}
                        disabled={replyBusy || closeBusy}
                      >
                        Close request
                      </button>
                      <button
                        type="button"
                        className="dash-btn dash-btn-primary"
                        onClick={() => void sendReply()}
                        disabled={replyBusy || !replyBody.trim()}
                      >
                        {replyBusy ? "Sending…" : "Send reply"}
                      </button>
                    </div>
                  </div>
                )}
              </>
            ) : null}
          </div>
        </div>
      </div>

      {confirmingClose && request && (
        <CloseRequestModal
          subject={request.subject}
          busy={closeBusy}
          onConfirm={() => void closeRequest()}
          onCancel={() => setConfirmingClose(false)}
        />
      )}
    </>
  );
}

export default function DashboardSupportPage() {
  const [loading, setLoading] = useState(true);
  const [requests, setRequests] = useState<SupportRequest[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  // The account's own target, read from the list response rather than derived from a
  // loaded request: an account that has never opened one still needs to be told what its
  // plan promises, and it has no request to read the number from.
  const [targetHours, setTargetHours] = useState<number | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);

  const fetchRequests = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/api/v1/support/requests", { credentials: "include" });
      // A failed read used to be indistinguishable from a fresh account if `requests`
      // were simply left empty, so it raises instead of falling through to the empty state.
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      // The route answers `{ data: [...], response_target_hours }`, already newest first
      // (`id desc`).
      setRequests(
        Array.isArray(data?.data) ? (data.data as SupportRequest[]) : [],
      );
      setTargetHours(
        typeof data?.response_target_hours === "number"
          ? (data.response_target_hours as number)
          : null,
      );
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchRequests();
  }, [fetchRequests]);

  return (
    <>
      <div>
        <div className="dash-page-head">
          <div>
            <h2 className="dash-page-title">Support</h2>
            <div className="dash-page-subtitle">
              Open a request, follow our replies and close the thread
            </div>
          </div>
        </div>

        {/* Stated from `response_target_hours`, never a number typed into this file. It is
            withheld while the list read is unknown or failed, because an unread target is
            not a target of "none". */}
        {!loading && !loadError && (
          <div className="dash-info">{responseTargetCopy(targetHours)}</div>
        )}

        {!loading && !loadError && requests.length > 0 && (
          <div className="dash-toolbar">
            <div />
            <button
              type="button"
              className="dash-btn dash-btn-primary"
              onClick={() => setShowNew(true)}
            >
              + New request
            </button>
          </div>
        )}

        {loading ? (
          <div className="dash-info">Loading your requests…</div>
        ) : loadError ? (
          <div className="dash-warn">
            Your support requests could not be loaded, so this list is unknown rather
            than empty. Every request you already opened is still with us.
            <div className="dash-empty-cta-row">
              <button
                type="button"
                className="dash-btn dash-btn-secondary dash-btn-sm"
                onClick={() => void fetchRequests()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : requests.length === 0 ? (
          <div className="dash-empty">
            No support requests yet.
            <div className="dash-empty-desc">
              Open one and we will answer it here — your view of the thread is on this
              page.
              <div className="dash-empty-cta-row">
                <button
                  type="button"
                  className="dash-btn dash-btn-primary dash-btn-sm"
                  onClick={() => setShowNew(true)}
                >
                  + New request
                </button>
              </div>
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Subject</th>
                <th>Category</th>
                <th>Status</th>
                <th>Waiting on</th>
                <th>Priority</th>
                <th>Opened</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {requests.map((r) => {
                const status = statusPill(r.status);
                const priority = priorityBadge(r.priority);
                return (
                  <tr key={r.id}>
                    <td>
                      {r.subject}
                      <div className="dash-code-mono">{r.id}</div>
                    </td>
                    <td>{categoryLabel(r.category)}</td>
                    <td>
                      <span className={status.className}>{status.label}</span>
                    </td>
                    <td>{waitingOn(r.status)}</td>
                    <td>
                      <span className={priority.className}>{priority.label}</span>
                    </td>
                    <td>{formatDateTime(r.created_at)}</td>
                    <td>
                      <button
                        type="button"
                        className="dash-btn dash-btn-secondary dash-btn-sm"
                        onClick={() => setOpenId(r.id)}
                      >
                        Open
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>

      {showNew && (
        <NewRequestModal
          onClose={() => setShowNew(false)}
          onCreated={() => {
            setShowNew(false);
            void fetchRequests();
          }}
        />
      )}

      {openId !== null && (
        <RequestThreadModal
          publicId={openId}
          onClose={() => setOpenId(null)}
          onChanged={() => void fetchRequests()}
        />
      )}
    </>
  );
}

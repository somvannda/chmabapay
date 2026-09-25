"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type SupportMessage = {
  id: number;
  author_kind: string;
  author_account_id: number;
  body: string;
  created_at: string;
};

type SupportDetail = {
  id: string;
  subject: string;
  category: string;
  status: string;
  priority: string;
  account_id: number;
  account_email: string | null;
  account_name: string | null;
  assigned_admin_account_id: number | null;
  first_response_at: string | null;
  resolved_at: string | null;
  created_at: string;
  updated_at: string;
  response_target_hours: number | null;
  target_breached: boolean;
  messages: SupportMessage[];
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

function statusPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "resolved":
      return { className: "dash-pill dash-pill-paid", label: "resolved" };
    case "pending":
      return { className: "dash-pill dash-pill-pending", label: "waiting" };
    case "open":
      return { className: "dash-pill dash-pill-scanned", label: "open" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "open",
      };
  }
}

function priorityPill(priority: string | null | undefined): {
  className: string;
  label: string;
} {
  return (priority || "").toLowerCase() === "priority"
    ? { className: "dash-pill dash-pill-superseded", label: "priority" }
    : { className: "dash-pill dash-pill-pending", label: "standard" };
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <tr>
      <td>
        <div className="dash-stat-label">{label}</div>
      </td>
      <td>{children}</td>
    </tr>
  );
}

function Dash({ value }: { value: string | null | undefined }) {
  return <>{value ? value : "—"}</>;
}

export default function AdminSupportDetailPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;
  const { notify } = useToast();

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<SupportDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const [reply, setReply] = useState("");
  const [assigneeInput, setAssigneeInput] = useState("");
  const [closeOpen, setCloseOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    setNotFound(false);
    try {
      const res = await apiFetch(`/api/v1/admin/support/requests/${publicId}`, {
        credentials: "include",
      });
      if (res.status === 404) {
        setNotFound(true);
        return;
      }
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as SupportDetail;
      setDetail(data);
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [publicId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Status and assignment share one PATCH. A null `assigned_admin_account_id` is
  // meaningful — it unassigns — so it is always sent as a real field, never omitted.
  const applyPatch = useCallback(
    async (payload: {
      status?: string;
      assigned_admin_account_id?: number | null;
    }) => {
      const res = await apiFetch(`/api/v1/admin/support/requests/${publicId}`, {
        method: "PATCH",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      return (await res.json()) as SupportDetail;
    },
    [publicId],
  );

  const changeStatus = useCallback(
    async (next: string) => {
      setBusy("status");
      try {
        const data = await applyPatch({ status: next });
        setDetail(data);
        setCloseOpen(false);
        notify(
          next === "resolved"
            ? "Request closed."
            : next === "pending"
              ? "Marked as waiting on the merchant."
              : "Request reopened.",
        );
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), "error");
      } finally {
        setBusy(null);
      }
    },
    [applyPatch, notify],
  );

  const sendReply = useCallback(async () => {
    const body = reply.trim();
    if (!body) {
      notify("Write a reply before sending.", "error");
      return;
    }
    setBusy("reply");
    try {
      const res = await apiFetch(
        `/api/v1/admin/support/requests/${publicId}/reply`,
        {
          method: "POST",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ body }),
        },
      );
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as SupportDetail;
      setDetail(data);
      setReply("");
      notify("Reply sent. The merchant is emailed a copy.");
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [publicId, reply, notify]);

  const assign = useCallback(async () => {
    const value = assigneeInput.trim();
    if (!/^[0-9]+$/.test(value)) {
      notify("Enter the numeric account ID of a platform admin.", "error");
      return;
    }
    setBusy("assign");
    try {
      const data = await applyPatch({
        assigned_admin_account_id: Number(value),
      });
      setDetail(data);
      setAssigneeInput("");
      notify("Request assigned.");
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [assigneeInput, applyPatch, notify]);

  const unassign = useCallback(async () => {
    setBusy("assign");
    try {
      const data = await applyPatch({ assigned_admin_account_id: null });
      setDetail(data);
      notify("Request unassigned.");
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [applyPatch, notify]);

  const sp = statusPill(detail?.status);
  const pp = priorityPill(detail?.priority);

  return (
    <>
      <div className="dash-page-head">
        <div>
          <div className="dash-toolbar-filters">
            <Link className="dash-link-btn" href="/support">
              ← Back to support
            </Link>
          </div>
          <h1 className="dash-page-title">
            {detail ? detail.id : "Support request"}
          </h1>
          <div className="dash-page-subtitle">
            {detail
              ? `${detail.subject} · ${
                  detail.account_email || `Account #${detail.account_id}`
                }`
              : "Support request detail"}
          </div>
        </div>
      </div>

      {errorMsg && (
        <div className="dash-warn">
          {errorMsg}{" "}
          <button
            type="button"
            className="dash-btn dash-btn-secondary dash-btn-sm"
            onClick={() => void load()}
          >
            Retry
          </button>
        </div>
      )}

      {loading ? (
        <div className="dash-info">Loading support request…</div>
      ) : errorMsg && !detail ? (
        <div className="dash-empty">
          Could not load this support request.
          <div className="dash-empty-desc">
            The request failed, so this is not a missing request. Use Retry above,
            or reload the page.
          </div>
        </div>
      ) : notFound || !detail ? (
        <div className="dash-empty">
          Support request not found.
          <div className="dash-empty-desc">
            Check the request ID from the merchant&rsquo;s message.{" "}
            <Link className="dash-link-btn" href="/support">
              Return to support
            </Link>
          </div>
        </div>
      ) : (
        <>
          {detail.target_breached && (
            <div className="dash-breach" role="alert">
              <strong>First-response target breached.</strong> This request has been
              open for more than {detail.response_target_hours} hours without a
              reply from the platform. Answer it before anything else in the queue.
            </div>
          )}

          <div className="dash-panels">
            <div className="dash-panel">
              <div className="dash-panel-title">Request</div>
              <table className="dash-table">
                <tbody>
                  <Row label="Subject">{detail.subject}</Row>
                  <Row label="Account">
                    <Link
                      className="dash-link-btn"
                      href={`/accounts/${detail.account_id}`}
                    >
                      {detail.account_email || `Account #${detail.account_id}`}
                    </Link>
                    {detail.account_name && (
                      <div className="dash-sub">{detail.account_name}</div>
                    )}
                  </Row>
                  <Row label="Category">
                    <span className="dash-badge dash-badge-muted">
                      {detail.category}
                    </span>
                  </Row>
                  <Row label="Status">
                    <span className={sp.className}>{sp.label}</span>
                  </Row>
                  <Row label="Priority">
                    <span className={pp.className}>{pp.label}</span>
                  </Row>
                  <Row label="Opened">{formatDateTime(detail.created_at)}</Row>
                  <Row label="First response">
                    {detail.target_breached ? (
                      <>
                        <span className="dash-breach-flag">Breach</span>
                        <div className="dash-sub">
                          Past the {detail.response_target_hours}h target, still
                          unanswered
                        </div>
                      </>
                    ) : detail.first_response_at ? (
                      formatDateTime(detail.first_response_at)
                    ) : detail.response_target_hours !== null ? (
                      <span className="dash-sub">
                        Within the {detail.response_target_hours}h target
                      </span>
                    ) : (
                      <span className="dash-sub">Best effort (no target)</span>
                    )}
                  </Row>
                  <Row label="Resolved">
                    <Dash value={formatDateTime(detail.resolved_at)} />
                  </Row>
                  <Row label="Last updated">
                    {formatDateTime(detail.updated_at)}
                  </Row>
                </tbody>
              </table>
            </div>

            <div className="dash-panel">
              <div className="dash-panel-title">Triage</div>
              <table className="dash-table">
                <tbody>
                  <Row label="Assigned">
                    {detail.assigned_admin_account_id !== null ? (
                      <Link
                        className="dash-link-btn"
                        href={`/accounts/${detail.assigned_admin_account_id}`}
                      >
                        Account #{detail.assigned_admin_account_id}
                      </Link>
                    ) : (
                      <span className="dash-sub">Unassigned</span>
                    )}
                  </Row>
                </tbody>
              </table>
              <div className="dash-panel-actions">
                {detail.status !== "pending" && (
                  <button
                    type="button"
                    className="dash-btn dash-btn-secondary"
                    onClick={() => void changeStatus("pending")}
                    disabled={busy !== null}
                  >
                    Waiting on merchant
                  </button>
                )}
                {detail.status !== "open" && (
                  <button
                    type="button"
                    className="dash-btn dash-btn-secondary"
                    onClick={() => void changeStatus("open")}
                    disabled={busy !== null}
                  >
                    Reopen
                  </button>
                )}
                {detail.status !== "resolved" && (
                  <button
                    type="button"
                    className="dash-btn dash-btn-danger"
                    onClick={() => setCloseOpen(true)}
                    disabled={busy !== null}
                  >
                    Close request
                  </button>
                )}
              </div>
              <div className="dash-field">
                <label htmlFor="support-assignee">Assign to admin account ID</label>
                <div className="dash-toolbar-filters">
                  <input
                    id="support-assignee"
                    className="dash-input"
                    type="text"
                    inputMode="numeric"
                    placeholder="e.g. 1"
                    value={assigneeInput}
                    onChange={(e) =>
                      setAssigneeInput(e.target.value.replace(/[^0-9]/g, ""))
                    }
                    aria-label="Admin account id to assign this request to"
                  />
                  <button
                    type="button"
                    className="dash-btn dash-btn-secondary"
                    onClick={() => void assign()}
                    disabled={busy !== null || assigneeInput.trim().length === 0}
                  >
                    {busy === "assign" ? "Assigning…" : "Assign"}
                  </button>
                  {detail.assigned_admin_account_id !== null && (
                    <button
                      type="button"
                      className="dash-btn dash-btn-secondary"
                      onClick={() => void unassign()}
                      disabled={busy !== null}
                    >
                      Unassign
                    </button>
                  )}
                </div>
              </div>
              <div className="dash-hint">
                Only a platform admin can hold a request — the API refuses any other
                account, so check the ID before assigning.
              </div>
            </div>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Conversation</div>
            {detail.messages.length === 0 ? (
              <div className="dash-empty">
                This thread has no messages.
                <div className="dash-empty-desc">
                  Every request opens with the merchant&rsquo;s first message, so an
                  empty thread means the original text was not stored.
                </div>
              </div>
            ) : (
              <div className="dash-thread">
                {detail.messages.map((message) => {
                  const isOperator = message.author_kind === "operator";
                  return (
                    <div
                      key={message.id}
                      className={`dash-thread-msg${
                        isOperator ? " is-operator" : ""
                      }`}
                    >
                      <div className="dash-thread-head">
                        <span className="dash-thread-author">
                          {isOperator ? "Operator" : "Merchant"}
                        </span>
                        <span className="dash-thread-time">
                          {formatDateTime(message.created_at)}
                        </span>
                      </div>
                      <div className="dash-thread-body">{message.body}</div>
                    </div>
                  );
                })}
              </div>
            )}

            <div className="dash-field">
              <label htmlFor="support-reply">Reply to the merchant</label>
              <textarea
                id="support-reply"
                className="dash-textarea"
                value={reply}
                onChange={(e) => setReply(e.target.value)}
                placeholder="Answer the question in full — this is the message the merchant reads and is emailed a copy of."
                rows={5}
                maxLength={8000}
              />
            </div>
            <div className="dash-panel-actions">
              <button
                type="button"
                className="dash-btn dash-btn-primary"
                onClick={() => void sendReply()}
                disabled={busy !== null || reply.trim().length === 0}
              >
                {busy === "reply" ? "Sending…" : "Send reply"}
              </button>
            </div>
            <div className="dash-hint">
              Sending a reply starts the first-response clock and moves the request
              to <strong>waiting</strong>. The merchant is emailed a courtesy copy;
              the thread here is the record.
            </div>
          </div>
        </>
      )}

      {closeOpen && detail && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">Close this request</h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setCloseOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                This closes <span className="dash-code-mono">{detail.id}</span> —{" "}
                <strong>{detail.subject}</strong> — for{" "}
                {detail.account_email || `account #${detail.account_id}`}. A closed
                request stops its first-response clock and drops out of the open
                queue. It can be reopened from here if the merchant comes back.
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setCloseOpen(false)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void changeStatus("resolved")}
                  disabled={busy !== null}
                >
                  {busy === "status" ? "Closing…" : "Close request"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

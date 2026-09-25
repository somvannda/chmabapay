"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

type PaymentDelivery = {
  id: number;
  event_id: string;
  event_type: string;
  status: string;
  attempts: number;
  last_response_status: number | null;
  last_error: string | null;
  next_attempt_at: string | null;
  updated_at: string;
};

type PaymentDetail = {
  id: string;
  status: string;
  amount_cents: number;
  currency: string;
  reference_id: string | null;
  bill_number: string;
  idempotency_key: string | null;
  metadata: Record<string, unknown> | null;
  account_id: number;
  account_email: string | null;
  store_public_id: string;
  store_name: string;
  created_at: string;
  expires_at: string;
  scanned_at: string | null;
  paid_at: string | null;
  approved_at: string | null;
  reversed_at: string | null;
  reversal_reason: string | null;
  detection_closed_at: string | null;
  bakong_ref: string | null;
  gateway_status_raw: Record<string, unknown> | null;
  qr_md5: string | null;
  qr_string: string;
  attempt_history: unknown[] | null;
  reissued_from: string | null;
  superseded_by: string | null;
  deliveries: PaymentDelivery[];
};

type ReconcileResult = {
  rail_status: string;
  source: string | null;
  matched_amount: number | null;
  transitioned_to_paid: boolean;
  signals: string[];
  error: string | null;
};

const nf = new Intl.NumberFormat("en-US");

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

function formatAmount(cents: number, currency: string): string {
  const symbol = (currency || "USD").toUpperCase() === "USD" ? "$" : "";
  return `${symbol}${(cents / 100).toFixed(2)} ${(currency || "USD").toUpperCase()}`;
}

function paymentPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return { className: "dash-pill dash-pill-paid", label: "paid" };
    case "scanned":
      return { className: "dash-pill dash-pill-scanned", label: "scanned" };
    case "reversed":
      return { className: "dash-pill dash-pill-reversed", label: "refunded" };
    case "superseded":
      return { className: "dash-pill dash-pill-superseded", label: "superseded" };
    case "expired":
      return { className: "dash-pill dash-pill-expired", label: "expired" };
    case "failed":
      return { className: "dash-pill dash-pill-failed", label: "failed" };
    default:
      return {
        className: "dash-pill dash-pill-pending",
        label: status || "pending",
      };
  }
}

function deliveryPill(status: string | null | undefined): {
  className: string;
  label: string;
} {
  switch ((status || "").toLowerCase()) {
    case "success":
      return { className: "dash-pill dash-pill-paid", label: "success" };
    case "retrying":
      return { className: "dash-pill dash-pill-retrying", label: "retrying" };
    case "failed":
      return { className: "dash-pill dash-pill-failed", label: "failed" };
    default:
      return { className: "dash-pill dash-pill-pending", label: "pending" };
  }
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

export default function AdminPaymentDetailPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;
  const { notify } = useToast();

  const [loading, setLoading] = useState(true);
  const [detail, setDetail] = useState<PaymentDetail | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [markOpen, setMarkOpen] = useState(false);
  const [markReason, setMarkReason] = useState("");
  const [refundOpen, setRefundOpen] = useState(false);
  const [refundReason, setRefundReason] = useState("");
  const [showRaw, setShowRaw] = useState(false);
  const [redeliverOpen, setRedeliverOpen] = useState(false);
  const [redeliverSuccesses, setRedeliverSuccesses] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    setNotFound(false);
    try {
      const res = await apiFetch(`/api/v1/admin/payments/${publicId}`, {
        credentials: "include",
      });
      if (res.status === 404) {
        setNotFound(true);
        return;
      }
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as PaymentDetail;
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

  // Re-asking the rail is the safe half of the dispute flow: it can only settle
  // the payment if ABA itself says the money moved.
  const reconcile = useCallback(async () => {
    setBusy("reconcile");
    try {
      const res = await apiFetch(`/api/v1/admin/payments/${publicId}/reconcile`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as ReconcileResult;
      if (data.transitioned_to_paid) {
        notify("Rail confirmed the payment — marked paid.");
      } else if (data.error) {
        notify(`Rail could not answer: ${data.error}`, "error");
      } else {
        notify(`Rail still reports ${data.rail_status.toLowerCase()}.`);
      }
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [publicId, notify, load]);

  // Crediting money with no rail confirmation, so the reason is the record.
  const markPaid = useCallback(async () => {
    const reason = markReason.trim();
    if (reason.length < 3) {
      notify("A reason is required to mark a payment paid.", "error");
      return;
    }
    setBusy("mark-paid");
    try {
      const res = await apiFetch(`/api/v1/admin/payments/${publicId}/mark-paid`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setMarkOpen(false);
      setMarkReason("");
      notify("Payment marked paid. This is recorded in the audit trail.");
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [publicId, markReason, notify, load]);

  // The merchant-facing reverse route resolves the payment inside the caller's own
  // account, so a refund the merchant reports by phone had no console path at all.
  const recordRefund = useCallback(async () => {
    const reason = refundReason.trim();
    if (reason.length < 3) {
      notify("A reason is required to record a refund.", "error");
      return;
    }
    setBusy("refund");
    try {
      const res = await apiFetch(`/api/v1/admin/payments/${publicId}/reverse`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      setRefundOpen(false);
      setRefundReason("");
      notify(
        "Refund recorded. The merchant is notified by webhook and the audit trail names you.",
      );
      await load();
    } catch (e) {
      notify(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  }, [publicId, refundReason, notify, load]);

  const redeliver = useCallback(
    async (includeSuccesses: boolean) => {
      setBusy("redeliver");
      try {
        const res = await apiFetch(
          `/api/v1/admin/payments/${publicId}/redeliver${
            includeSuccesses ? "?include_successes=true" : ""
          }`,
          { method: "POST", credentials: "include" },
        );
        if (!res.ok) throw new Error(await readApiError(res));
        const data = (await res.json()) as { redelivered: number };
        if (data.redelivered === 0) {
          notify(
            "Nothing to re-send: success was already recorded for every delivery.",
          );
        } else {
          notify(
            data.redelivered === 1
              ? "Webhook queued for re-delivery."
              : `${data.redelivered} webhooks queued for re-delivery.`,
          );
        }
        setRedeliverOpen(false);
        await load();
      } catch (e) {
        notify(e instanceof Error ? e.message : String(e), "error");
      } finally {
        setBusy(null);
      }
    },
    [publicId, notify, load],
  );

  const pill = paymentPill(detail?.status);
  const settled = Boolean(detail?.paid_at);
  const terminal = ["paid", "failed", "reversed"].includes(
    (detail?.status || "").toLowerCase(),
  );
  // Re-sending a delivery the merchant already processed can double-process the
  // sale, so the count is surfaced next to the opt-in.
  const successCount = (detail?.deliveries ?? []).filter(
    (d) => d.status.toLowerCase() === "success",
  ).length;

  return (
    <>
      <div className="dash-page-head">
        <div>
          <div className="dash-toolbar-filters">
            <Link className="dash-link-btn" href="/payments">
              ← Back to payments
            </Link>
          </div>
          <h1 className="dash-page-title">
            {detail ? detail.id : "Payment"}
          </h1>
          <div className="dash-page-subtitle">
            {detail
              ? `${detail.store_name} · ${detail.account_email || `Account #${detail.account_id}`}`
              : "Payment detail"}
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
        <div className="dash-info">Loading payment…</div>
      ) : errorMsg && !detail ? (
        <div className="dash-empty">
          Could not load this payment.
          <div className="dash-empty-desc">
            The request failed, so this is not a missing payment. Use Retry above,
            or reload the page.
          </div>
        </div>
      ) : notFound || !detail ? (
        <div className="dash-empty">
          Payment not found.
          <div className="dash-empty-desc">
            Check the payment ID from the merchant&rsquo;s message.{" "}
            <Link className="dash-link-btn" href="/payments">
              Return to payments
            </Link>
          </div>
        </div>
      ) : (
        <>
          <div className="dash-panels">
            <div className="dash-panel">
              <div className="dash-panel-title">Payment</div>
              <table className="dash-table">
                <tbody>
                  <Row label="Status">
                    <span className={pill.className}>{pill.label}</span>
                  </Row>
                  <Row label="Amount">
                    {formatAmount(detail.amount_cents, detail.currency)}
                  </Row>
                  <Row label="Reference">
                    <Dash value={detail.reference_id} />
                  </Row>
                  <Row label="Bill number">
                    <span className="dash-code-mono">{detail.bill_number}</span>
                  </Row>
                  <Row label="Idempotency key">
                    <Dash value={detail.idempotency_key} />
                  </Row>
                  <Row label="Created">{formatDateTime(detail.created_at)}</Row>
                  <Row label="Expires">{formatDateTime(detail.expires_at)}</Row>
                  <Row label="Scanned">{formatDateTime(detail.scanned_at)}</Row>
                  <Row label="Paid">{formatDateTime(detail.paid_at)}</Row>
                  <Row label="Approved">
                    {formatDateTime(detail.approved_at)}
                  </Row>
                  <Row label="Stopped watching">
                    {detail.detection_closed_at ? (
                      formatDateTime(detail.detection_closed_at)
                    ) : (
                      <span className="dash-pill dash-pill-pending">
                        still watching
                      </span>
                    )}
                  </Row>
                  {detail.reversed_at && (
                    <Row label="Refunded">
                      {formatDateTime(detail.reversed_at)}
                      {detail.reversal_reason && (
                        <div className="dash-sub">{detail.reversal_reason}</div>
                      )}
                    </Row>
                  )}
                  {(detail.reissued_from || detail.superseded_by) && (
                    <Row label="Reissue">
                      {detail.reissued_from && (
                        <div>
                          replaced{" "}
                          <Link
                            className="dash-link-btn"
                            href={`/payments/${detail.reissued_from}`}
                          >
                            {detail.reissued_from}
                          </Link>
                        </div>
                      )}
                      {detail.superseded_by && (
                        <div>
                          replaced by{" "}
                          <Link
                            className="dash-link-btn"
                            href={`/payments/${detail.superseded_by}`}
                          >
                            {detail.superseded_by}
                          </Link>
                        </div>
                      )}
                    </Row>
                  )}
                </tbody>
              </table>
            </div>

            <div className="dash-panel">
              <div className="dash-panel-title">Rail evidence</div>
              <table className="dash-table">
                <tbody>
                  <Row label="ABA transaction">
                    <span className="dash-code-mono">
                      <Dash value={detail.bakong_ref} />
                    </span>
                  </Row>
                  <Row label="QR md5">
                    <span className="dash-code-mono">
                      <Dash value={detail.qr_md5} />
                    </span>
                  </Row>
                  <Row label="Hosted session">
                    {detail.gateway_status_raw?.payway_hosted ? (
                      <span className="dash-pill dash-pill-paid">present</span>
                    ) : (
                      <span className="dash-pill dash-pill-pending">none</span>
                    )}
                  </Row>
                  <Row label="Attempts">
                    {detail.attempt_history
                      ? nf.format(detail.attempt_history.length)
                      : 0}
                  </Row>
                </tbody>
              </table>
              <div className="dash-panel-actions">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary dash-btn-sm"
                  onClick={() => setShowRaw((v) => !v)}
                >
                  {showRaw ? "Hide raw payload" : "Show raw payload"}
                </button>
              </div>
              {showRaw && (
                <pre className="dash-pre">
                  {JSON.stringify(
                    {
                      gateway_status_raw: detail.gateway_status_raw,
                      attempt_history: detail.attempt_history,
                      metadata: detail.metadata,
                      qr_string: detail.qr_string,
                    },
                    null,
                    2,
                  )}
                </pre>
              )}
            </div>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Resolve</div>
            <div className="dash-hint">
              Re-reconcile asks the rail again and settles the payment only if the
              rail confirms the money moved. Marking paid credits it with no rail
              confirmation and is recorded in the audit trail. Recording a refund
              reverses a settled payment and tells the merchant by webhook.
              Re-delivering queues this payment&rsquo;s webhooks for the merchant
              again.
            </div>
            <div className="dash-panel-actions">
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => void reconcile()}
                disabled={busy !== null || settled}
                title={
                  settled
                    ? "Already paid — nothing left to reconcile."
                    : undefined
                }
              >
                {busy === "reconcile" ? "Reconciling…" : "Re-reconcile"}
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-danger"
                onClick={() => setMarkOpen(true)}
                disabled={busy !== null || terminal}
                title={
                  terminal
                    ? "This payment is terminal and cannot be credited by hand."
                    : undefined
                }
              >
                Mark paid manually
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => setRefundOpen(true)}
                disabled={busy !== null || !settled}
                title={
                  settled
                    ? undefined
                    : "Only a settled payment can be refunded — there is nothing to give back."
                }
              >
                Record refund
              </button>
              <button
                type="button"
                className="dash-btn dash-btn-secondary"
                onClick={() => {
                  setRedeliverSuccesses(false);
                  setRedeliverOpen(true);
                }}
                disabled={busy !== null || detail.deliveries.length === 0}
                title={
                  detail.deliveries.length === 0
                    ? "No webhook was ever queued for this payment."
                    : undefined
                }
              >
                {busy === "redeliver"
                  ? "Re-delivering…"
                  : "Re-deliver webhooks"}
              </button>
            </div>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Webhook deliveries</div>
            {detail.deliveries.length === 0 ? (
              <div className="dash-empty">
                No webhook was queued for this payment.
                <div className="dash-empty-desc">
                  Nothing was delivered because no event was raised — usually
                  because the payment never reached a terminal state.
                </div>
              </div>
            ) : (
              <table className="dash-table">
                <thead>
                  <tr>
                    <th>Delivery</th>
                    <th>Event</th>
                    <th>Status</th>
                    <th>Attempts</th>
                    <th>HTTP</th>
                    <th>Next attempt</th>
                    <th>Last error</th>
                  </tr>
                </thead>
                <tbody>
                  {detail.deliveries.map((d) => {
                    const dp = deliveryPill(d.status);
                    return (
                      <tr key={d.id}>
                        <td>{d.id}</td>
                        <td>
                          <span className="dash-code-mono">{d.event_type}</span>
                        </td>
                        <td>
                          <span className={dp.className}>{dp.label}</span>
                        </td>
                        <td>{nf.format(d.attempts)}</td>
                        <td>{d.last_response_status ?? "—"}</td>
                        <td>{formatDateTime(d.next_attempt_at)}</td>
                        <td>{d.last_error || "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </>
      )}

      {markOpen && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">Mark this payment paid</h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setMarkOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                This credits <span className="dash-code-mono">{detail?.id}</span> —{" "}
                {detail
                  ? formatAmount(detail.amount_cents, detail.currency)
                  : "—"}{" "}
                for {detail?.store_name} — with no confirmation from the rail. Only
                do it when a customer&rsquo;s receipt proves the money moved. The
                reason below is stored in the audit trail against your account.
              </div>
              <div className="dash-field">
                <label htmlFor="mark-paid-reason">Reason</label>
                <textarea
                  id="mark-paid-reason"
                  className="dash-textarea"
                  value={markReason}
                  onChange={(e) => setMarkReason(e.target.value)}
                  placeholder="e.g. Customer receipt ABA ref 12345678 confirms payment; hosted status endpoint unreachable."
                  rows={4}
                  maxLength={255}
                />
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setMarkOpen(false)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void markPaid()}
                  disabled={busy !== null || markReason.trim().length < 3}
                >
                  {busy === "mark-paid" ? "Marking paid…" : "Mark paid"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {refundOpen && detail && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">Record a refund</h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setRefundOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                This reverses{" "}
                <span className="dash-code-mono">{detail.id}</span> —{" "}
                {formatAmount(detail.amount_cents, detail.currency)} for{" "}
                {detail.store_name} — and sends{" "}
                <span className="dash-code-mono">payment.reversed</span> to the
                merchant. Only record it once the money has actually gone back: a
                reversal cannot be undone from here. The reason below is stored in
                the audit trail against your account.
              </div>
              <div className="dash-field">
                <label htmlFor="refund-reason">Reason</label>
                <textarea
                  id="refund-reason"
                  className="dash-textarea"
                  value={refundReason}
                  onChange={(e) => setRefundReason(e.target.value)}
                  placeholder="e.g. Customer returned the order; refunded at the counter in cash on 2026-09-23."
                  rows={4}
                  maxLength={255}
                />
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setRefundOpen(false)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-danger"
                  onClick={() => void recordRefund()}
                  disabled={busy !== null || refundReason.trim().length < 3}
                >
                  {busy === "refund" ? "Recording…" : "Confirm and record refund"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {redeliverOpen && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">Re-deliver this payment&rsquo;s webhooks</h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setRedeliverOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-hint">
                Failed and still-queued deliveries are queued to go out now, with
                their attempt budget reset. Deliveries that already succeeded are
                left alone by default: re-sending a <code>payment.completed</code>{" "}
                the merchant already processed can double-process the sale on their
                side.
              </div>
              <div className="dash-field">
                <label className="dash-check">
                  <input
                    type="checkbox"
                    checked={redeliverSuccesses}
                    onChange={(e) => setRedeliverSuccesses(e.target.checked)}
                  />{" "}
                  Also re-send deliveries that already succeeded
                  {successCount > 0 ? ` (${successCount})` : ""}
                </label>
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setRedeliverOpen(false)}
                  disabled={busy !== null}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-primary"
                  onClick={() => void redeliver(redeliverSuccesses)}
                  disabled={busy !== null}
                >
                  {busy === "redeliver" ? "Re-delivering…" : "Re-deliver"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

type StoreOption = {
  id: string;
  name: string;
  [k: string]: unknown;
};

type Payment = {
  id: string;
  status: string;
  amount: string;
  currency?: string;
  reference_id?: string | null;
  store?: string;
  metadata?: Record<string, unknown> | null;
  checkout_url?: string | null;
  qr_string?: string | null;
  scanned_at?: string | null;
  paid_at?: string | null;
  approved_at?: string | null;
  created_at?: string | null;
  expires_at?: string | null;
  bakong_ref?: string | null;
  reissued_from?: string | null;
  reversed_at?: string | null;
  reversal_reason?: string | null;
  [k: string]: unknown;
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function formatAmount(str: string | null | undefined): string {
  if (!str) return "$0.00";
  const n = parseFloat(str);
  if (Number.isNaN(n)) return str;
  return `$${n.toFixed(2)}`;
}

function pillClassForStatus(status: string): string {
  switch ((status || "").toLowerCase()) {
    case "paid":
      return "dash-pill dash-pill-paid";
    case "scanned":
      return "dash-pill dash-pill-scanned";
    case "expired":
      return "dash-pill dash-pill-expired";
    case "failed":
      return "dash-pill dash-pill-failed";
    // `reversed` and `superseded` used to fall through to the grey "pending" pill,
    // so a refunded payment read as one still waiting for money and a replaced code
    // read as live. Both are terminal.
    case "reversed":
      return "dash-pill dash-pill-reversed";
    case "superseded":
      return "dash-pill dash-pill-superseded";
    default:
      return "dash-pill dash-pill-pending";
  }
}

export default function DashboardPaymentDetailPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [checkoutOrigin, setCheckoutOrigin] = useState("");
  const [copied, setCopied] = useState(false);
  const [reissuing, setReissuing] = useState(false);
  const [refunding, setRefunding] = useState(false);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setCheckoutOrigin(window.location.origin);
    }
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/stores", { credentials: "include" });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const items: StoreOption[] = Array.isArray(data)
            ? data
            : Array.isArray(data?.items)
              ? data.items
              : Array.isArray(data?.data)
                ? data.data
                : [];
          if (alive) setStores(items);
        }
      } catch {
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`/v1/payments/${publicId}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          if (alive) setPayment(data as Payment);
        } else if (res.status === 404) {
          if (alive) setError("Payment not found.");
        } else {
          if (alive) setError("Could not load payment.");
        }
      } catch {
        if (alive) setError("Network error loading payment.");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId]);

  async function handleRefresh() {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/v1/payments/${publicId}`, {
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json();
        setPayment(data as Payment);
      }
    } catch {
    } finally {
      setLoading(false);
    }
  }

  // The dev-only "Test: Mark paid" button that used to sit in the header called
  // `/_dev/payments/{id}/pay`, a route that exists only when the dev gateway is mounted
  // — so in production a platform admin was offered a control that answered 404. It is
  // deleted rather than hidden because the console now carries a supported
  // `POST /v1/admin/payments/{id}/mark-paid`: same effect, a required reason, and an
  // audit row naming the operator.
  function storeName(): string {
    if (!payment?.store) return "-";
    const s = stores.find((x) => x.id === payment.store);
    return s?.name || payment.store;
  }

  /**
   * Record that the merchant refunded the customer.
   *
   * This is bookkeeping, not a transfer. ChmabaPay never holds the merchant's
   * money — it settles straight into their own ABA or Bakong account — so there is
   * nothing here to send back, and ABA reports no reversal we could detect. The
   * merchant refunds through their own bank and records it here; the point of the
   * record is that the reports and the monthly quota stop counting a sale that was
   * given back.
   */
  async function handleRefund() {
    if (!payment) return;
    const note = window.prompt(
      `Record a refund of ${formatAmount(payment.amount)} for this payment?\n\n` +
        "This does not move money. Send the refund back to the customer from your " +
        "own ABA or bank account — ChmabaPay never holds your funds and cannot " +
        "return them.\n\n" +
        "Note for your records (optional):",
      "",
    );
    // An empty box means "refund, no note" (the API allows it — refusing to record
    // a real refund for want of a note would leave the ledger knowingly wrong).
    // Cancel means no refund at all, so null is the only abort case.
    if (note === null) return;

    setRefunding(true);
    setError(null);
    try {
      const res = await fetch(`/v1/payments/${payment.id}/reverse`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: note.trim() || null }),
      });
      if (res.ok) {
        // The response is the updated payment, so there is no second round trip to
        // race against.
        setPayment((await res.json()) as Payment);
        setFlash("Refund recorded.");
        setTimeout(() => setFlash(null), 4000);
        return;
      }
      const err = await res.json().catch(() => ({}));
      const detail = typeof err?.detail === "string" ? err.detail : "";
      setError(
        detail === "payment_already_reversed"
          ? "This payment already has a refund recorded against it."
          : detail === "payment_not_paid"
            ? "Only a settled payment can be refunded."
            : detail === "payment_not_found"
              ? "Payment not found."
              : "Could not record the refund. Try again in a moment.",
      );
    } catch {
      setError("Network error recording the refund.");
    } finally {
      setRefunding(false);
    }
  }

  async function handleCopy(text: string) {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
    }
  }

  // A new QR means a new ABA session, so the backend answers with a successor
  // payment; follow it rather than pretending this row came back to life.
  async function handleReissue() {
    if (!payment) return;
    setReissuing(true);
    setError(null);
    try {
      const res = await fetch(`/v1/payments/${payment.id}/reissue`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        setError("Could not generate a new QR. Try again in a moment.");
        return;
      }
      const next = (await res.json()) as Payment;
      router.push(`/dashboard/payments/${next.id}`);
    } catch {
      setError("Network error generating a new QR.");
    } finally {
      setReissuing(false);
    }
  }

  // The page's own origin wins, because it is where this dashboard is actually
  // being served and `/pay/{id}` resolves there. The server's `checkout_url` is
  // built from `request.base_url`, which behind a proxy is the backend's own
  // address — so recording a refund made this field flip from the host the
  // merchant was looking at to the internal one, and copying it then produced a
  // link that does not work. The server value is still the fallback for the first
  // paint, before `window.location.origin` is known.
  const checkoutUrl = payment
    ? checkoutOrigin
      ? `${checkoutOrigin}/pay/${payment.id}`
      : payment.checkout_url || ""
    : "";

  const status = (payment?.status || "").toLowerCase();
  const isReversed = status === "reversed";

  // `/pay/{id}/qr.png` never existed on the backend and onError hid the 404, so
  // this page promised a scannable code and showed nothing. The QR route now
  // answers 410 once the code is dead — expired, failed, superseded or reversed —
  // so don't point an <img> at a dead one.
  const isDead = ["expired", "failed", "superseded", "reversed"].includes(status);
  // A refunded sale does not want a fresh code; only one that ran out of time does.
  const canReissue = status === "expired" || status === "failed";
  const qrSrc = payment?.qr_string && !isDead ? `/pay/${publicId}/qr.svg` : "";

  return (
    <>
      <div className="dash-page-head">
        <div>
          <div className="dash-toolbar-filters">
            <Link className="dash-link-btn" href="/dashboard/payments">
              ← Back to payments
            </Link>
          </div>
          <h2 className="dash-page-title">
            {loading ? "Loading…" : error ? "Payment" : `#${publicId.slice(0, 10)}`}
          </h2>
          <div className="dash-page-subtitle">Payment details</div>
        </div>
        {payment && payment.status === "paid" && (
          <div className="dash-toolbar-filters">
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={handleRefund}
              disabled={refunding}
            >
              {refunding ? "Recording…" : "Record a refund"}
            </button>
          </div>
        )}
      </div>

      {error && <div className="dash-warn">{error}</div>}
      {flash && <div className="dash-info">{flash}</div>}

      {!loading && payment && (
        <>
          <div className="dash-panels">
            <div className="dash-panel">
              <div className="dash-panel-title">Payment QR</div>
              <div className="dash-empty">
                {qrSrc ? (
                  <img className="dash-qr-img" src={qrSrc} alt="Payment QR" />
                ) : null}
                <div className="dash-empty-desc">
                  {qrSrc ? (
                    <>
                      Customer scans this QR with their Bakong or ABA mobile app
                      to pay <strong>{formatAmount(payment.amount)}</strong>.
                    </>
                  ) : isReversed ? (
                    <>
                      This payment was refunded on{" "}
                      {formatDate(payment.reversed_at)}, so its code cannot be shown
                      again. Nothing moved from here — the money went back to the
                      customer from your own ABA or bank account.
                    </>
                  ) : canReissue ? (
                    <>
                      This code stopped working at{" "}
                      {formatDate(payment.expires_at)} and cannot be shown again.
                      Generate a new one and send the new link to the customer.
                    </>
                  ) : isDead ? (
                    // `superseded`: a newer code replaced this one, so the button
                    // above must not appear. Offering "Generate new QR" here would
                    // spend a second ABA session for a sale that already has a live
                    // successor.
                    <>A newer code replaced this one, so it cannot be shown again.</>
                  ) : (
                    <>This payment carries no QR payload to display.</>
                  )}
                  {qrSrc && payment.expires_at
                    ? ` The code stops working at ${formatDate(payment.expires_at)}.`
                    : ""}
                  {payment.reissued_from ? (
                    <div className="dash-hint">
                      Replaces #{payment.reissued_from.slice(0, 10)}, whose code
                      expired.
                    </div>
                  ) : null}
                  {/* Only rendered when it has something in it. A reversed or
                      superseded payment has no checkout page to open — `/pay/:id`
                      answers 410 for every dead status, so the link is withheld
                      rather than offered and then refused — and no new code to
                      mint either. */}
                  {(!isDead || canReissue) && (
                    <div className="dash-empty-cta-row">
                      {!isDead && (
                        <a
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          href={`/pay/${payment.id}`}
                          target="_blank"
                          rel="noreferrer"
                        >
                          Open checkout page ↗
                        </a>
                      )}
                      {canReissue ? (
                        <button
                          type="button"
                          className="dash-btn dash-btn-primary dash-btn-sm"
                          onClick={handleReissue}
                          disabled={reissuing}
                        >
                          {reissuing ? "Generating…" : "Generate new QR"}
                        </button>
                      ) : null}
                    </div>
                  )}
                </div>
              </div>
            </div>

            <div className="dash-panel">
              <div className="dash-panel-title">Information</div>
              <table className="dash-table">
                <tbody>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Amount</div>
                    </td>
                    <td>
                      <div className="dash-stat-value">
                        {formatAmount(payment.amount)}
                      </div>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Status</div>
                    </td>
                    <td>
                      <span className={pillClassForStatus(payment.status)}>
                        {(payment.status || "pending").toLowerCase()}
                      </span>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Reference</div>
                    </td>
                    <td>{payment.reference_id || "-"}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Store</div>
                    </td>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/dashboard/${payment.store || ""}`}
                      >
                        {storeName()}
                      </Link>
                    </td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Created at</div>
                    </td>
                    <td>{formatDate(payment.created_at)}</td>
                  </tr>
                  <tr>
                    <td>
                      <div className="dash-stat-label">Paid at</div>
                    </td>
                    <td>{formatDate(payment.paid_at)}</td>
                  </tr>
                  {isReversed && (
                    <tr>
                      <td>
                        <div className="dash-stat-label">Refunded at</div>
                      </td>
                      <td>{formatDate(payment.reversed_at)}</td>
                    </tr>
                  )}
                  {isReversed && payment.reversal_reason ? (
                    <tr>
                      <td>
                        <div className="dash-stat-label">Refund note</div>
                      </td>
                      <td>{payment.reversal_reason}</td>
                    </tr>
                  ) : null}
                  <tr>
                    <td>
                      <div className="dash-stat-label">Bakong ref</div>
                    </td>
                    <td>{payment.bakong_ref || "-"}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Checkout URL</div>
            <div className="dash-copy-field">
              <span className="dash-copy-field-value">{checkoutUrl}</span>
              <button
                type="button"
                className="dash-copy-btn"
                onClick={() => handleCopy(checkoutUrl)}
              >
                {copied ? "Copied!" : "Copy"}
              </button>
            </div>
          </div>

          {payment.metadata &&
            Object.keys(payment.metadata).length > 0 && (
              <div className="dash-panel">
                <div className="dash-panel-title">Metadata</div>
                <table className="dash-table">
                  <thead>
                    <tr>
                      <th>Key</th>
                      <th>Value</th>
                    </tr>
                  </thead>
                  <tbody>
                    {Object.entries(payment.metadata).map(([k, v]) => (
                      <tr key={k}>
                        <td>{k}</td>
                        <td>{String(v ?? "-")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
        </>
      )}
    </>
  );
}

"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useSession } from "@/components/portal/useSession";

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
  const { profile } = useSession();
  const router = useRouter();

  const [loading, setLoading] = useState(true);
  const [payment, setPayment] = useState<Payment | null>(null);
  const [stores, setStores] = useState<StoreOption[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [markingPaid, setMarkingPaid] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);
  const [checkoutOrigin, setCheckoutOrigin] = useState("");
  const [copied, setCopied] = useState(false);
  const [reissuing, setReissuing] = useState(false);

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

  async function handleMarkPaid() {
    if (!payment) return;
    if (!confirm("Simulate marking this payment as PAID?")) return;
    setMarkingPaid(true);
    try {
      const res = await fetch(`/_dev/payments/${payment.id}/pay`, {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        setFlash("Payment marked as paid. Refreshing…");
        setTimeout(() => {
          setFlash(null);
          void handleRefresh();
        }, 800);
      } else {
        const err = await res.json().catch(() => ({}));
        setFlash(err?.detail || "Could not mark payment as paid.");
        setTimeout(() => setFlash(null), 4000);
      }
    } catch {
      setFlash("Network error.");
      setTimeout(() => setFlash(null), 4000);
    } finally {
      setMarkingPaid(false);
    }
  }

  function storeName(): string {
    if (!payment?.store) return "-";
    const s = stores.find((x) => x.id === payment.store);
    return s?.name || payment.store;
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

  const checkoutUrl = payment
    ? payment.checkout_url || `${checkoutOrigin}/pay/${payment.id}`
    : "";

  // `/pay/{id}/qr.png` never existed on the backend and onError hid the 404, so
  // this page promised a scannable code and showed nothing. The QR route now
  // answers 410 once the code is dead, so don't point an <img> at a dead one.
  const isDead = payment?.status === "expired" || payment?.status === "failed";
  const qrSrc =
    payment?.qr_string && !isDead ? `/pay/${publicId}/qr.svg` : "";

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
        {profile?.is_platform_admin && payment && (
          <div>
            <button
              type="button"
              className="dash-btn dash-btn-secondary"
              onClick={handleMarkPaid}
              disabled={markingPaid || payment.status === "paid"}
            >
              {markingPaid
                ? "…"
                : payment.status === "paid"
                  ? "Already paid"
                  : "Test: Mark paid"}
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
                  ) : isDead ? (
                    <>
                      This code stopped working at{" "}
                      {formatDate(payment.expires_at)} and cannot be shown again.
                      Generate a new one and send the new link to the customer.
                    </>
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
                  <div className="dash-empty-cta-row">
                    <a
                      className="dash-btn dash-btn-secondary dash-btn-sm"
                      href={`/pay/${payment.id}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      Open checkout page ↗
                    </a>
                    {isDead ? (
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

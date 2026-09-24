"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { readApiErrorInfo } from "@/components/portal/apiError";

type StoreOption = {
  id: string;
  name: string;
  status?: string;
  [k: string]: unknown;
};

export default function DashboardPaymentsNewPage() {
  const router = useRouter();

  const [stores, setStores] = useState<StoreOption[]>([]);
  const [storesLoading, setStoresLoading] = useState(true);
  const [amount, setAmount] = useState("");
  const [referenceId, setReferenceId] = useState("");
  const [storePublicId, setStorePublicId] = useState("");
  const [merchant, setMerchant] = useState("");
  const [idempotencyKey, setIdempotencyKey] = useState("");
  // "auto" omits the field entirely, which is what lets the API decide: ABA issues the
  // code whenever the store's link is a PayWay link. The other two are the explicit
  // answers the API accepts.
  const [hostedQr, setHostedQr] = useState<"auto" | "hosted" | "offline">("auto");
  const [metaKey1, setMetaKey1] = useState("");
  const [metaVal1, setMetaVal1] = useState("");
  const [metaKey2, setMetaKey2] = useState("");
  const [metaVal2, setMetaVal2] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

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
          if (alive) {
            setStores(items);
            if (items.length === 1) {
              setStorePublicId(items[0].id);
            }
          }
        }
      } catch {
      } finally {
        if (alive) setStoresLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const parsedAmount = parseFloat(amount);
  const previewAmount =
    amount && !Number.isNaN(parsedAmount) && parsedAmount > 0
      ? `$${parsedAmount.toFixed(2)}`
      : "$0.00";

  const amountValid =
    amount.trim() !== "" &&
    !Number.isNaN(parsedAmount) &&
    parsedAmount > 0 &&
    Math.round(parsedAmount * 100) / 100 === parsedAmount;

  const activeStores = stores.filter(
    (s) => {
      const sLower = (s.status || "active").toLowerCase();
      return sLower === "active" || sLower === "store_draft" || sLower === "draft";
    },
  );

  const storeValid =
    storePublicId !== "" ||
    (stores.length === 1 && activeStores.length === 1);

  // The API resolves `merchant` (external id) before `store` (public id) and before the
  // single-active-store fallback, so a form that offers the external id has to accept it
  // on its own — otherwise the field could be filled in and the submit button still dead.
  const merchantValid = merchant.trim() !== "";

  function isFormValid(): boolean {
    return amountValid && (merchantValid || storeValid || stores.length === 1);
  }

  function buildMetadata(): Record<string, string> | undefined {
    const out: Record<string, string> = {};
    if (metaKey1.trim() && metaVal1.trim()) {
      out[metaKey1.trim()] = metaVal1.trim();
    }
    if (metaKey2.trim() && metaVal2.trim()) {
      out[metaKey2.trim()] = metaVal2.trim();
    }
    return Object.keys(out).length > 0 ? out : undefined;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isFormValid() || submitting) return;
    setSubmitting(true);
    setFormError(null);
    setUpgrade(false);
    try {
      const body: Record<string, unknown> = {
        amount: parsedAmount,
      };
      if (referenceId.trim()) body.reference_id = referenceId.trim();
      const metadata = buildMetadata();
      if (metadata) body.metadata = metadata;
      if (storePublicId) body.store = storePublicId;
      // The same three fields the API documents. Left untouched they are omitted rather
      // than sent as null, so the request is what the API's own defaults describe.
      if (merchant.trim()) body.merchant = merchant.trim();
      if (idempotencyKey.trim()) body.idempotency_key = idempotencyKey.trim();
      if (hostedQr !== "auto") body.hosted_qr = hostedQr === "hosted";
      const res = await fetch("/v1/payments", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.status === 201 || res.status === 200) {
        const data = await res.json().catch(() => ({}));
        const pid = data?.id || data?.public_id;
        if (pid) {
          router.push(`/dashboard/payments/${pid}`);
          return;
        }
        router.push("/dashboard/payments");
        return;
      }
      const info = await readApiErrorInfo(res);
      setFormError(info.message);
      setUpgrade(info.upgrade);
    } catch {
      setFormError("Network error. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">New payment</h2>
          <div className="dash-page-subtitle">
            Create a payment request for your customer
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-secondary dash-btn-sm"
            href="/dashboard/payments"
          >
            ← Back to payments
          </Link>
        </div>
      </div>

      {formError &&
        (upgrade ? (
          <div className="dash-warn">
            <strong>Plan limit reached.</strong> {formError}{" "}
            <Link className="dash-link-btn" href="/dashboard/billing">
              Upgrade your plan
            </Link>
          </div>
        ) : (
          <div className="dash-form-alert dash-form-alert-error">{formError}</div>
        ))}

      <form className="dash-panel dash-form" onSubmit={handleSubmit}>
        <div className="dash-form-row">
          <div className="dash-field">
            <label htmlFor="pay-amount">Amount</label>
            <input
              id="pay-amount"
              className="dash-input"
              type="number"
              step="0.01"
              min="0.01"
              placeholder="e.g. 25.00"
              value={amount}
              onChange={(e) => setAmount(e.target.value)}
              required
            />
            {amount && !amountValid && (
              <div className="dash-field-error">
                Enter a valid positive amount with up to 2 decimals.
              </div>
            )}
          </div>
          <div className="dash-field">
            <label>Preview</label>
            <div className="dash-copy-field">
              <span className="dash-copy-field-value">{previewAmount}</span>
            </div>
          </div>
        </div>

        <div className="dash-form-row">
          <div className="dash-field">
            <label htmlFor="pay-reference">
              Reference ID <span className="dash-badge dash-badge-muted">optional</span>
            </label>
            <input
              id="pay-reference"
              className="dash-input"
              type="text"
              placeholder="e.g. ORDER-12345"
              value={referenceId}
              onChange={(e) => setReferenceId(e.target.value)}
              maxLength={255}
            />
          </div>
          <div className="dash-field">
            <label htmlFor="pay-store">Store</label>
            <select
              id="pay-store"
              className="dash-select"
              value={storePublicId}
              onChange={(e) => setStorePublicId(e.target.value)}
              required={stores.length > 1}
              disabled={storesLoading}
            >
              {stores.length > 1 || activeStores.length !== 1 ? (
                <option value="">Select a store…</option>
              ) : null}
              {activeStores.map((s) => (
                <option key={s.id} value={s.id}>
                  {s.name}
                </option>
              ))}
              {activeStores.length === 0 && stores.length > 0
                ? stores.map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.name}
                    </option>
                  ))
                : null}
            </select>
            {!storesLoading && stores.length === 0 && (
              <div className="dash-field-error">
                No stores available.{" "}
                <Link className="dash-link-btn" href="/dashboard/stores/new">
                  Create a store first.
                </Link>
              </div>
            )}
          </div>
        </div>

        <div className="dash-form-row">
          <div className="dash-field">
            <label htmlFor="pay-merchant">
              Store external ID{" "}
              <span className="dash-badge dash-badge-muted">optional</span>
            </label>
            <input
              id="pay-merchant"
              className="dash-input"
              type="text"
              placeholder="e.g. sokha-cafe"
              value={merchant}
              onChange={(e) => setMerchant(e.target.value)}
              maxLength={255}
            />
            <div className="dash-hint">
              Names the store by your own reference instead of the id above. If both are
              given the API resolves this one first.
            </div>
          </div>
          <div className="dash-field">
            <label htmlFor="pay-idempotency">
              Idempotency key{" "}
              <span className="dash-badge dash-badge-muted">optional</span>
            </label>
            <input
              id="pay-idempotency"
              className="dash-input"
              type="text"
              placeholder="e.g. ORDER-12345"
              value={idempotencyKey}
              onChange={(e) => setIdempotencyKey(e.target.value)}
              maxLength={255}
            />
            <div className="dash-hint">
              Sending the same key again returns the payment already stored for it
              instead of creating a second one. Leave it blank to always create a new
              payment.
            </div>
          </div>
        </div>

        <div className="dash-field">
          <label htmlFor="pay-hosted-qr">QR source</label>
          <select
            id="pay-hosted-qr"
            className="dash-select"
            value={hostedQr}
            onChange={(e) =>
              setHostedQr(e.target.value as "auto" | "hosted" | "offline")
            }
          >
            <option value="auto">Automatic — ABA issues the QR when the store is on a PayWay link</option>
            <option value="hosted">ABA PayWay issues the QR</option>
            <option value="offline">Build the QR ourselves</option>
          </select>
          {hostedQr === "offline" ? (
            <div className="dash-hint">
              The code is built without an ABA session, so no ABA transaction exists
              for it. A deployment with no Bakong Open API credentials refuses this
              outright — a QR nothing can confirm must not be handed to a customer.
            </div>
          ) : (
            <div className="dash-hint">
              Automatic is what the API default does, and it is what you want: a code we
              build ourselves for a PayWay store carries no ABA transaction.
            </div>
          )}
        </div>

        <div className="dash-field">
          <label>
            Metadata <span className="dash-badge dash-badge-muted">optional</span>
          </label>
          <div className="dash-form-row">
            <input
              className="dash-input"
              type="text"
              placeholder="Key 1"
              value={metaKey1}
              onChange={(e) => setMetaKey1(e.target.value)}
            />
            <input
              className="dash-input"
              type="text"
              placeholder="Value 1"
              value={metaVal1}
              onChange={(e) => setMetaVal1(e.target.value)}
            />
          </div>
          <div className="dash-form-row">
            <input
              className="dash-input"
              type="text"
              placeholder="Key 2"
              value={metaKey2}
              onChange={(e) => setMetaKey2(e.target.value)}
            />
            <input
              className="dash-input"
              type="text"
              placeholder="Value 2"
              value={metaVal2}
              onChange={(e) => setMetaVal2(e.target.value)}
            />
          </div>
        </div>

        <div className="dash-toolbar">
          <Link
            className="dash-btn dash-btn-secondary"
            href="/dashboard/payments"
          >
            Cancel
          </Link>
          <button
            type="submit"
            className="dash-btn dash-btn-primary"
            disabled={!isFormValid() || submitting}
          >
            {submitting ? "Creating…" : "Create payment"}
          </button>
        </div>
      </form>
    </>
  );
}

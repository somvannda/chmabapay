"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

function paywaySlug(input: string): string {
  const s = input.trim();
  const last = s.split(/[?#]/)[0].split("/").filter(Boolean).pop();
  return last || s;
}

export default function DashboardStoresNewPage() {
  const router = useRouter();

  const [name, setName] = useState("");
  const [externalId, setExternalId] = useState("");
  const [paywayLink, setPaywayLink] = useState("");
  const [paywayMerchantName, setPaywayMerchantName] = useState("");

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);

  function isFormValid(): boolean {
    return Boolean(name.trim()) && paywayLink.trim().length >= 6;
  }

  function buildBody(): Record<string, unknown> {
    const link = paywayLink.trim();
    const body: Record<string, unknown> = {
      name: name.trim(),
      link: {
        raw_link: link,
        merchant_account_id: paywaySlug(link),
        merchant_name: paywayMerchantName.trim() || name.trim(),
      },
    };
    if (externalId.trim()) body.external_id = externalId.trim();
    return body;
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!isFormValid() || submitting) return;
    setSubmitting(true);
    setFormError(null);
    try {
      const res = await fetch("/v1/stores", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody()),
      });
      if (res.status === 201) {
        router.push("/dashboard/stores");
        return;
      }
      const err = await res.json().catch(() => ({}));
      setFormError(err?.detail || "Failed to create store.");
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
          <h2 className="dash-page-title">New store</h2>
          <div className="dash-page-subtitle">
            Paste your ABA PayWay link to start accepting KHQR payments
            immediately.
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-secondary dash-btn-sm"
            href="/dashboard/stores"
          >
            ← Back to stores
          </Link>
        </div>
      </div>

      {formError &&
        (formError.includes("Upgrade") ||
        formError.includes("max") ||
        formError.includes("stores") ? (
          <div className="dash-warn">
            <strong>Store limit reached.</strong> {formError}{" "}
            <Link className="dash-link-btn" href="/dashboard/billing">
              Upgrade your plan
            </Link>
          </div>
        ) : (
          <div className="dash-form-alert dash-form-alert-error">{formError}</div>
        ))}

      <form className="dash-panel dash-form" onSubmit={handleSubmit}>
        <div className="dash-field">
          <label htmlFor="store-name">Store name</label>
          <input
            id="store-name"
            className="dash-input"
            type="text"
            placeholder="e.g. Phnom Penh Cafe"
            value={name}
            onChange={(e) => setName(e.target.value)}
            maxLength={120}
            required
          />
        </div>

        <div className="dash-field">
          <label htmlFor="store-external-id">
            Merchant ID (your external_id){" "}
            <span className="dash-badge dash-badge-muted">optional</span>
          </label>
          <input
            id="store-external-id"
            className="dash-input"
            type="text"
            placeholder="Your internal reference for this store"
            value={externalId}
            onChange={(e) => setExternalId(e.target.value)}
          />
          <div className="dash-hint">
            Use this to reference the store as <code>merchant</code> when
            creating payments and filtering reports.
          </div>
        </div>

        <div className="dash-field">
          <label htmlFor="payway-link">ABA PayWay link</label>
          <input
            id="payway-link"
            className="dash-input"
            type="url"
            placeholder="https://link.payway.com.kh/ABAPAYpe518710Y"
            value={paywayLink}
            onChange={(e) => setPaywayLink(e.target.value)}
            required
          />
          <div className="dash-hint">
            ABA PayWay is the only supported payment destination.
          </div>
        </div>

        <div className="dash-field">
          <label htmlFor="payway-merchant-name">
            Merchant name{" "}
            <span className="dash-badge dash-badge-muted">optional</span>
          </label>
          <input
            id="payway-merchant-name"
            className="dash-input"
            type="text"
            placeholder="Shown to the payer in their bank app"
            value={paywayMerchantName}
            onChange={(e) => setPaywayMerchantName(e.target.value)}
          />
        </div>

        <div className="dash-toolbar">
          <Link
            className="dash-btn dash-btn-secondary"
            href="/dashboard/stores"
          >
            Cancel
          </Link>
          <button
            type="submit"
            className="dash-btn dash-btn-primary"
            disabled={!isFormValid() || submitting}
          >
            {submitting ? "Creating…" : "Create store"}
          </button>
        </div>
      </form>
    </>
  );
}

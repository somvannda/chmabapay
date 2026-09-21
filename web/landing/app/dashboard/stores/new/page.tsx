"use client";

import Link from "next/link";
import { useState } from "react";
import { useRouter } from "next/navigation";

import { readApiErrorInfo } from "@/components/portal/apiError";

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
  const [linkError, setLinkError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  // The slug is what PayWay actually resolves, so the pre-flight check is on the
  // slug and not on the length of whatever was pasted. The server still has the
  // final word: it asks PayWay whether the link exists, which no local check can.
  function isFormValid(): boolean {
    return Boolean(name.trim()) && paywaySlug(paywayLink).length >= 4;
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
    setLinkError(null);
    setUpgrade(false);
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
      // The API says whether this was the link, a plan limit, or something else, so
      // the message lands where it belongs: on the link field, or behind an upgrade
      // link, instead of in one generic banner.
      const info = await readApiErrorInfo(res);
      if (info.field === "link") {
        setLinkError(info.message);
        return;
      }
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
          {linkError && <div className="dash-field-error">{linkError}</div>}
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

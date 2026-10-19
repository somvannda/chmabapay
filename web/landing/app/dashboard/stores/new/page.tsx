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
  const [city, setCity] = useState("");
  const [cityTouched, setCityTouched] = useState(false);
  const [externalId, setExternalId] = useState("");
  const [usdLink, setUsdLink] = useState("");
  const [usdMerchantName, setUsdMerchantName] = useState("");
  const [khrLink, setKhrLink] = useState("");
  const [khrMerchantName, setKhrMerchantName] = useState("");
  // One destination per currency. ABA fixes a link's currency when the link is created
  // and ignores any attempt to ask it for another, so a store that takes both dollars
  // and riel holds one of each — that is why these are two fields, not a choice.
  // Whether riel is offered at all is gated behind the same decision as the API's
  // `ENABLE_KHR_CURRENCY`; the backend refuses KHR when it is off regardless.
  const khrEnabled = process.env.NEXT_PUBLIC_ENABLE_KHR === "true";

  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [upgrade, setUpgrade] = useState(false);

  // The city used to be left out of this form, so every store created here took the
  // column default and reported as "Phnom Penh" — including stores that are not. It
  // is required for the same reason the settings page requires it: the column is
  // NOT NULL and an empty string is not a city. Held back until the field has been
  // visited, because an error under an untouched input on first paint is noise.
  const cityError =
    cityTouched && city.trim() === "" ? "A store needs a city." : null;

  // The slug is what PayWay actually resolves, so the pre-flight check is on the
  // slug and not on the length of whatever was pasted. The server still has the
  // final word: it asks PayWay whether the link exists, which no local check can.
  function isFormValid(): boolean {
    const usdOk = paywaySlug(usdLink).length >= 4;
    const khrOk = paywaySlug(khrLink).length >= 4;
    if (!name.trim() || city.trim() === "") return false;
    // At least one destination, and any destination that was typed has to be complete —
    // a half-typed second link must not be silently dropped from the request.
    if (!usdOk && !khrOk) return false;
    return khrLink.trim() === "" || khrOk;
  }

  function buildBody(): Record<string, unknown> {
    const links: Record<string, unknown>[] = [];
    if (usdLink.trim()) {
      links.push({
        raw_link: usdLink.trim(),
        merchant_account_id: paywaySlug(usdLink),
        merchant_name: usdMerchantName.trim() || name.trim(),
        currency: "USD",
      });
    }
    if (khrEnabled && khrLink.trim()) {
      links.push({
        raw_link: khrLink.trim(),
        merchant_account_id: paywaySlug(khrLink),
        merchant_name: khrMerchantName.trim() || name.trim(),
        currency: "KHR",
      });
    }
    const body: Record<string, unknown> = {
      name: name.trim(),
      city: city.trim(),
      // One destination keeps the original shape; two go as `links`. The API accepts
      // either, so an integration written against `link` is unaffected.
      ...(links.length === 1 ? { link: links[0] } : { links }),
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
      const res = await fetch("/api/v1/stores", {
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
          <label htmlFor="store-city">City</label>
          <input
            id="store-city"
            className="dash-input"
            type="text"
            placeholder="Phnom Penh"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            onBlur={() => setCityTouched(true)}
            maxLength={15}
            aria-invalid={cityError !== null}
            required
          />
          {cityError ? (
            <div className="dash-field-error">{cityError}</div>
          ) : (
            <div className="dash-hint">
              Where this store is. It appears in your store list and in the
              stores CSV export.
            </div>
          )}
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

        <div className="dash-form-row">
          <div className="dash-field">
            <label htmlFor="payway-link-usd">USD link</label>
            <input
              id="payway-link-usd"
              className="dash-input"
              type="url"
              placeholder="https://link.payway.com.kh/ABAPAY…"
              value={usdLink}
              onChange={(e) => setUsdLink(e.target.value)}
            />
          </div>
          <div className="dash-field">
            <label htmlFor="payway-merchant-usd">
              USD merchant name{" "}
              <span className="dash-badge dash-badge-muted">optional</span>
            </label>
            <input
              id="payway-merchant-usd"
              className="dash-input"
              type="text"
              placeholder="Shown to the payer in their bank app"
              value={usdMerchantName}
              onChange={(e) => setUsdMerchantName(e.target.value)}
            />
          </div>
        </div>
        <div className="dash-hint">
          The dollar link. ABA fixes a link&rsquo;s currency when the link is created,
          so a store that takes both currencies holds one link of each. Leave this blank
          if the store only accepts riel.
        </div>

        {khrEnabled && (
          <>
            <div className="dash-form-row">
              <div className="dash-field">
                <label htmlFor="payway-link-khr">
                  KHR link{" "}
                  <span className="dash-badge dash-badge-muted">optional</span>
                </label>
                <input
                  id="payway-link-khr"
                  className="dash-input"
                  type="url"
                  placeholder="https://link.payway.com.kh/ABAPAY…"
                  value={khrLink}
                  onChange={(e) => setKhrLink(e.target.value)}
                />
              </div>
              <div className="dash-field">
                <label htmlFor="payway-merchant-khr">
                  KHR merchant name{" "}
                  <span className="dash-badge dash-badge-muted">optional</span>
                </label>
                <input
                  id="payway-merchant-khr"
                  className="dash-input"
                  type="text"
                  placeholder="Shown to the payer in their bank app"
                  value={khrMerchantName}
                  onChange={(e) => setKhrMerchantName(e.target.value)}
                />
              </div>
            </div>
            <div className="dash-hint">
              The riel link, for a store that accepts riel. Leave it blank if the store
              only takes dollars — you can add it later in the store&rsquo;s settings.
            </div>
          </>
        )}

        {linkError && <div className="dash-field-error">{linkError}</div>}

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

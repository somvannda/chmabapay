"use client";

import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import { useSession } from "@/components/portal/useSession";

function paywaySlug(input: string): string {
  const s = input.trim();
  const last = s.split(/[?#]/)[0].split("/").filter(Boolean).pop();
  return last || s;
}

type StoreLink = {
  link_type?: string | null;
  raw_link?: string | null;
  merchant_account_id?: string | null;
  merchant_name?: string | null;
  [k: string]: unknown;
};

type StoreSettings = {
  id: string;
  name?: string;
  support_email?: string | null;
  brand_color?: string | null;
  redirect_success_url?: string | null;
  redirect_failure_url?: string | null;
  logo_image_url?: string | null;
  whitelabel_css?: string | null;
  link?: StoreLink | null;
  telegram_chat_id?: string | null;
  [k: string]: unknown;
};

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

export default function StoreSettingsPage({
  params,
}: {
  params: { public_id: string };
}) {
  const publicId = params.public_id;
  const { profile } = useSession();
  // Checkout branding is an entitlement the backend enforces; unpaid accounts get
  // the fields locked here rather than a 403 on save.
  const brandingLocked = profile?.whitelabel_enabled !== true;

  const [loading, setLoading] = useState(true);
  const [settings, setSettings] = useState<StoreSettings | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const [name, setName] = useState("");
  const [supportEmail, setSupportEmail] = useState("");
  const [brandColor, setBrandColor] = useState("");
  const [successRedirect, setSuccessRedirect] = useState("");
  const [failureRedirect, setFailureRedirect] = useState("");
  const [logoImageUrl, setLogoImageUrl] = useState("");
  const [whitelabelCss, setWhitelabelCss] = useState("");
  const [storeSaving, setStoreSaving] = useState(false);
  const [storeSuccess, setStoreSuccess] = useState<string | null>(null);
  const [storeError, setStoreError] = useState<string | null>(null);

  const [paywayLink, setPaywayLink] = useState("");
  const [paywayMerchantName, setPaywayMerchantName] = useState("");
  const [paywayUnsupported, setPaywayUnsupported] = useState(false);
  const [plSaving, setPlSaving] = useState(false);
  const [plSuccess, setPlSuccess] = useState<string | null>(null);
  const [plError, setPlError] = useState<string | null>(null);

  const [telegramChatId, setTelegramChatId] = useState("");
  const [tgSaving, setTgSaving] = useState(false);
  const [tgSuccess, setTgSuccess] = useState<string | null>(null);
  const [tgError, setTgError] = useState<string | null>(null);
  const [tgTestSending, setTgTestSending] = useState(false);
  const [tgTestResult, setTgTestResult] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`/v1/stores/${publicId}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = (await res.json()) as StoreSettings;
          if (alive) {
            setSettings(data);
            setName(data.name || "");
            setSupportEmail(data.support_email || "");
            setBrandColor(data.brand_color || "");
            setSuccessRedirect(data.redirect_success_url || "");
            setFailureRedirect(data.redirect_failure_url || "");
            setLogoImageUrl(data.logo_image_url || "");
            setWhitelabelCss(data.whitelabel_css || "");
            const link = data.link ?? null;
            const legacyUnsupported = link?.link_type === "bakong_id";
            setPaywayUnsupported(legacyUnsupported);
            setPaywayLink(legacyUnsupported ? "" : link?.raw_link || "");
            setPaywayMerchantName(
              legacyUnsupported ? "" : link?.merchant_name || "",
            );
            setTelegramChatId(data.telegram_chat_id || "");
          }
        }
      } catch {
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId]);

  useEffect(() => {
    if (storeSuccess) {
      const id = setTimeout(() => setStoreSuccess(null), 2500);
      return () => clearTimeout(id);
    }
  }, [storeSuccess]);

  const handleStoreSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setStoreSaving(true);
      setStoreError(null);
      setStoreSuccess(null);
      setActionError(null);
      try {
        const body: Record<string, unknown> = {
          name,
          support_email: supportEmail || null,
          redirect_success_url: successRedirect || null,
          redirect_failure_url: failureRedirect || null,
        };
        if (!brandingLocked) {
          body.brand_color = brandColor || null;
          body.logo_image_url = logoImageUrl || null;
          body.whitelabel_css = whitelabelCss || null;
        }
        const res = await fetch(`/v1/stores/${publicId}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        setStoreSuccess("Saved!");
      } catch (e) {
        setStoreError(e instanceof Error ? e.message : String(e));
      } finally {
        setStoreSaving(false);
      }
    },
    [
      name,
      supportEmail,
      brandColor,
      successRedirect,
      failureRedirect,
      logoImageUrl,
      whitelabelCss,
      brandingLocked,
      publicId,
    ],
  );

  const handlePaymentLinkSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setPlSaving(true);
      setPlError(null);
      setPlSuccess(null);
      setActionError(null);
      try {
        const body: Record<string, unknown> = {
          link: {
            raw_link: paywayLink.trim(),
            merchant_account_id: paywaySlug(paywayLink),
            merchant_name: paywayMerchantName.trim() || name.trim(),
          },
        };
        const res = await fetch(`/v1/stores/${publicId}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        setPlSuccess("Saved!");
      } catch (e) {
        setPlError(e instanceof Error ? e.message : String(e));
      } finally {
        setPlSaving(false);
      }
    },
    [paywayLink, paywayMerchantName, name, publicId],
  );

  const handleTelegramSave = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setTgSaving(true);
      setTgError(null);
      setTgSuccess(null);
      setActionError(null);
      try {
        const body: Record<string, unknown> = {
          telegram_chat_id: telegramChatId || null,
        };
        const res = await fetch(`/v1/stores/${publicId}`, {
          method: "PATCH",
          credentials: "include",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(await readApiError(res));
        setTgSuccess("Saved!");
      } catch (e) {
        setTgError(e instanceof Error ? e.message : String(e));
      } finally {
        setTgSaving(false);
      }
    },
    [telegramChatId, publicId],
  );

  const handleSendTestTelegram = useCallback(async () => {
    setTgTestSending(true);
    setTgTestResult(null);
    setActionError(null);
    try {
      const res = await fetch(`/v1/stores/${publicId}/telegram/test`, {
        method: "POST",
        credentials: "include",
      });
      let msg = "";
      try {
        const json = (await res.json()) as { ok?: boolean; chat_id?: string; message?: string };
        msg = JSON.stringify(json);
      } catch {
        msg = await res.text().catch(() => "");
      }
      if (res.ok) {
        setTgTestResult(`Test sent: ${msg}`);
      } else {
        setTgTestResult(`Test failed: ${msg || `HTTP ${res.status}`}`);
      }
    } catch (e) {
      setTgTestResult(
        `Network error: ${e instanceof Error ? e.message : String(e)}`,
      );
    } finally {
      setTgTestSending(false);
    }
  }, [publicId]);

  useEffect(() => {
    if (tgTestResult) {
      const id = setTimeout(() => setTgTestResult(null), 4000);
      return () => clearTimeout(id);
    }
  }, [tgTestResult]);

  const paymentLinkUrl =
    typeof window !== "undefined"
      ? `${window.location.origin}/s/${publicId}`
      : `/s/${publicId}`;

  return (
    <div>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">Store settings</h2>
          <div className="dash-page-subtitle">
            Configure store details, payment links, and notifications
          </div>
        </div>
      </div>

      {loading ? (
        <div className="dash-info">Loading store settings…</div>
      ) : (
        <>
          {actionError && (
            <div className="dash-form-alert dash-form-alert-error">
              {actionError}
            </div>
          )}

          <div className="dash-panel">
            <div className="dash-panel-title">Store</div>
            <form className="dash-form" onSubmit={handleStoreSave}>
              {storeSuccess && (
                <div className="dash-form-alert dash-form-alert-success">
                  {storeSuccess}
                </div>
              )}
              {storeError && (
                <div className="dash-form-alert dash-form-alert-error">
                  {storeError}
                </div>
              )}

              <div className="dash-form-row">
                <div className="dash-field">
                  <label htmlFor="ss-name">Name</label>
                  <input
                    id="ss-name"
                    type="text"
                    className="dash-input"
                    value={name}
                    onChange={(e) => setName(e.target.value)}
                    placeholder="My store"
                  />
                </div>
                <div className="dash-field">
                  <label htmlFor="ss-email">Support email</label>
                  <input
                    id="ss-email"
                    type="email"
                    className="dash-input"
                    value={supportEmail}
                    onChange={(e) => setSupportEmail(e.target.value)}
                    placeholder="store@example.com"
                  />
                </div>
              </div>

              {brandingLocked && (
                <div className="dash-hint">
                  Brand color, logo, and custom CSS are white-label options that
                  this account does not have enabled. Contact ChmabaPay to turn
                  them on.
                </div>
              )}

              <div className="dash-form-row">
                <div className="dash-field">
                  <label htmlFor="ss-brand">Brand color (hex)</label>
                  <input
                    id="ss-brand"
                    type="text"
                    className="dash-input"
                    value={brandColor}
                    onChange={(e) => setBrandColor(e.target.value)}
                    placeholder="#6957F5"
                    disabled={brandingLocked}
                  />
                  <div className="dash-hint">
                    Used on receipts and checkout pages.
                  </div>
                </div>
                <div className="dash-field">
                  <label htmlFor="ss-success">Success redirect URL</label>
                  <input
                    id="ss-success"
                    type="url"
                    className="dash-input"
                    value={successRedirect}
                    onChange={(e) => setSuccessRedirect(e.target.value)}
                    placeholder="https://your-app.com/payment/success"
                  />
                  <div className="dash-hint">
                    Customer is sent here after a successful payment.
                  </div>
                </div>
              </div>

              <div className="dash-field">
                <label htmlFor="ss-failure">Failure redirect URL</label>
                <input
                  id="ss-failure"
                  type="url"
                  className="dash-input"
                  value={failureRedirect}
                  onChange={(e) => setFailureRedirect(e.target.value)}
                  placeholder="https://your-app.com/payment/failed"
                />
                <div className="dash-hint">
                  Customer is sent here after an expired or failed payment.
                </div>
              </div>

              <div className="dash-field">
                <label htmlFor="ss-logo">Logo image URL</label>
                <input
                  id="ss-logo"
                  type="url"
                  className="dash-input"
                  value={logoImageUrl}
                  onChange={(e) => setLogoImageUrl(e.target.value)}
                  placeholder="https://your-app.com/logo.png"
                  disabled={brandingLocked}
                />
                <div className="dash-hint">
                  Shown on the hosted checkout page and receipts.
                </div>
              </div>

              <div className="dash-field">
                <label htmlFor="ss-whitelabel-css">Custom checkout CSS</label>
                <textarea
                  id="ss-whitelabel-css"
                  className="dash-textarea"
                  value={whitelabelCss}
                  onChange={(e) => setWhitelabelCss(e.target.value)}
                  placeholder=".checkout { background: #0f172a; }"
                  disabled={brandingLocked}
                />
                <div className="dash-hint">
                  Injected into the hosted checkout page for this store.
                </div>
              </div>

              <div>
                <button
                  type="submit"
                  className="dash-btn dash-btn-primary"
                  disabled={storeSaving}
                >
                  {storeSaving ? "Saving…" : "Save store"}
                </button>
              </div>
            </form>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Payment link</div>
            <form className="dash-form" onSubmit={handlePaymentLinkSave}>
              {plSuccess && (
                <div className="dash-form-alert dash-form-alert-success">
                  {plSuccess}
                </div>
              )}
              {plError && (
                <div className="dash-form-alert dash-form-alert-error">
                  {plError}
                </div>
              )}

              <div className="dash-field">
                <label>Public payment link URL</label>
                <CopyField value={paymentLinkUrl} />
                <div className="dash-hint">
                  Share this link with customers. They can enter any amount and
                  pay instantly.
                </div>
              </div>

              {paywayUnsupported && (
                <div className="dash-warn">
                  <strong>Destination no longer supported.</strong> This store
                  uses a Bakong account destination, which is no longer
                  supported. Paste an ABA PayWay link below to replace it.
                </div>
              )}

              <div className="dash-field">
                <label htmlFor="ss-payway-link">ABA PayWay link</label>
                <input
                  id="ss-payway-link"
                  type="url"
                  className="dash-input"
                  value={paywayLink}
                  onChange={(e) => setPaywayLink(e.target.value)}
                  placeholder="https://link.payway.com.kh/ABAPAYpe518710Y"
                />
                <div className="dash-hint">
                  Paste your ABA PayWay share link. ABA PayWay is the only
                  supported payment destination.
                </div>
              </div>

              <div className="dash-field">
                <label htmlFor="ss-payway-merchant-name">
                  Merchant name{" "}
                  <span className="dash-badge dash-badge-muted">optional</span>
                </label>
                <input
                  id="ss-payway-merchant-name"
                  type="text"
                  className="dash-input"
                  value={paywayMerchantName}
                  onChange={(e) => setPaywayMerchantName(e.target.value)}
                  placeholder="Shown to the payer in their bank app"
                />
              </div>

              <div>
                <button
                  type="submit"
                  className="dash-btn dash-btn-primary"
                  disabled={plSaving}
                >
                  {plSaving ? "Saving…" : "Save changes"}
                </button>
              </div>
            </form>
          </div>

          <div className="dash-panel">
            <div className="dash-panel-title">Telegram notifications</div>
            <form className="dash-form" onSubmit={handleTelegramSave}>
              {tgSuccess && (
                <div className="dash-form-alert dash-form-alert-success">
                  {tgSuccess}
                </div>
              )}
              {tgError && (
                <div className="dash-form-alert dash-form-alert-error">
                  {tgError}
                </div>
              )}

              <div className="dash-hint">
                <ul className="dash-list-bulleted">
                  <li>
                    Add <code>@ChmabaPayBot</code> to your Telegram
                    group.
                  </li>
                  <li>
                    The bot immediately replies in the group with that
                    group&apos;s chat ID. To see it again later, send{" "}
                    <code>/id @ChmabaPayBot</code> in the group — the bot
                    only sees messages addressed to it.
                  </li>
                  <li>Paste the chat ID below and save.</li>
                </ul>
                Leave empty to turn notifications off. You will receive a
                Telegram message in your team&apos;s Telegram group every
                time this store takes a payment.
              </div>

              <div className="dash-field">
                <label htmlFor="ss-tgchat">Chat ID</label>
                <input
                  id="ss-tgchat"
                  type="text"
                  className="dash-input"
                  value={telegramChatId}
                  onChange={(e) => setTelegramChatId(e.target.value)}
                  placeholder="-1001234567890"
                />
              </div>

              {tgTestResult && (
                <div
                  className={
                    tgTestResult.startsWith("Test sent")
                      ? "dash-form-alert dash-form-alert-success"
                      : "dash-form-alert dash-form-alert-error"
                  }
                >
                  {tgTestResult}
                </div>
              )}

              <div className="dash-toolbar">
                <div />
                <div className="dash-toolbar-filters">
                  <button
                    type="button"
                    className="dash-btn dash-btn-secondary"
                    onClick={handleSendTestTelegram}
                    disabled={tgTestSending || !telegramChatId}
                  >
                    {tgTestSending ? "Sending…" : "Send test"}
                  </button>
                  <button
                    type="submit"
                    className="dash-btn dash-btn-primary"
                    disabled={tgSaving}
                  >
                    {tgSaving ? "Saving…" : "Save"}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </>
      )}
    </div>
  );
}

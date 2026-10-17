"use client";

import { useCallback, useEffect, useState } from "react";

import { useToast } from "@/components/Toast";
import { readApiError } from "@/lib/apiError";
import { apiFetch } from "@/lib/apiFetch";

/**
 * Where ChmabaPay's own subscription fees are collected.
 *
 * The platform sells plans, so it needs somewhere to receive the money — and that used
 * to be configuration only: a `CHMABAPAY_HQ_PAYWAY_LINK` line in the server's env file
 * plus a sign-in to seed the store. This is the same setting as a field, which is what
 * it should have been: the person who knows the PayWay link is the person looking at
 * this page.
 *
 * `source` is shown rather than hidden because it is the one genuinely confusing state:
 * a store pinned by `CHMABAPAY_HQ_STORE_ID` wins over whatever is saved here, so an
 * operator who has just saved a link and seen no change needs to be told that an
 * environment variable is deciding it.
 */

type HqStore = {
  configured: boolean;
  source: string;
  store_public_id?: string | null;
  store_status?: string | null;
  raw_link?: string | null;
  merchant_account_id?: string | null;
  merchant_name?: string | null;
};

const SOURCE_LABEL: Record<string, string> = {
  env: "pinned by CHMABAPAY_HQ_STORE_ID",
  console: "set here",
  fallback: "an existing platform store",
  none: "not set",
};

/**
 * Mirror of `services.payway_parser._extract_slug`, so the confirmation can echo
 * the exact merchant account id the API will derive from the link. Kept small and
 * literal: this is the one value the operator is being asked to check, so a
 * different answer here would be worse than no confirmation at all.
 */
function deriveMerchantAccountId(raw: string): string {
  const s = raw.trim();
  if (!s) return "";
  if (s.includes("://")) {
    try {
      const parsed = new URL(s);
      const path = parsed.pathname.replace(/\/+$/, "");
      if (path) return path.split("/").filter(Boolean).pop() || "";
    } catch {
      // Not a parseable URL; fall through to the string handling below.
    }
  }
  const base = "https://link.payway.com.kh";
  if (s.startsWith(base)) {
    let tail = s.slice(base.length).replace(/^\/+|\/+$/g, "");
    tail = tail.split("?")[0].split("#")[0];
    return tail || s;
  }
  let cut = s;
  if (cut.includes("?")) cut = cut.split("?")[0];
  if (cut.includes("#")) cut = cut.split("#")[0];
  return cut;
}

export function HqStorePanel() {
  const { notify } = useToast();
  const [store, setStore] = useState<HqStore | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [link, setLink] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch("/api/v1/admin/hq-store", { credentials: "include" });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as HqStore;
      setStore(data);
      setLink(data.raw_link ?? "");
    } catch (e) {
      setErrorMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Staging, not saving: this write reroutes *all* plan-fee revenue, and the
  // merchant account id is a shape-valid string whenever the link is, so a typo
  // passes every check the form can make. The confirmation echoes the resolved id
  // before the write.
  function requestSave(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    const trimmed = link.trim();
    if (!trimmed) {
      notify("Paste your ABA PayWay share link first.", "error");
      return;
    }
    if (!deriveMerchantAccountId(trimmed)) {
      notify("That link does not carry a merchant account id.", "error");
      return;
    }
    setReason("");
    setConfirmOpen(true);
  }

  async function applySave() {
    if (saving) return;
    const trimmed = link.trim();
    setSaving(true);
    try {
      const res = await apiFetch("/api/v1/admin/hq-store/link", {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          raw_link: trimmed,
          reason: reason.trim() || undefined,
        }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as HqStore;
      setStore(data);
      setLink(data.raw_link ?? trimmed);
      setConfirmOpen(false);
      setReason("");
      notify("Plan fees will now be collected into that link.");
    } catch (err) {
      notify(err instanceof Error ? err.message : String(err), "error");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dash-panel">
      <h2 className="dash-panel-title">Plan fee collection</h2>

      {loading ? (
        <div className="dash-info">Loading…</div>
      ) : errorMsg ? (
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
      ) : (
        <>
          {store?.configured ? (
            <div className="dash-hint">
              Subscription fees are collected into{" "}
              <strong>{store.merchant_account_id || "—"}</strong>
              {store.merchant_name ? ` (${store.merchant_name})` : ""}, currently{" "}
              {SOURCE_LABEL[store.source] ?? store.source}.
              {store.source === "env" || store.source === "fallback" ? (
                <>
                  {" "}
                  Saving below sets up a <strong>ChmabaPay HQ</strong> store, but this
                  deployment prefers the source above until{" "}
                  <code>CHMABAPAY_HQ_STORE_ID</code> is cleared.
                </>
              ) : null}
            </div>
          ) : (
            <div className="dash-hint">
              Not set up yet, so plan invoices cannot be paid. Paste the ABA PayWay
              share link you want subscription fees paid into — the same link you would
              share with a customer.
            </div>
          )}

          <form className="dash-form" onSubmit={requestSave}>
            <div className="dash-field">
              <label htmlFor="hq-payway-link">ABA PayWay link</label>
              <input
                id="hq-payway-link"
                type="text"
                className="dash-input"
                value={link}
                onChange={(e) => setLink(e.target.value)}
                placeholder="https://link.payway.com.kh/ABAPAY..."
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div>
              <button
                type="submit"
                className="dash-btn dash-btn-primary"
                disabled={saving}
              >
                {saving ? "Saving…" : "Save link"}
              </button>
            </div>
          </form>
        </>
      )}

      {confirmOpen && (
        <div className="dash-modal-backdrop" role="dialog" aria-modal="true">
          <div className="dash-modal">
            <div className="dash-modal-head">
              <h2 className="dash-modal-title">
                Change where plan fees are collected
              </h2>
              <button
                type="button"
                className="dash-modal-close"
                onClick={() => setConfirmOpen(false)}
                aria-label="Close"
              >
                ×
              </button>
            </div>
            <div className="dash-modal-body">
              <div className="dash-warn">
                Plan invoices will be collected into merchant account{" "}
                <strong>{deriveMerchantAccountId(link) || "—"}</strong>
                {store?.merchant_account_id &&
                store.merchant_account_id !== deriveMerchantAccountId(link) ? (
                  <>
                    {" "}
                    — replacing <strong>{store.merchant_account_id}</strong>
                  </>
                ) : null}
                . Check the identifier before saving: a wrong-but-well-formed link
                sends every future plan fee somewhere else.
              </div>
              <div className="dash-field">
                <label htmlFor="hq-reason">Reason (optional)</label>
                <textarea
                  id="hq-reason"
                  className="dash-textarea"
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                  rows={3}
                  maxLength={500}
                  placeholder="e.g. Moving collection to the new company ABA account."
                />
                <div className="dash-hint">
                  Stored in the audit trail next to the merchant account id.
                </div>
              </div>
              <div className="dash-modal-foot">
                <button
                  type="button"
                  className="dash-btn dash-btn-secondary"
                  onClick={() => setConfirmOpen(false)}
                  disabled={saving}
                >
                  Cancel
                </button>
                <button
                  type="button"
                  className="dash-btn dash-btn-primary"
                  onClick={() => void applySave()}
                  disabled={saving}
                >
                  {/* Not "Save link": the form button above carries that label, and
                      an operator who clicked it and stopped at this dialog — reading
                      the reason field as a required prompt — left without saving
                      anything. This is the button that writes, so it says so. */}
                  {saving ? "Saving…" : "Confirm and save"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

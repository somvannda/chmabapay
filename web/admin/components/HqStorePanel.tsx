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

export function HqStorePanel() {
  const { notify } = useToast();
  const [store, setStore] = useState<HqStore | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [link, setLink] = useState("");
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch("/v1/admin/hq-store", { credentials: "include" });
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

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (saving) return;
    const trimmed = link.trim();
    if (!trimmed) {
      notify("Paste your ABA PayWay share link first.", "error");
      return;
    }
    setSaving(true);
    try {
      const res = await apiFetch("/v1/admin/hq-store/link", {
        method: "PUT",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ raw_link: trimmed }),
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = (await res.json()) as HqStore;
      setStore(data);
      setLink(data.raw_link ?? trimmed);
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

          <form className="dash-form" onSubmit={save}>
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
    </div>
  );
}

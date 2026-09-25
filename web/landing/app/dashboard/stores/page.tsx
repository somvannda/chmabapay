"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { readApiError } from "@/components/portal/apiError";
import { useSession } from "@/components/portal/useSession";

type Store = {
  id: string;
  name: string;
  status: string;
  created_at?: string | null;
  // True for the platform's own store ("ChmabaPay HQ"), from which plan fees are
  // collected. Shown rather than hidden, so the owner sees why it is different.
  is_internal?: boolean;
  // The platform's billing hold: set when the account is on a plan smaller than its store
  // count. Orthogonal to `status`, which it never touches — so a held store still reads
  // "active" and the flag is the only thing that says it cannot mint a code.
  billing_suspended_at?: string | null;
  [k: string]: unknown;
};

/** Just the fields this page needs from an invoice, to name the amount that is owed. */
type Invoice = {
  status?: string | null;
  voided_at?: string | null;
  total_due_formatted?: string | null;
};

type SubscriptionResponse = {
  plan?: { max_stores?: number | null } | null;
};

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "-";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "-";
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

function pillClassForStatus(status: string): string {
  const s = (status || "").toLowerCase();
  if (s === "active") return "dash-pill dash-pill-paid";
  return "dash-pill dash-pill-expired";
}

export default function DashboardStoresPage() {
  // The hold is account-level, so whether a held store can be brought back is a property of
  // the account rather than of the store row.
  const { profile } = useSession();
  const [loading, setLoading] = useState(true);
  const [stores, setStores] = useState<Store[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [disablingId, setDisablingId] = useState<string | null>(null);
  const [enablingId, setEnablingId] = useState<string | null>(null);
  const [bringingBackId, setBringingBackId] = useState<string | null>(null);
  // Named only when a store is actually held, and deliberately allowed to stay null: an
  // amount that failed to load is left out rather than guessed at.
  const [owed, setOwed] = useState<string | null>(null);
  const [allowance, setAllowance] = useState<number | null>(null);

  const held = useMemo(
    () => stores.filter((s) => s.billing_suspended_at !== null && s.billing_suspended_at !== undefined),
    [stores],
  );
  const isFrozen = profile?.status === "restricted";

  /**
   * What is owed, and how many stores the plan allows.
   *
   * Fetched only once a held store has been seen, so the ordinary account pays nothing for a
   * panel it will never see. Both are reads the billing page already makes, and both stay
   * available while the account is frozen — which is the state this panel exists for.
   */
  const loadBilling = useCallback(async () => {
    try {
      const [subRes, invRes] = await Promise.all([
        fetch("/api/v1/billing/subscription", { credentials: "include" }),
        fetch("/api/v1/billing/invoices", { credentials: "include" }),
      ]);
      if (subRes.ok) {
        const sub = (await subRes.json().catch(() => ({}))) as SubscriptionResponse;
        if (typeof sub?.plan?.max_stores === "number") {
          setAllowance(sub.plan.max_stores);
        }
      }
      if (!invRes.ok) return;
      const data = await invRes.json().catch(() => ({}));
      const items: Invoice[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.data)
          ? data.data
          : Array.isArray(data?.items)
            ? data.items
            : [];
      const unpaid = items.find((inv) => inv.status !== "paid" && !inv.voided_at);
      if (unpaid?.total_due_formatted) setOwed(unpaid.total_due_formatted);
    } catch {
      // Deliberately silent. The amount is a courtesy here; the count and the link to the
      // billing page are what the merchant actually needs, and they render either way.
    }
  }, []);

  const fetchStores = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/api/v1/stores", { credentials: "include" });
      // A failed read used to leave `stores` empty, which the page then rendered as
      // "No stores yet." — the same screen as a fresh account, and the one state in
      // which a merchant might create a duplicate store.
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      const items: Store[] = Array.isArray(data)
        ? data
        : Array.isArray(data?.items)
          ? data.items
          : Array.isArray(data?.data)
            ? data.data
            : [];
      setStores(items);
      if (items.some((s) => s.billing_suspended_at)) void loadBilling();
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [loadBilling]);

  useEffect(() => {
    void fetchStores();
  }, [fetchStores]);

  /**
   * Swap a billing-held store back in, the same call the billing page's chooser makes.
   *
   * Not offered while the account is frozen: the platform's gate refuses every write except
   * the billing ones, so this would be a button that can only answer 403. The panel says so
   * and points at billing instead.
   */
  async function handleBringBack(store: Store) {
    setBringingBackId(store.id);
    try {
      const res = await fetch(`/api/v1/stores/${store.id}/activate`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) throw new Error(await readApiError(res));
      const data = await res.json().catch(() => ({}));
      await fetchStores();
      setFlash(
        data?.displaced?.name
          ? `"${store.name}" is live again. "${data.displaced.name}" was suspended to make room.`
          : `"${store.name}" is live again.`,
      );
      setTimeout(() => setFlash(null), 6000);
    } catch (e) {
      setFlash(e instanceof Error ? e.message : String(e));
      setTimeout(() => setFlash(null), 6000);
    } finally {
      setBringingBackId(null);
    }
  }

  async function handleDisable(store: Store) {
    if (!confirm(`Disable store "${store.name}"?`)) return;
    setDisablingId(store.id);
    try {
      const res = await fetch(`/api/v1/stores/${store.id}/disable`, {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json().catch(() => null);
        setStores((prev) =>
          prev.map((s) =>
            s.id === store.id
              ? { ...s, status: data?.status || "disabled" }
              : s,
          ),
        );
        setFlash(`Store "${store.name}" has been disabled.`);
        setTimeout(() => setFlash(null), 4000);
      } else if (res.status === 400) {
        const err = await res.json().catch(() => ({}));
        setFlash(err?.detail || "Could not disable store.");
        setTimeout(() => setFlash(null), 6000);
      }
    } catch {
      setFlash("Network error while disabling store.");
      setTimeout(() => setFlash(null), 4000);
    } finally {
      setDisablingId(null);
    }
  }

  /**
   * Reverse a disable.
   *
   * This exists because the button below had no counterpart. Disabling a store
   * stops its links, keys and webhooks from working, and none of the other write
   * paths will touch it — a settings save and attaching a link both refuse a
   * disabled store with `store_disabled`. So one press used to be permanent.
   *
   * The status it comes back as is decided by the backend: `active` if it still has
   * a payment link, `draft` if it does not. The merchant is told which, because
   * "enabled" next to a store that still cannot take payments would be a lie.
   */
  async function handleEnable(store: Store) {
    if (
      !confirm(
        `Enable store "${store.name}"?\n\nIts payment link starts accepting payments again.`,
      )
    ) {
      return;
    }
    setEnablingId(store.id);
    try {
      const res = await fetch(`/api/v1/stores/${store.id}/enable`, {
        method: "POST",
        credentials: "include",
      });
      if (res.ok) {
        const data = await res.json().catch(() => null);
        const status = data?.status || "active";
        setStores((prev) =>
          prev.map((s) => (s.id === store.id ? { ...s, status } : s)),
        );
        setFlash(
          status === "draft"
            ? `Store "${store.name}" is enabled again, but it has no payment link, so it still cannot take payments. Add one to finish.`
            : `Store "${store.name}" is enabled and accepting payments again.`,
        );
        setTimeout(() => setFlash(null), status === "draft" ? 8000 : 4000);
      } else {
        const err = await res.json().catch(() => ({}));
        setFlash(err?.detail || "Could not enable store.");
        setTimeout(() => setFlash(null), 6000);
      }
    } catch {
      setFlash("Network error while enabling store.");
      setTimeout(() => setFlash(null), 4000);
    } finally {
      setEnablingId(null);
    }
  }

  return (
    <>
      <div className="dash-page-head">
        <div>
          <h2 className="dash-page-title">Stores</h2>
          <div className="dash-page-subtitle">
            Manage stores and payment destinations
          </div>
        </div>
        <div>
          <Link
            className="dash-btn dash-btn-primary"
            href="/dashboard/stores/new"
          >
            + New store
          </Link>
        </div>
      </div>

      {/* The one place a merchant learns that a store stopped for a billing reason rather than
          their own. Without it a held store reads "active" and they cannot see why no codes
          are being minted — which is the state that ends in a support call instead of a
          payment (§7.6). */}
      {held.length > 0 && (
        <div className="dash-warn dash-warn-sm">
          <strong>
            {held.length} {held.length === 1 ? "store is" : "stores are"} suspended — plan
            limit.
          </strong>{" "}
          {allowance !== null
            ? `Your plan allows ${allowance} store${allowance === 1 ? "" : "s"}. `
            : ""}
          {owed ? `${owed} is unpaid on your plan invoice. ` : ""}
          {isFrozen
            ? "Your account is on hold, so settling the invoice or moving to a plan you can afford is what brings them back."
            : "Bring one back below, and whichever store makes room for it is suspended in its place, so you never go over the allowance."}{" "}
          <Link className="dash-link-btn" href="/dashboard/billing">
            Plan &amp; billing
          </Link>
        </div>
      )}

      {flash && (
        <div className="dash-warn dash-warn-sm">
          {flash.includes("Upgrade") || flash.includes("max") ? (
            <>
              <strong>Limit reached.</strong> {flash}{" "}
              <Link className="dash-link-btn" href="/dashboard/billing">
                Upgrade plan
              </Link>
            </>
          ) : (
            flash
          )}
        </div>
      )}

      <div className="dash-panel">
        {loading ? (
          <div className="dash-empty">Loading stores…</div>
        ) : loadError ? (
          <div className="dash-warn">
            Your stores could not be loaded, so this list is unknown rather than
            empty. Any store you already created is still working.
            <div className="dash-empty-cta-row">
              <button
                type="button"
                className="dash-btn dash-btn-secondary dash-btn-sm"
                onClick={() => void fetchStores()}
              >
                Retry
              </button>
            </div>
          </div>
        ) : stores.length === 0 ? (
          <div className="dash-empty">
            No stores yet.
            <div className="dash-empty-desc">
              Create your first store to connect a payment destination and
              start accepting payments.
              <div className="dash-empty-cta-row">
                <Link
                  className="dash-btn dash-btn-primary dash-btn-sm"
                  href="/dashboard/stores/new"
                >
                  + Create store
                </Link>
              </div>
            </div>
          </div>
        ) : (
          <table className="dash-table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Status</th>
                <th>Created</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {stores.map((s) => {
                // A held store keeps whatever `status` it had, so the flag is the only thing
                // that tells the merchant why it stopped — `active` beside a store that cannot
                // mint a code is the whole confusion this badge exists to remove.
                const isHeld = s.billing_suspended_at !== null && s.billing_suspended_at !== undefined;
                return (
                  <tr key={s.id}>
                    <td>
                      <Link
                        className="dash-link-btn"
                        href={`/dashboard/${s.id}`}
                      >
                        {s.name}
                      </Link>
                      {s.is_internal && (
                        <span className="dash-badge dash-badge-muted">
                          {" "}
                          platform store
                        </span>
                      )}
                    </td>
                    <td>
                      {isHeld ? (
                        <span className="dash-pill dash-pill-expired">
                          suspended — plan limit
                        </span>
                      ) : (
                        <span className={pillClassForStatus(s.status)}>
                          {(s.status || "unknown").toLowerCase()}
                        </span>
                      )}
                    </td>
                    <td>{formatDate(s.created_at)}</td>
                    <td>
                      <div className="dash-toolbar-filters">
                        <Link
                          className="dash-btn dash-btn-secondary dash-btn-sm"
                          href={`/dashboard/${s.id}`}
                        >
                          View
                        </Link>
                        {isHeld &&
                          (isFrozen ? (
                            <Link
                              className="dash-btn dash-btn-primary dash-btn-sm"
                              href="/dashboard/billing"
                            >
                              Resolve on billing
                            </Link>
                          ) : (
                            <button
                              type="button"
                              className="dash-btn dash-btn-primary dash-btn-sm"
                              onClick={() => handleBringBack(s)}
                              disabled={bringingBackId === s.id}
                            >
                              {bringingBackId === s.id ? "…" : "Bring back"}
                            </button>
                          ))}
                        {(s.status || "").toLowerCase() === "disabled" ? (
                          <button
                            type="button"
                            className="dash-btn dash-btn-secondary dash-btn-sm"
                            onClick={() => handleEnable(s)}
                            disabled={enablingId === s.id}
                          >
                            {enablingId === s.id ? "…" : "Enable"}
                          </button>
                        ) : (
                          <button
                            type="button"
                            className="dash-btn dash-btn-danger dash-btn-sm"
                            onClick={() => handleDisable(s)}
                            disabled={
                              disablingId === s.id ||
                              (s.status || "").toLowerCase() !== "active"
                            }
                          >
                            {disablingId === s.id ? "…" : "Disable"}
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

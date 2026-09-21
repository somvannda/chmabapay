"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { readApiError } from "@/components/portal/apiError";

type Store = {
  id: string;
  name: string;
  status: string;
  created_at?: string | null;
  // True for the platform's own store ("ChmabaPay HQ"), from which plan fees are
  // collected. Shown rather than hidden, so the owner sees why it is different.
  is_internal?: boolean;
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
  });
}

function pillClassForStatus(status: string): string {
  const s = (status || "").toLowerCase();
  if (s === "active") return "dash-pill dash-pill-paid";
  return "dash-pill dash-pill-expired";
}

export default function DashboardStoresPage() {
  const [loading, setLoading] = useState(true);
  const [stores, setStores] = useState<Store[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [disablingId, setDisablingId] = useState<string | null>(null);
  const [enablingId, setEnablingId] = useState<string | null>(null);

  const fetchStores = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetch("/v1/stores", { credentials: "include" });
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
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchStores();
  }, [fetchStores]);

  async function handleDisable(store: Store) {
    if (!confirm(`Disable store "${store.name}"?`)) return;
    setDisablingId(store.id);
    try {
      const res = await fetch(`/v1/stores/${store.id}/disable`, {
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
      const res = await fetch(`/v1/stores/${store.id}/enable`, {
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
              {stores.map((s) => (
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
                    <span className={pillClassForStatus(s.status)}>
                      {(s.status || "unknown").toLowerCase()}
                    </span>
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
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

type StoreInfo = {
  id: string;
  name: string;
  status?: string;
  [k: string]: unknown;
};

const STATUS_PILL: Record<string, string> = {
  active: "dash-pill dash-pill-paid",
  draft: "dash-pill dash-pill-pending",
  link_pending: "dash-pill dash-pill-scanned",
  disabled: "dash-pill dash-pill-failed",
};

const STORE_TABS = [
  { key: "overview", href: "", label: "Overview" },
  { key: "payments", href: "/payments", label: "Payments" },
  { key: "settings", href: "/settings", label: "Settings" },
] as const;

function activeTabFromPath(pathname: string, base: string): string {
  const rest = (pathname || "").slice(base.length) || "/";
  if (rest === "/" || rest === "") return "overview";
  if (rest.startsWith("/payments")) return "payments";
  if (rest.startsWith("/settings")) return "settings";
  return "overview";
}

export default function StoreScopedLayout({
  params,
  children,
}: {
  params: { public_id: string };
  children: React.ReactNode;
}) {
  const publicId = params.public_id;
  const basePath = `/dashboard/${publicId}`;
  const pathname = usePathname() || basePath;

  const [storeLoading, setStoreLoading] = useState(true);
  const [store, setStore] = useState<StoreInfo | null>(null);
  const [stores, setStores] = useState<StoreInfo[]>([]);
  const [switcherOpen, setSwitcherOpen] = useState(false);
  const switcherRef = useRef<HTMLDivElement | null>(null);

  const activeTab = useMemo(
    () => activeTabFromPath(pathname, basePath),
    [pathname, basePath],
  );

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch(`/v1/stores/${publicId}`, {
          credentials: "include",
        });
        if (res.ok) {
          const data = await res.json();
          if (alive) setStore(data as StoreInfo);
        }
      } catch {
      } finally {
        if (alive) setStoreLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [publicId]);

  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const res = await fetch("/v1/stores", { credentials: "include" });
        if (res.ok) {
          const data = await res.json().catch(() => ({}));
          const items: StoreInfo[] = Array.isArray(data)
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
    if (!switcherOpen) return;
    const handler = (e: MouseEvent) => {
      if (switcherRef.current && !switcherRef.current.contains(e.target as Node)) {
        setSwitcherOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [switcherOpen]);

  const storeName = store?.name || "Store";
  const status = (store?.status || "").toLowerCase();

  return (
    <div className="cp-store">
      <div className="cp-store-bar">
        <div className="cp-switcher" ref={switcherRef}>
          <button
            type="button"
            className="cp-switcher-btn"
            onClick={() => setSwitcherOpen((v) => !v)}
            aria-expanded={switcherOpen}
            aria-haspopup="menu"
          >
            <span className="cp-switcher-eyebrow">Store</span>
            <span className="cp-switcher-name" title={storeName}>
              {storeLoading ? "Loading…" : storeName}
            </span>
            <svg
              className="cp-switcher-caret"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              aria-hidden="true"
            >
              <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </button>
          {switcherOpen && (
            <div className="cp-switcher-menu" role="menu">
              {stores.length === 0 ? (
                <div className="cp-switcher-empty">No other stores</div>
              ) : (
                stores.map((s) => {
                  const isCurrent = s.id === publicId;
                  return (
                    <Link
                      key={s.id}
                      href={`/dashboard/${s.id}`}
                      role="menuitem"
                      className={`cp-switcher-item${
                        isCurrent ? " cp-switcher-item-active" : ""
                      }`}
                      onClick={() => setSwitcherOpen(false)}
                    >
                      <span className="cp-switcher-item-name">
                        {s.name || `Store #${s.id}`}
                      </span>
                      {isCurrent && (
                        <span className="cp-switcher-item-tick" aria-hidden="true">
                          ✓
                        </span>
                      )}
                    </Link>
                  );
                })
              )}
            </div>
          )}
        </div>
        {status && (
          <span className={STATUS_PILL[status] || "dash-pill dash-pill-pending"}>
            {status.replace("_", " ")}
          </span>
        )}
      </div>

      <nav className="cp-tabs" aria-label="Store sections">
        {STORE_TABS.map((tab) => {
          const isActive = activeTab === tab.key;
          return (
            <Link
              key={tab.key}
              href={`${basePath}${tab.href}`}
              className={`cp-tab${isActive ? " cp-tab-active" : ""}`}
              aria-current={isActive ? "page" : undefined}
            >
              {tab.label}
            </Link>
          );
        })}
      </nav>

      {children}
    </div>
  );
}

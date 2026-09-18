"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { ToastProvider } from "./Toast";

/* ------------------------------------------------------------------ *
 * Nav model
 * ------------------------------------------------------------------ */

type NavItem = {
  key: string;
  href: string;
  label: string;
  icon: React.ReactNode;
};

const I = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="2" />
      <rect x="14" y="3" width="7" height="7" rx="2" />
      <rect x="3" y="14" width="7" height="7" rx="2" />
      <rect x="14" y="14" width="7" height="7" rx="2" />
    </>
  ),
  stores: (
    <>
      <path d="M4 10v9a1 1 0 0 0 1 1h14a1 1 0 0 0 1-1v-9" />
      <path d="M3 6.5 4.6 3.6A1 1 0 0 1 5.5 3h13a1 1 0 0 1 .9.6L21 6.5a3 3 0 0 1-6 0 3 3 0 0 1-6 0 3 3 0 0 1-6 0Z" />
      <path d="M10 20v-5h4v5" />
    </>
  ),
  payments: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="3" />
      <path d="M2.5 10h19" />
      <path d="M6.5 14.5h3" />
    </>
  ),
  reports: (
    <>
      <path d="M4 20V10" />
      <path d="M10 20V4" />
      <path d="M16 20v-7" />
      <path d="M22 20H2" />
    </>
  ),
  keys: (
    <>
      <circle cx="8" cy="14" r="4" />
      <path d="M11 11.5 19.5 3" />
      <path d="M17 5.5 19.5 8" />
      <path d="M14.5 8 17 10.5" />
    </>
  ),
  webhooks: (
    <>
      <path d="M13 2 4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5Z" />
    </>
  ),
  billing: (
    <>
      <path d="M5 2.5h14a1 1 0 0 1 1 1v18l-3-2-3 2-3-2-3 2-3-2v-16a1 1 0 0 1 1-1Z" />
      <path d="M9 8h6" />
      <path d="M9 12h6" />
    </>
  ),
  settings: (
    <>
      <path d="M4 7h10" />
      <path d="M18 7h2" />
      <path d="M4 17h6" />
      <path d="M14 17h6" />
      <circle cx="16" cy="7" r="2.2" />
      <circle cx="12" cy="17" r="2.2" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9.5" />
      <path d="M9.6 9.4a2.5 2.5 0 1 1 3.3 2.4c-.6.2-.9.8-.9 1.4v.4" />
      <path d="M12 17h.01" />
    </>
  ),
} as const;

const NAV_GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "Workspace",
    items: [
      { key: "overview", href: "/dashboard", label: "Overview", icon: I.overview },
      { key: "stores", href: "/dashboard/stores", label: "Stores", icon: I.stores },
      { key: "payments", href: "/dashboard/payments", label: "Payments", icon: I.payments },
      { key: "reports", href: "/dashboard/reports", label: "Reports", icon: I.reports },
    ],
  },
  {
    label: "Developers",
    items: [
      { key: "keys", href: "/dashboard/keys", label: "API keys", icon: I.keys },
      { key: "webhooks", href: "/dashboard/webhooks", label: "Webhooks", icon: I.webhooks },
    ],
  },
  {
    label: "Account",
    items: [
      { key: "billing", href: "/dashboard/billing", label: "Billing", icon: I.billing },
      { key: "settings", href: "/dashboard/settings", label: "Settings", icon: I.settings },
      { key: "help", href: "/dashboard/help", label: "Help", icon: I.help },
    ],
  },
];

const KNOWN_SECTIONS = new Set([
  "stores",
  "payments",
  "reports",
  "keys",
  "webhooks",
  "billing",
  "settings",
  "help",
]);

const SECTION_LABELS: Record<string, string> = {
  overview: "Overview",
  stores: "Stores",
  payments: "Payments",
  reports: "Reports",
  keys: "API keys",
  webhooks: "Webhooks",
  billing: "Billing",
  settings: "Settings",
  help: "Help",
};

export function activeNavFromPath(pathname: string): string {
  const p = pathname || "/dashboard";
  if (p === "/dashboard" || p === "/dashboard/" || p === "") return "overview";
  if (!p.startsWith("/dashboard/")) return "overview";
  const seg = p.slice("/dashboard/".length).split("/")[0];
  if (!seg) return "overview";
  if (KNOWN_SECTIONS.has(seg)) return seg;
  // Any other first segment is a store detail route: /dashboard/<store_id>
  return "stores";
}

function breadcrumbFromPath(pathname: string): string[] {
  const active = activeNavFromPath(pathname);
  const crumbs = [SECTION_LABELS[active] || "Overview"];
  if (active === "stores") {
    const seg = (pathname || "")
      .slice("/dashboard/".length)
      .split("/")
      .filter(Boolean);
    if (seg[0] === "stores" && seg[1]) crumbs.push(seg[1]);
    else if (seg[0] && !KNOWN_SECTIONS.has(seg[0])) crumbs.push(seg[0]);
  }
  return crumbs;
}

function initialsOf(value: string): string {
  const parts = value
    .split(/[\s.@_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "");
  return parts.join("") || "CP";
}

/* ------------------------------------------------------------------ *
 * Shell
 * ------------------------------------------------------------------ */

export type DashboardShellProps = {
  profile: { email?: string; name?: string | null } | null;
  planName: string;
  planCode: string;
  used: number;
  limit: number;
  resetsLabel?: string;
  planLoaded?: boolean;
  onSignOut: () => void;
  signoutLoading?: boolean;
  children: React.ReactNode;
};

export function DashboardShell({
  profile,
  planName,
  planCode,
  used,
  limit,
  resetsLabel,
  planLoaded = false,
  onSignOut,
  signoutLoading = false,
  children,
}: DashboardShellProps) {
  const pathname = usePathname() || "/dashboard";
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [menuOpen]);

  const activeNav = useMemo(() => activeNavFromPath(pathname), [pathname]);
  const crumbs = useMemo(() => breadcrumbFromPath(pathname), [pathname]);

  const email = profile?.email || "";
  const displayName = profile?.name || email || "Your account";
  const initials = initialsOf(profile?.name || email || "CP");

  const safeLimit = limit > 0 ? limit : 0;
  const pct =
    safeLimit > 0 ? Math.min(100, Math.round((used / safeLimit) * 100)) : 0;
  const nearLimit = safeLimit > 0 && used / safeLimit >= 0.8;
  const showUpgrade = planCode.toLowerCase() !== "pro";

  return (
    <div className="cp-app">
      <aside className="cp-side">
        <Link href="/dashboard" className="cp-brand" aria-label="ChmabaPay dashboard">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="cp-brand-mark" src="/logo.svg" alt="" width={30} height={30} />
          <span className="cp-brand-word">
            <span className="cp-brand-name">chmaba</span>
            <span className="cp-brand-pay">Pay</span>
          </span>
        </Link>

        <nav className="cp-nav" aria-label="Workspace">
          {NAV_GROUPS.map((group) => (
            <div className="cp-nav-group" key={group.label}>
              <div className="cp-nav-group-label">{group.label}</div>
              {group.items.map((item) => {
                const isActive = activeNav === item.key;
                return (
                  <Link
                    key={item.key}
                    href={item.href}
                    className={`cp-nav-item${isActive ? " cp-nav-item-active" : ""}`}
                    aria-current={isActive ? "page" : undefined}
                    title={item.label}
                  >
                    <svg
                      className="cp-nav-icon"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="1.6"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      {item.icon}
                    </svg>
                    <span className="cp-nav-label">{item.label}</span>
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="cp-side-foot">
          <div className="cp-plan">
            {planLoaded ? (
              <>
                <div className="cp-plan-top">
                  <span className="cp-plan-name">{planName}</span>
                  {showUpgrade && (
                    <Link className="cp-plan-upgrade" href="/dashboard/billing">
                      Upgrade
                    </Link>
                  )}
                </div>
                {safeLimit > 0 && (
                  <>
                    <div className="cp-plan-bar">
                      <span
                        className={`cp-plan-fill${nearLimit ? " cp-plan-fill-warn" : ""}`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <div className="cp-plan-usage">
                      {used} / {limit} payments
                      {resetsLabel ? ` · resets ${resetsLabel}` : ""}
                    </div>
                  </>
                )}
                <Link className="cp-plan-manage" href="/dashboard/billing">
                  Manage plan
                </Link>
              </>
            ) : (
              <div className="cp-plan-usage">Loading plan…</div>
            )}
          </div>

          <div className="cp-user">
            <span className="cp-user-avatar" aria-hidden="true">
              {initials}
            </span>
            <span className="cp-user-meta">
              <span className="cp-user-name" title={displayName}>
                {displayName}
              </span>
              {email && displayName !== email && (
                <span className="cp-user-email" title={email}>
                  {email}
                </span>
              )}
            </span>
          </div>
        </div>
      </aside>

      <div className="cp-body">
        <header className="cp-topbar">
          <div className="cp-topbar-inner">
            <nav className="cp-crumbs" aria-label="Breadcrumb">
              <Link className="cp-crumb cp-crumb-root" href="/dashboard">
                Workspace
              </Link>
              {crumbs.map((c, idx) => (
                <span className="cp-crumb-wrap" key={`${c}-${idx}`}>
                  <span className="cp-crumb-sep" aria-hidden="true">
                    /
                  </span>
                  <span
                    className={
                      idx === crumbs.length - 1 ? "cp-crumb cp-crumb-current" : "cp-crumb"
                    }
                  >
                    {c}
                  </span>
                </span>
              ))}
            </nav>

            <div className="cp-topbar-actions">
              <a className="cp-toplink" href="/api/docs">
                API docs
              </a>
              <Link className="cp-toplink" href="/dashboard/help">
                Help
              </Link>
              <div className="cp-menu" ref={menuRef}>
                <button
                  type="button"
                  className="cp-menu-btn"
                  onClick={() => setMenuOpen((v) => !v)}
                  aria-expanded={menuOpen}
                  aria-haspopup="menu"
                >
                  <span className="cp-menu-avatar" aria-hidden="true">
                    {initials}
                  </span>
                  <span className="cp-menu-chevron" aria-hidden="true">
                    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="m6 9 6 6 6-6" strokeLinecap="round" strokeLinejoin="round" />
                    </svg>
                  </span>
                </button>
                {menuOpen && (
                  <div className="cp-menu-pop" role="menu">
                    <div className="cp-menu-head">
                      <div className="cp-menu-head-name" title={displayName}>
                        {displayName}
                      </div>
                      {email && <div className="cp-menu-head-email">{email}</div>}
                    </div>
                    <Link
                      className="cp-menu-item"
                      href="/dashboard/settings"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                    >
                      Account settings
                    </Link>
                    <Link
                      className="cp-menu-item"
                      href="/dashboard/billing"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                    >
                      Billing &amp; plans
                    </Link>
                    <a className="cp-menu-item" href="/" role="menuitem">
                      Back to website
                    </a>
                    <button
                      type="button"
                      className="cp-menu-item cp-menu-item-danger"
                      role="menuitem"
                      onClick={onSignOut}
                      disabled={signoutLoading}
                    >
                      {signoutLoading ? "Signing out…" : "Sign out"}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </div>
        </header>

        <main className="cp-main">
          <ToastProvider>{children}</ToastProvider>
        </main>

        <footer className="cp-foot">
          <div className="cp-foot-inner">
            <div className="cp-foot-brand">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img className="cp-foot-mark" src="/logo.svg" alt="" width={20} height={20} />
              <span className="cp-foot-copy">
                © 2026 ChmabaPay Technologies
              </span>
            </div>
            <nav className="cp-foot-links" aria-label="Footer">
              <a className="cp-foot-link" href="/">
                Website
              </a>
              <a className="cp-foot-link" href="/api/docs">
                API docs
              </a>
              <Link className="cp-foot-link" href="/dashboard/help">
                Help
              </Link>
              <Link className="cp-foot-link" href="/dashboard/billing">
                Pricing
              </Link>
            </nav>
          </div>
        </footer>
      </div>
    </div>
  );
}

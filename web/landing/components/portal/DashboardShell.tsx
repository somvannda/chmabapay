"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import { BillingNotice, type BillingNoticePayload } from "./BillingNotice";
import { ToastProvider } from "./Toast";
import { ThemeToggle } from "@shared/ui";
import { ChmabaLogo } from "@shared/chrome";

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
  support: (
    <>
      <path d="M20.5 11.5a8.5 8.5 0 0 1-11.9 7.9L3.5 20.5l1.1-5.1A8.5 8.5 0 1 1 20.5 11.5Z" />
      <path d="M8.5 10.5h7" />
      <path d="M8.5 13.5h4.5" />
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
      { key: "support", href: "/dashboard/support", label: "Support", icon: I.support },
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
  "support",
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
  support: "Support",
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
  /**
   * The single most urgent billing notice, from `GET /api/v1/billing/notices`. Fetched by the layout
   * rather than here, so the shell stays presentational and the notice refreshes on the same
   * `chmabapay:plan-changed` event that refreshes the plan card.
   */
  notice?: BillingNoticePayload | null;
  /**
   * The account is on hold for non-payment (`status === "restricted"`). Computing it in the
   * layout keeps the shell from having to know which account statuses mean what.
   */
  readOnly?: boolean;
  children: React.ReactNode;
};

const nf = new Intl.NumberFormat("en-US");

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
  notice = null,
  readOnly = false,
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

  // Billing is the one page that still accepts input while the account is on hold — it is how the
  // hold is lifted — so it is the one page the read-only treatment is not applied to.
  const billingIsTheWayOut = pathname.startsWith("/dashboard/billing");

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-[252px] flex-col bg-[#17181c] px-3.5 py-5 text-white lg:flex">
        <div className="px-3">
          <Link href="/dashboard" aria-label="ChmabaPay dashboard">
            <ChmabaLogo light suffix="Pay" />
          </Link>
        </div>

        <div className="mt-8 flex-1 overflow-y-auto px-2">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-5">
              <p className="mb-2 px-2 text-[10px] font-bold uppercase tracking-[.15em] text-[#686970]">
                {group.label}
              </p>
              <nav className="space-y-1" aria-label={group.label}>
                {group.items.map((item) => {
                  const isActive = activeNav === item.key;
                  return (
                    <Link
                      key={item.key}
                      href={item.href}
                      aria-current={isActive ? "page" : undefined}
                      className={`flex h-10 w-full items-center gap-3 rounded-xl px-3 text-left text-[12px] font-semibold transition ${
                        isActive
                          ? "bg-[#6957f5] text-white shadow-[0_7px_18px_rgba(105,87,245,.25)]"
                          : "text-[#8b8c95] hover:bg-[#292a30] hover:text-white"
                      }`}
                    >
                      <svg
                        className="h-4 w-4 shrink-0"
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
                      <span>{item.label}</span>
                      {isActive && (
                        <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#c4f27c]" />
                      )}
                    </Link>
                  );
                })}
              </nav>
            </div>
          ))}
        </div>

        <div className="mt-auto">
          <div className="mb-3 rounded-xl border border-[#33343a] bg-[#202126] p-3">
            {planLoaded ? (
              <>
                <div className="flex items-center justify-between">
                  <span className="text-[11px] font-bold">{planName}</span>
                  {showUpgrade && (
                    <Link className="text-[10px] font-bold text-[#c4f27c] hover:underline" href="/dashboard/billing">
                      Upgrade
                    </Link>
                  )}
                </div>
                {safeLimit > 0 && (
                  <>
                    <div
                      className="mt-2 h-1 w-full overflow-hidden rounded-full bg-[#2a2b32]"
                      role="progressbar"
                      aria-valuenow={pct}
                      aria-valuemin={0}
                      aria-valuemax={100}
                      aria-label="Monthly payment quota used"
                    >
                      <div
                        className={`h-full rounded-full ${nearLimit ? "bg-[#e0a23c]" : "bg-[#c4f27c]"}`}
                        style={{ width: pct > 0 ? `${Math.max(pct, 3)}%` : "0%" }}
                      />
                    </div>
                    <div className="mt-1.5 text-[9px] text-[#85868e]">
                      {nf.format(used)} / {nf.format(limit)} payments this month
                      {resetsLabel ? ` · resets ${resetsLabel}` : ""}
                    </div>
                  </>
                )}
                <Link
                  className="mt-2 inline-block text-[10px] font-bold text-[#9a9aa4] hover:text-white"
                  href="/dashboard/billing"
                >
                  Manage plan
                </Link>
              </>
            ) : (
              <div className="text-[9px] text-[#85868e]">Loading plan…</div>
            )}
          </div>

          <div className="flex items-center gap-2 px-1">
            <span
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-[#c4f27c] text-[10px] font-extrabold text-[#1d2817]"
              aria-hidden="true"
            >
              {initials}
            </span>
            <span className="min-w-0">
              <span className="block truncate text-[11px] font-bold" title={displayName}>
                {displayName}
              </span>
              {email && displayName !== email && (
                <span className="block truncate text-[9px] text-[#85868e]" title={email}>
                  {email}
                </span>
              )}
            </span>
          </div>
        </div>
      </aside>

      <div className="lg:pl-[252px]">
        <header className="flex min-h-[74px] items-center justify-between gap-4 border-b border-[#e9e9ef] bg-[#fafafd]/90 px-5 backdrop-blur dark:border-[#2a2b30] dark:bg-[#1a1b1f]/90 lg:px-8">
          <div className="min-w-0">
            <h1 className="truncate text-[18px] font-extrabold tracking-[-.04em]">
              {crumbs[crumbs.length - 1] || "Overview"}
            </h1>
            <p className="mt-0.5 truncate text-[11px] text-[#898a95]">ChmabaPay workspace</p>
          </div>

          <div className="cp-topbar-actions">
              <ThemeToggle />
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
                  <span className="cp-menu-name" title={email || displayName}>
                    {displayName}
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
        </header>

        {/* Above `<main>`, and outside it, for two reasons: it must be on screen on every
            /dashboard/* page without each page rendering it, and it must stay clickable while the
            page below is read-only — its action is the way out of that state. */}
        <BillingNotice notice={notice} />

        <main
          className={
            readOnly && !billingIsTheWayOut
              ? "cp-main cp-main-readonly mx-auto w-full max-w-[1200px] p-5 lg:p-8"
              : "cp-main mx-auto w-full max-w-[1200px] p-5 lg:p-8"
          }
        >
          {/* The only *native* way to disable a whole subtree at once, and the reason it is worth
              a wrapper: CSS can stop a mouse click but not a keyboard, and a merchant who tabs
              into a field and types would meet a 403 instead of a locked form. The class above
              makes it look locked; this makes it be locked. A disabled `fieldset` disables every
              form control inside it — inputs, selects, buttons — and leaves links alone, which is
              what the read-only state wants (the banner's way out is an anchor).
              `display: block` with the resets below keeps it out of the layout entirely. */}
          <fieldset
            className="cp-readonly"
            disabled={readOnly && !billingIsTheWayOut}
          >
            <ToastProvider>{children}</ToastProvider>
          </fieldset>
        </main>
      </div>
    </div>
  );
}

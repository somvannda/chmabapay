"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { ToastProvider } from "./Toast";
import { ThemeToggle } from "./ThemeToggle";
import { ChmabaLogo } from "@shared/chrome";
import { Badge } from "@shared/ui";
import { apiFetch } from "@/lib/apiFetch";

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
  accounts: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
      <path d="M16 11.5a3 3 0 1 0 0-6" />
      <path d="M18 20c0-2.4-.8-4.3-2.2-5.4 3 .2 5.2 2.3 5.2 5.4" />
    </>
  ),
  payments: (
    <>
      <rect x="2.5" y="6" width="19" height="12" rx="2.5" />
      <circle cx="12" cy="12" r="2.4" />
      <path d="M6 9.5v5" />
      <path d="M18 9.5v5" />
    </>
  ),
  deliveries: (
    <>
      <path d="M21 3 10.5 13.5" />
      <path d="m21 3-6.8 18-3.7-7.5L3 9.8 21 3Z" />
    </>
  ),
  plans: (
    <>
      <path d="M4 6.5 12 3l8 3.5-8 3.5-8-3.5Z" />
      <path d="m4 12 8 3.5 8-3.5" />
      <path d="m4 17 8 3.5 8-3.5" />
    </>
  ),
  invoices: (
    <>
      <path d="M5 2.5h14a1 1 0 0 1 1 1v18l-3-2-3 2-3-2-3 2-3-2v-16a1 1 0 0 1 1-1Z" />
      <path d="M9 8h6" />
      <path d="M9 12h6" />
    </>
  ),
  audit: (
    <>
      <path d="M12 3 4 6.5v5c0 4.6 3.2 8.3 8 9.5 4.8-1.2 8-4.9 8-9.5v-5L12 3Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1A1.7 1.7 0 0 0 8.9 19a1.7 1.7 0 0 0-1.9.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1A1.7 1.7 0 0 0 4.7 8.9a1.7 1.7 0 0 0-.3-1.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.9.3H9a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.9V9a1.7 1.7 0 0 0 1.6 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z" />
    </>
  ),
  health: (
    <>
      <path d="M3 12h3.5l2-5 3 10 2.5-5H21" />
    </>
  ),
  support: (
    <>
      <path d="M4 5h16v10H8l-4 3.5V5Z" />
      <path d="M8 9.5h8" />
    </>
  ),
} as const;

const NAV_GROUP: { label: string; items: NavItem[] } = {
  label: "Platform",
  items: [
    { key: "overview", href: "/", label: "Overview", icon: I.overview },
    { key: "accounts", href: "/accounts", label: "Accounts", icon: I.accounts },
    { key: "payments", href: "/payments", label: "Payments", icon: I.payments },
    {
      key: "deliveries",
      href: "/deliveries",
      label: "Deliveries",
      icon: I.deliveries,
    },
    { key: "support", href: "/support", label: "Support", icon: I.support },
    { key: "plans", href: "/plans", label: "Plans", icon: I.plans },
    { key: "invoices", href: "/invoices", label: "Invoices", icon: I.invoices },
    { key: "audit", href: "/audit", label: "Audit trail", icon: I.audit },
    { key: "health", href: "/health", label: "Health", icon: I.health },
    { key: "settings", href: "/settings", label: "Settings", icon: I.settings },
  ],
};

const SECTION_LABELS: Record<string, string> = {
  overview: "Overview",
  accounts: "Accounts",
  payments: "Payments",
  deliveries: "Deliveries",
  support: "Support",
  plans: "Plans",
  invoices: "Invoices",
  audit: "Audit trail",
  health: "Health",
  settings: "Settings",
};

export function activeNavFromPath(pathname: string): string {
  const p = pathname || "/";
  if (p === "/" || p === "") return "overview";
  const seg = p.slice(1).split("/")[0];
  return seg && SECTION_LABELS[seg] ? seg : "overview";
}

function initialsOf(value: string): string {
  const parts = value
    .split(/[\s.@_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "");
  return parts.join("") || "CP";
}

type Profile = {
  email?: string;
  name?: string;
  is_platform_admin?: boolean;
  auth_method?: string;
};

/* ------------------------------------------------------------------ *
 * Shell
 * ------------------------------------------------------------------ */

function SignInGate({ message }: { message: string }) {
  return (
    <div className="cp-gate">
      <div className="cp-gate-card">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img className="cp-gate-mark" src="/logo.svg" alt="" width={40} height={40} />
        <div className="cp-gate-title">Platform admin only</div>
        <p className="cp-gate-text">{message}</p>
        <a className="dash-btn dash-btn-primary" href="/login">
          Sign in as a different account
        </a>
      </div>
    </div>
  );
}

export function AdminShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname() || "/";
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signoutLoading, setSignoutLoading] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  // The sign-in page renders bare — it must not be gated by the check it exists to
  // satisfy.
  const isLoginRoute = pathname.startsWith("/login");

  useEffect(() => {
    if (isLoginRoute) {
      setLoading(false);
      return;
    }
    let alive = true;
    (async () => {
      try {
        const res = await apiFetch("/api/v1/me", { credentials: "include" });
        if (res.status === 401) {
          const next = encodeURIComponent(
            window.location.pathname + window.location.search,
          );
          window.location.replace(`/login?next=${next}`);
          return;
        }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as Profile;
        if (alive) setProfile(data);
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [isLoginRoute]);

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

  const signOut = useCallback(async () => {
    if (signoutLoading) return;
    setSignoutLoading(true);
    try {
      await fetch("/auth/signout", { method: "POST", credentials: "include" });
    } catch {
    }
    window.location.href = "/login";
  }, [signoutLoading]);

  if (isLoginRoute) {
    return <>{children}</>;
  }

  if (loading) {
    return (
      <div className="cp-gate">
        <div className="cp-gate-card">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="cp-gate-mark" src="/logo.svg" alt="" width={40} height={40} />
          <div className="cp-gate-title">Checking your access…</div>
        </div>
      </div>
    );
  }

  if (error) {
    return <SignInGate message={error} />;
  }
  if (!profile) {
    return <SignInGate message="Sign in to open the platform console." />;
  }
  if (profile.is_platform_admin !== true) {
    return (
      <SignInGate
        message={`${profile.email ?? "This account"} is not a platform admin.`}
      />
    );
  }
  // The console is password-only: a Google session — even for an admin address — is
  // not accepted, so the SSO flow cannot be used to reach platform controls.
  if (profile.auth_method === "google") {
    return (
      <SignInGate message="Google sign-in is not allowed for the platform console. Sign in with your admin password." />
    );
  }

  const email = profile.email || "";
  const displayName = profile.name || email || "Platform admin";
  const initials = initialsOf(profile.name || email || "CP");
  const activeNav = activeNavFromPath(pathname);

  return (
    <div className="min-h-screen">
      <aside className="fixed inset-y-0 left-0 z-20 hidden w-[252px] flex-col bg-[#17181c] px-3.5 py-5 text-white lg:flex">
        <div className="px-3">
          <Link href="/" aria-label="ChmabaPay admin">
            <ChmabaLogo light suffix="Admin" />
          </Link>
        </div>

        <div className="mt-9 px-2">
          <p className="mb-2 px-2 text-[10px] font-bold uppercase tracking-[.15em] text-[#686970]">
            {NAV_GROUP.label}
          </p>
          <nav className="space-y-1" aria-label="Platform">
            {NAV_GROUP.items.map((item) => {
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
                  {isActive && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-[#c4f27c]" />}
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="mt-auto">
          <div className="mb-3 rounded-xl border border-[#33343a] bg-[#202126] p-3">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#c4f27c] text-[10px] font-extrabold text-[#1d2817]">
                {initials}
              </div>
              <div className="min-w-0">
                <p className="text-[11px] font-bold">Platform admin</p>
                <p className="mt-0.5 truncate text-[9px] text-[#85868e]" title={email}>
                  {email}
                </p>
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void signOut()}
            disabled={signoutLoading}
            className="flex w-full items-center gap-2 px-2 text-[11px] font-semibold text-[#777880] transition hover:text-white disabled:opacity-50"
          >
            <svg
              className="h-[15px] w-[15px]"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4" />
              <path d="m16 17 5-5-5-5M21 12H9" />
            </svg>
            {signoutLoading ? "Signing out…" : "Sign out"}
          </button>
        </div>
      </aside>

      <div className="lg:pl-[252px]">
        <header className="flex min-h-[74px] items-center justify-between border-b border-[#e9e9ef] bg-[#fafafd]/90 px-5 backdrop-blur dark:border-[#2a2b30] dark:bg-[#1a1b1f]/90 lg:px-8">
          <div className="flex items-center gap-3">
            <div>
              <p className="text-[10px] font-bold uppercase tracking-[.15em] text-[#6957f5]">
                Chmaba control room
              </p>
              <h1 className="mt-1 text-[18px] font-extrabold tracking-[-.04em]">
                {SECTION_LABELS[activeNav]}
              </h1>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <ThemeToggle />
            <Badge tone="violet">SUPER ADMIN</Badge>
            {/* The same component as the merchant portal's header menu — `cp-menu`
                is defined in both stylesheets. The console used to draw its own
                panel here, so the one control present on every page was the one
                that looked like it came from a different product: no avatar in the
                trigger, a different popover, a different item style. */}
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
                    href="/"
                    role="menuitem"
                    onClick={() => setMenuOpen(false)}
                  >
                    Platform overview
                  </Link>
                  <Link
                    className="cp-menu-item"
                    href="/settings"
                    role="menuitem"
                    onClick={() => setMenuOpen(false)}
                  >
                    Console settings
                  </Link>
                  <a
                    className="cp-menu-item"
                    href="https://pay.chmaba.com/dashboard"
                    role="menuitem"
                  >
                    Merchant portal
                  </a>
                  <button
                    type="button"
                    className="cp-menu-item cp-menu-item-danger"
                    role="menuitem"
                    onClick={() => void signOut()}
                    disabled={signoutLoading}
                  >
                    {signoutLoading ? "Signing out…" : "Sign out"}
                  </button>
                </div>
              )}
            </div>
          </div>
        </header>

        <main className="mx-auto max-w-[1200px] p-5 lg:p-8">
          <ToastProvider>{children}</ToastProvider>
        </main>
      </div>
    </div>
  );
}

export default AdminShell;

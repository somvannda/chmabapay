"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

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
  accounts: (
    <>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3 20c0-3.3 2.7-5.5 6-5.5s6 2.2 6 5.5" />
      <path d="M16 11.5a3 3 0 1 0 0-6" />
      <path d="M18 20c0-2.4-.8-4.3-2.2-5.4 3 .2 5.2 2.3 5.2 5.4" />
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
} as const;

const NAV_GROUP: { label: string; items: NavItem[] } = {
  label: "Platform",
  items: [
    { key: "overview", href: "/", label: "Overview", icon: I.overview },
    { key: "accounts", href: "/accounts", label: "Accounts", icon: I.accounts },
    { key: "plans", href: "/plans", label: "Plans", icon: I.plans },
    { key: "invoices", href: "/invoices", label: "Invoices", icon: I.invoices },
    { key: "audit", href: "/audit", label: "Audit trail", icon: I.audit },
  ],
};

const SECTION_LABELS: Record<string, string> = {
  overview: "Overview",
  accounts: "Accounts",
  plans: "Plans",
  invoices: "Invoices",
  audit: "Audit trail",
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
        const res = await fetch("/v1/me", { credentials: "include" });
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
    <div className="cp-app">
      <aside className="cp-side">
        <Link className="cp-brand" href="/" aria-label="ChmabaPay admin">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="cp-brand-mark" src="/logo.svg" alt="" width={30} height={30} />
          <span className="cp-brand-word">
            <span className="cp-brand-name">chmaba</span>
            <span className="cp-brand-pay">Admin</span>
          </span>
        </Link>

        <nav className="cp-nav" aria-label="Platform">
          <div className="cp-nav-group">
            <div className="cp-nav-group-label">{NAV_GROUP.label}</div>
            {NAV_GROUP.items.map((item) => {
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
        </nav>

        <div className="cp-side-foot">
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
              <Link className="cp-crumb cp-crumb-root" href="/">
                Platform
              </Link>
              <span className="cp-crumb-wrap">
                <span className="cp-crumb-sep" aria-hidden="true">
                  /
                </span>
                <span className="cp-crumb cp-crumb-current">
                  {SECTION_LABELS[activeNav]}
                </span>
              </span>
            </nav>

            <div className="cp-topbar-actions">
              <a className="cp-toplink" href="/openapi.json">
                OpenAPI
              </a>
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
                © 2026 ChmabaPay Technologies — internal console
              </span>
            </div>
          </div>
        </footer>
      </div>
    </div>
  );
}

export default AdminShell;

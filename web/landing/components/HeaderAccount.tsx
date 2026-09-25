"use client";

import { useEffect, useRef, useState } from "react";
import { MobileNav } from "@/components/MobileNav";
import type { Profile } from "@/components/portal/useSession";

/**
 * The header's account slot.
 *
 * The header used to be a static pair of links — "Sign in" and "Start free" — so a
 * merchant who was already signed in still saw both: Google login returns them to
 * `/`, which is the homepage. This renders their name and a menu instead.
 *
 * It is a client component on purpose. `app/layout.tsx` is a server component and
 * the homepage is statically rendered (`revalidate = 60`); reading the session
 * cookie there would opt every marketing route out of static rendering, so the
 * check runs in the browser against the same-origin `GET /v1/me`.
 *
 * Unlike `useSession`, a 401 here means "anonymous visitor" and must not redirect
 * to the login gateway — that hook's redirect is right for `/dashboard/*` and
 * fatal for a page whose whole job is to be readable signed out.
 */
const SIGN_IN_HREF = "/auth/google/login";

function initialsOf(value: string): string {
  const parts = value
    .split(/[\s.@_-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() || "");
  return parts.join("") || "CP";
}

export function HeaderAccount() {
  const [loading, setLoading] = useState(true);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    let alive = true;
    void (async () => {
      try {
        const res = await fetch("/v1/me", { credentials: "include" });
        // Any non-2xx — 401 included — is treated as "not signed in". There is
        // nothing useful to show for an error here, and the signed-out buttons are
        // the correct fallback for both cases.
        if (!res.ok) return;
        const data = (await res.json()) as Profile;
        if (alive) setProfile(data);
      } catch {
        // Offline, or the API is down. Same fallback as above.
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

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

  async function handleSignOut() {
    if (signingOut) return;
    setSigningOut(true);
    try {
      await fetch("/auth/signout", { method: "POST", credentials: "include" });
    } catch {
      // Leave anyway, for the same reason as `app/dashboard/layout.tsx`: the cookie
      // may already be gone, and stranding the visitor in a signed-in header is the
      // worse outcome.
    }
    // A reload, not a state change: the server-rendered page behind the header was
    // built for the signed-in account.
    window.location.href = "/";
  }

  const email = profile?.email || "";
  const displayName = profile?.name || email;

  // Placeholder, not the signed-out buttons: showing "Sign in" to someone who is
  // signed in — even for a frame — is the bug being fixed here.
  if (loading) {
    return (
      <>
        <span className="landing-account-skeleton" aria-hidden="true" />
        <MobileNav />
      </>
    );
  }

  if (!profile) {
    return (
      <>
        <a className="landing-header-signin" href={SIGN_IN_HREF}>
          Sign in
        </a>
        <a href={SIGN_IN_HREF} className="nav-cta-primary">
          Start free
        </a>
        <MobileNav />
      </>
    );
  }

  return (
    <>
      <div className="cp-menu" ref={menuRef}>
        <button
          type="button"
          className="cp-menu-btn"
          onClick={() => setMenuOpen((v) => !v)}
          aria-expanded={menuOpen}
          aria-haspopup="menu"
        >
          <span className="cp-menu-avatar" aria-hidden="true">
            {initialsOf(displayName || "CP")}
          </span>
          <span className="cp-menu-name" title={email || displayName}>
            {displayName || "Your account"}
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
                {displayName || "Your account"}
              </div>
              {email && <div className="cp-menu-head-email">{email}</div>}
            </div>
            <a className="cp-menu-item" href="/dashboard" role="menuitem">
              Open dashboard
            </a>
            <a className="cp-menu-item" href="/dashboard/settings" role="menuitem">
              Account settings
            </a>
            <a className="cp-menu-item" href="/dashboard/billing" role="menuitem">
              Billing &amp; plans
            </a>
            <button
              type="button"
              className="cp-menu-item cp-menu-item-danger"
              role="menuitem"
              onClick={handleSignOut}
              disabled={signingOut}
            >
              {signingOut ? "Signing out…" : "Sign out"}
            </button>
          </div>
        )}
      </div>
      <MobileNav profile={profile} onSignOut={handleSignOut} signingOut={signingOut} />
    </>
  );
}

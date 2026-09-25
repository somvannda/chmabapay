"use client";

import { useEffect, useRef, useState } from "react";
import { useAccount } from "@/components/accountSession";
import { MobileNav } from "@/components/MobileNav";

/**
 * The header's account slot.
 *
 * The header used to be a static pair of links — "Sign in" and "Start free" — so a
 * merchant who was already signed in still saw both: Google login returns them to
 * `/`, which is the homepage. This renders their name and a menu instead.
 *
 * It is a client component on purpose, and it shares the page's single session check
 * with every `SessionCta` — see `accountSession`. The reasoning for reading the session
 * in the browser rather than the server, and for treating a 401 as "anonymous visitor"
 * rather than a redirect, lives there.
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
  const account = useAccount();
  const [menuOpen, setMenuOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
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

  const email = account.status === "signed-in" ? account.profile.email || "" : "";
  const displayName =
    account.status === "signed-in" ? account.profile.name || email : "";

  // Placeholder, not the signed-out buttons: showing "Sign in" to someone who is
  // signed in — even for a frame — is the bug being fixed here.
  if (account.status === "loading") {
    return (
      <>
        <span className="landing-account-skeleton" aria-hidden="true" />
        <MobileNav />
      </>
    );
  }

  if (account.status === "anonymous") {
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
      <MobileNav
        profile={account.profile}
        onSignOut={handleSignOut}
        signingOut={signingOut}
      />
    </>
  );
}

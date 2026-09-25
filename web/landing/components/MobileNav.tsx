"use client";

import { useState } from "react";
import type { Profile } from "./portal/useSession";

/**
 * The phone-sized menu.
 *
 * `globals.css` hides `.landing-header-links` at 768px and below, which left no
 * way to reach Product / How it works / Plans / API / Late payments on a phone —
 * the links were simply gone. This is the disclosure that replaces them.
 *
 * Anchors are absolute (`/#plans`) rather than bare (`#plans`) because the header
 * renders on every page: a bare hash is a no-op on `/api/docs`, `/terms` and the
 * 404 page, where no such element exists.
 *
 * The auth links at the bottom flip with the session, same as the desktop slot in
 * `HeaderAccount`: "Sign in" / "Start free" when anonymous, the account's routes
 * when signed in. The panel is the only way to sign out from a phone, because the
 * desktop dropdown is not rendered at this width.
 */
const LINKS = [
  { href: "/#product", label: "Product" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#plans", label: "Plans" },
  { href: "/api/docs", label: "API" },
  { href: "/#late-payments", label: "Late payments" },
] as const;

const SIGN_IN_HREF = "/auth/google/login";

export type MobileNavProps = {
  /** Omitted while anonymous — and while the `GET /v1/me` check is still in flight. */
  profile?: Profile | null;
  onSignOut?: () => void;
  signingOut?: boolean;
};

export function MobileNav({ profile, onSignOut, signingOut = false }: MobileNavProps) {
  const [open, setOpen] = useState(false);
  const close = () => setOpen(false);

  return (
    <div className="landing-mobile-nav">
      <button
        type="button"
        className="landing-mobile-toggle"
        aria-label={open ? "Close menu" : "Open menu"}
        aria-expanded={open}
        aria-controls="landing-mobile-panel"
        onClick={() => setOpen((value) => !value)}
      >
        <span className="landing-mobile-toggle-bar" />
        <span className="landing-mobile-toggle-bar" />
        <span className="landing-mobile-toggle-bar" />
      </button>

      {open && (
        <div id="landing-mobile-panel" className="landing-mobile-panel">
          {LINKS.map((link) => (
            <a
              key={link.href}
              className="landing-mobile-link"
              href={link.href}
              onClick={close}
            >
              {link.label}
            </a>
          ))}
          {profile ? (
            <>
              <a
                className="landing-mobile-link landing-mobile-signin"
                href="/dashboard"
                onClick={close}
              >
                Open dashboard
              </a>
              <a
                className="landing-mobile-link"
                href="/dashboard/settings"
                onClick={close}
              >
                Account settings
              </a>
              <a
                className="landing-mobile-link"
                href="/dashboard/billing"
                onClick={close}
              >
                Billing &amp; plans
              </a>
              <button
                type="button"
                className="landing-mobile-link landing-mobile-signout"
                onClick={() => {
                  close();
                  onSignOut?.();
                }}
                disabled={signingOut}
              >
                {signingOut ? "Signing out…" : "Sign out"}
              </button>
            </>
          ) : (
            <>
              <a
                className="landing-mobile-link landing-mobile-signin"
                href={SIGN_IN_HREF}
                onClick={close}
              >
                Sign in
              </a>
              <a className="landing-mobile-cta" href={SIGN_IN_HREF} onClick={close}>
                Start free
              </a>
            </>
          )}
        </div>
      )}
    </div>
  );
}

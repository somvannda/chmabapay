"use client";

import { useState } from "react";

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
 */
const LINKS = [
  { href: "/#product", label: "Product" },
  { href: "/#how-it-works", label: "How it works" },
  { href: "/#plans", label: "Plans" },
  { href: "/api/docs", label: "API" },
  { href: "/#late-payments", label: "Late payments" },
] as const;

const SIGN_IN_HREF = "/auth/google/login";

export function MobileNav() {
  const [open, setOpen] = useState(false);

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
              onClick={() => setOpen(false)}
            >
              {link.label}
            </a>
          ))}
          <a
            className="landing-mobile-link landing-mobile-signin"
            href={SIGN_IN_HREF}
            onClick={() => setOpen(false)}
          >
            Sign in
          </a>
          <a
            className="landing-mobile-cta"
            href={SIGN_IN_HREF}
            onClick={() => setOpen(false)}
          >
            Start free
          </a>
        </div>
      )}
    </div>
  );
}

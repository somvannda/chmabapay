"use client";

import type { ReactNode } from "react";
import { useAccount } from "@/components/accountSession";

/**
 * A call to action whose destination follows the session.
 *
 * "Start free" and "Choose <plan>" used to hand a merchant who already had an account back
 * to Google's consent screen — the same bug the header had, on the most-clicked buttons of
 * the page. Signed in, they now open the workspace (or billing, for a plan) instead.
 *
 * Only the `href` follows the check, never the label or the box. A placeholder would shift
 * the hero's button row when it resolved, and rendering the signed-out label first would
 * show "Start free" to a merchant who already pays — the thing this removes. Until the
 * answer arrives the link points at the sign-in gateway, which is where a click a few
 * milliseconds into the page load would have gone before this existed.
 */
export type SessionCtaProps = {
  /** Where an anonymous visitor goes: the sign-in gateway. */
  href: string;
  /** Where a signed-in visitor goes instead. */
  hrefWhenSignedIn: string;
  className?: string;
  children: ReactNode;
};

export function SessionCta({
  href,
  hrefWhenSignedIn,
  className,
  children,
}: SessionCtaProps) {
  const account = useAccount();
  return (
    <a
      className={className}
      href={account.status === "signed-in" ? hrefWhenSignedIn : href}
    >
      {children}
    </a>
  );
}

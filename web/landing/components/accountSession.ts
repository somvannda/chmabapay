"use client";

import { useEffect, useState } from "react";
import type { Profile } from "@/components/portal/useSession";

/**
 * The session, as the marketing site can see it.
 *
 * The landing pages are statically rendered — `revalidate = 60` on the homepage — and the
 * session lives in an httpOnly cookie that a build machine cannot read. Consulting it on
 * the server would opt every marketing route out of static rendering, so it is read here,
 * in the browser, from the same-origin `GET /api/v1/me`.
 *
 * One request serves the whole page. The header's account chip and each session-aware call
 * to action subscribe to this module instead of fetching for themselves, and the answer is
 * cached for the life of the document, so adding another CTA costs no traffic.
 *
 * Deliberately not `useSession` from the portal: that hook redirects a 401 to the login
 * gateway, which is right for `/dashboard/*` and wrong for a page whose job is to be
 * readable signed out. Here a 401 means "anonymous visitor".
 */
export type AccountState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "signed-in"; profile: Profile };

let state: AccountState = { status: "loading" };
let pending: Promise<void> | null = null;

const listeners = new Set<(next: AccountState) => void>();

function announce(next: AccountState): void {
  state = next;
  for (const listener of listeners) listener(next);
}

/** Resolve the session once per document; every later caller awaits the same promise. */
export function checkAccount(): Promise<void> {
  pending ??= fetch("/api/v1/me", { credentials: "include" })
    .then(async (res) => {
      // Any non-2xx is "not signed in". There is nothing useful to render for an error
      // here, and the signed-out treatment is the right fallback for all of them.
      announce(
        res.ok
          ? { status: "signed-in", profile: (await res.json()) as Profile }
          : { status: "anonymous" },
      );
    })
    // Offline, or the API is down: same fallback as a 401.
    .catch(() => announce({ status: "anonymous" }));
  return pending;
}

/** Subscribe to that one answer. */
export function useAccount(): AccountState {
  // The initializer reads whatever is already known, so a component mounting after the
  // answer arrived renders it on the first pass rather than flashing "loading".
  const [current, setCurrent] = useState<AccountState>(state);

  useEffect(() => {
    listeners.add(setCurrent);
    if (state.status === "loading") void checkAccount();
    return () => {
      listeners.delete(setCurrent);
    };
  }, []);

  return current;
}

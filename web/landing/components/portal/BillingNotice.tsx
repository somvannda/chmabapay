"use client";

import { useCallback, useEffect, useState } from "react";

/**
 * One billing notice, as the dashboard shell renders it.
 *
 * The copy is the server's. `title`, `body` and the action label all arrive in the payload, so the
 * sentence a merchant reads here is the same one the reminder worker recorded having delivered —
 * and nothing in this file rewrites it or re-derives the state. The API decides what is owed and
 * how urgently; this decides where it sits on the page.
 *
 * Dismissal is `info` only, and the *server* says so via `dismissible`. Re-deriving that rule here
 * would be a second copy of a policy, and the one that matters is the one the API enforces.
 */

/** The payload from `GET /v1/billing/notices` — see `services/billing.py::_render_notice`. */
export type BillingNoticePayload = {
  state: string;
  level: string;
  title: string;
  body: string;
  invoice_id: number | null;
  period_label: string | null;
  amount_cents: number | null;
  amount_formatted: string | null;
  due_at: string | null;
  days_until_due: number | null;
  action_label: string;
  action_url: string;
  dismissible: boolean;
};

const DISMISS_PREFIX = "chmabapay:notice-dismissed";

function dismissKey(notice: BillingNoticePayload): string {
  // Keyed per (invoice, state) so a *new* state on the same invoice is visible again after the
  // merchant dismissed the previous one: a debt waved away at `due_3` has to be able to come back
  // at `overdue_1`. Keying on the invoice alone would make dismissing a way to silence dunning,
  // and keying on nothing would let one dismissal hide every future notice.
  return `${DISMISS_PREFIX}:${notice.invoice_id ?? "account"}:${notice.state}`;
}

export function BillingNotice({ notice }: { notice: BillingNoticePayload | null }) {
  const [dismissed, setDismissed] = useState(false);

  useEffect(() => {
    if (!notice) {
      setDismissed(false);
      return;
    }
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(dismissKey(notice));
    } catch {
      // Storage can be unavailable (private mode, or a blocked origin). A notice that cannot be
      // dismissed is a far smaller problem than a shell that will not render.
    }
    setDismissed(stored === "1");
  }, [notice]);

  const dismiss = useCallback(() => {
    if (!notice) return;
    try {
      window.localStorage.setItem(dismissKey(notice), "1");
    } catch {
      // Same as above: dismissing is a convenience, and losing it costs one click.
    }
    setDismissed(true);
  }, [notice]);

  if (!notice) return null;
  // A non-dismissible notice ignores any stored key, which is what makes the server's flag the
  // only authority on whether it can be hidden.
  if (dismissed && notice.dismissible) return null;

  return (
    <div
      className={`cp-notice cp-notice-${notice.level}`}
      role={notice.level === "critical" ? "alert" : "status"}
    >
      <div className="cp-notice-inner">
        <div className="cp-notice-body">
          <div className="cp-notice-title">{notice.title}</div>
          <p className="cp-notice-text">{notice.body}</p>
        </div>
        <div className="cp-notice-actions">
          {/* A plain anchor, not a `Link`: the URL carries `?pay={id}`, and the destination has to
              mint a fresh KHQR on load (ABA's session is 180s, so a code from a previous render is
              dead on arrival). A full navigation is what this link means. */}
          <a className="dash-btn dash-btn-sm dash-btn-primary" href={notice.action_url}>
            {notice.action_label}
          </a>
          {notice.dismissible ? (
            <button
              type="button"
              className="cp-notice-dismiss"
              onClick={dismiss}
              aria-label="Dismiss this notice"
            >
              Dismiss
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
}

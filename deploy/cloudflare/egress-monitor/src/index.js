/**
 * ChmabaPay egress monitor.
 *
 * Why this exists: on 2026-11-04 the VPS's outbound DNS degraded and every
 * lookup cost ~4s. `/health` answered 200 the whole time and the internal
 * payment probe never leaves the box, so nothing inside the failure domain -
 * and no plain uptime check - could see it. This Worker runs outside the box
 * and asks the application to prove, on demand, that it can still resolve a
 * name and open an outbound connection (`GET /api/v1/internal/egress`).
 *
 * It pages over Telegram from Cloudflare's network, which is healthy even when
 * the host's resolver is not.
 *
 * Two rules keep it from crying wolf, and both were learned from real pages:
 *
 *   * **A failed minute is not a failed host.** On 2026-11-08 and 2026-11-09 a
 *     single bad minute paged, then recovered on the next run. The check must
 *     fail for three consecutive minutes before it wakes anyone, and pass for
 *     two before it announces recovery.
 *   * **The message must name the phase.** One of those pages was a Telegram
 *     TCP connect timeout with DNS at 5ms, but the text told the reader to check
 *     /etc/resolv.conf. A page that points at the wrong subsystem costs more
 *     than it saves.
 *
 * State lives in KV only across edges: the start of a failure, the alert, the
 * first success, the recovery. Steady-state runs write nothing, which is what
 * keeps it inside the KV free tier (1,000 writes/day) at a one-minute cadence.
 */

const ALERT_REPEAT_MS = 60 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 15_000;

// Three consecutive failing minutes. At a one-minute cron this is the smallest
// window that survives ordinary packet loss without hiding a real outage for
// long: a resolver that is actually broken stays broken for far longer.
const FAILURE_GRACE_MS = 2 * 60 * 1000;

// Two consecutive passing minutes before "recovered", so a flapping link does
// not produce an alert/recovery pair every other minute.
const RECOVERY_GRACE_MS = 60 * 1000;

const STATE_TTL_SECONDS = 7 * 24 * 60 * 60;

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(run(env));
  },

  // `/run` is a manual smoke test only. It never sends unless the check fails, but
  // it does make the Worker open outbound connections, so it is not anonymous.
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/run") {
      if (request.headers.get("x-monitor-key") !== env.EGRESS_TOKEN) {
        return new Response("forbidden\n", { status: 403 });
      }
      return Response.json(await run(env));
    }
    return new Response("chmabapay egress monitor\n");
  },
};

async function run(env) {
  const result = await probe(env);
  const now = Date.now();
  const previous = normalize(await env.STATE.get("state", { type: "json" }));
  const next = { ...previous };
  let alerted = false;

  if (!result.ok) {
    next.okSince = null;
    if (!next.failingSince) {
      next.failingSince = now;
    }
    const failingFor = now - next.failingSince;
    const sustained = failingFor >= FAILURE_GRACE_MS;
    const reminderDue = now - next.lastAlertAt >= ALERT_REPEAT_MS;
    if (sustained && (!next.down || reminderDue)) {
      await page(env, composeFailure(result, failingFor));
      next.down = true;
      next.lastAlertAt = now;
      alerted = true;
    }
  } else {
    if (next.down) {
      // Keep the failure start until recovery so the recovery message can say
      // how long it actually lasted; a single passing minute is not yet a
      // recovery, and the clock must not restart if it fails again.
      if (!next.okSince) {
        next.okSince = now;
      }
      if (now - next.okSince >= RECOVERY_GRACE_MS) {
        await page(env, composeRecovery(now - (previous.failingSince ?? now)));
        next.down = false;
        next.failingSince = null;
        next.okSince = null;
        next.lastAlertAt = now;
        alerted = true;
      }
    } else {
      // A blip that never reached the alert threshold ends here, so the next
      // failure starts a fresh window instead of inheriting this one.
      next.failingSince = null;
      next.okSince = null;
    }
  }

  // Write only when something actually changed. During a steady outage below
  // the alert threshold, or a steady healthy week, this is a no-op.
  if (JSON.stringify(next) !== JSON.stringify(previous)) {
    await env.STATE.put("state", JSON.stringify(next), {
      expirationTtl: STATE_TTL_SECONDS,
    });
  }

  return { ...result, alerted };
}

/**
 * Give the state object a stable shape so two runs that mean the same thing
 * serialise identically. Without this every run would rewrite KV with a
 * different key order or an explicit `null`.
 */
function normalize(state) {
  return {
    down: state?.down === true,
    failingSince: typeof state?.failingSince === "number" ? state.failingSince : null,
    okSince: typeof state?.okSince === "number" ? state.okSince : null,
    lastAlertAt: typeof state?.lastAlertAt === "number" ? state.lastAlertAt : 0,
  };
}

async function probe(env) {
  try {
    const response = await fetch(env.EGRESS_URL, {
      headers: { Authorization: `Bearer ${env.EGRESS_TOKEN}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const body = await response.text();
    let parsed = null;
    try {
      parsed = JSON.parse(body);
    } catch {
      // A non-JSON body (an nginx error page, say) is still a failing check;
      // the raw text is kept for the message.
    }
    return {
      ok: response.ok,
      reachable: true,
      status: response.status,
      checks: Array.isArray(parsed?.checks) ? parsed.checks : null,
      detail: `HTTP ${response.status} ${body}`.slice(0, 600),
    };
  } catch (error) {
    // A timeout from Cloudflare's side also counts: if the edge cannot reach the
    // app, the app is not serving.
    return {
      ok: false,
      reachable: false,
      status: 0,
      checks: null,
      detail: `${error.name}: ${error.message}`,
    };
  }
}

/**
 * Which phase a failed check failed in.
 *
 * A successful resolve still writes `DNS 3ms, connect ...`, so "the detail
 * starts with DNS" is always true and says nothing. The phase is the prefix
 * that describes the *failure*: `DNS timed out` / `DNS failed` is the resolver,
 * `connect` after a timing is TCP egress.
 */
function failedPhase(check) {
  if (check.ok) return null;
  if (/^DNS (timed out|failed)/.test(check.detail) || check.detail === "DNS returned no address") {
    return "dns";
  }
  if (check.detail.includes("connect")) {
    return "connect";
  }
  return "other";
}

function composeFailure(result, failingForMs) {
  if (!result.reachable) {
    return [
      "ChmabaPay ALERT: the host or the app is unreachable",
      "",
      `Cloudflare could not complete the egress check: ${result.detail}`,
      "",
      "This is the API being down or the box being unreachable, not a slow resolver. Webhook deliveries and Telegram alerts from the VPS will fail while this lasts.",
      "",
      `Failing for ${formatDuration(failingForMs)}.`,
    ].join("\n");
  }

  const checks = result.checks ?? [];
  const failed = checks.filter((check) => !check.ok);
  const dnsFailed = failed.some((check) => failedPhase(check) === "dns");
  const connectFailed = failed.some((check) => failedPhase(check) === "connect");

  let headline;
  let advice;
  if (dnsFailed && !connectFailed) {
    headline = "DNS is failing on the host";
    // Only mention the resolver when the resolver is what failed. On 2026-11-08
    // the alert said "check /etc/resolv.conf" while DNS was answering in 5ms.
    advice =
      "The resolver is not answering, so /etc/resolv.conf is the place to look - a dead nameserver is tried serially and stalls every lookup. Webhook deliveries and Telegram alerts from the VPS will fail until this clears.";
  } else if (connectFailed && !dnsFailed) {
    headline = "outbound connections are failing on the host";
    advice =
      "DNS answers but the TCP connect does not, so this is egress, a firewall, or a dead IPv6 route - not the resolver. The probe tries IPv4 before IPv6 now; if it persists, check the host's IPv6 route and whether the network blocks the target.";
  } else if (dnsFailed && connectFailed) {
    headline = "DNS and outbound connections are failing on the host";
    advice =
      "Both phases are failing, which is what a dead resolver plus a blocked path looks like. Start with /etc/resolv.conf; if DNS recovers and connects still fail, it is egress or IPv6.";
  } else {
    headline = "the host cannot reach the internet";
    advice =
      "Outbound traffic from the host is failing. Webhook deliveries and Telegram alerts from the VPS will fail until this clears.";
  }

  const lines = checks.map(
    (check) => `${check.name} (${check.host}): ${check.detail}`,
  );
  return [
    `ChmabaPay ALERT: ${headline}`,
    "",
    ...lines,
    "",
    advice,
    "",
    `Failing for ${formatDuration(failingForMs)}.`,
  ].join("\n");
}

function composeRecovery(downForMs) {
  const lasted = formatDuration(downForMs);
  return [
    "ChmabaPay: outbound network recovered.",
    "",
    `The egress check passed twice in a row after failing for ${lasted}. Webhook deliveries and alert messages can leave the host again.`,
  ].join("\n");
}

function formatDuration(ms) {
  const seconds = Math.max(0, Math.round(ms / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) {
    return `${minutes}m`;
  }
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, "0")}m`;
}

async function page(env, text) {
  const response = await fetch(
    `https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/sendMessage`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        chat_id: env.OPS_CHAT_ID,
        text,
        disable_web_page_preview: true,
      }),
    },
  );

  if (!response.ok) {
    // Nowhere left to escalate to; the Worker's own logs are the record.
    console.error(`telegram send failed: ${response.status} ${await response.text()}`);
  }
}

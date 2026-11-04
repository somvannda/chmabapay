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
 * State lives in KV only to throttle: one alert when the check starts failing,
 * a reminder every hour while it stays broken, and one message when it
 * recovers. Writes happen only on those edges, which keeps it inside the KV
 * free tier (1,000 writes/day) even at a one-minute cadence.
 */

const ALERT_REPEAT_MS = 60 * 60 * 1000;
const REQUEST_TIMEOUT_MS = 15_000;

export default {
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(run(env));
  },

  // `/run` is a manual smoke test only. It never sends unless the check fails.
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/run") {
      return Response.json(await run(env));
    }
    return new Response("chmabapay egress monitor\n");
  },
};

async function run(env) {
  const result = await probe(env);
  const previous = await env.STATE.get("state", { type: "json" });
  const now = Date.now();
  const wasOk = previous?.ok;
  const lastAlertAt = previous?.lastAlertAt ?? 0;

  let alert = false;
  if (!result.ok) {
    // Edge-triggered, then a reminder while it stays down.
    alert = wasOk !== false || now - lastAlertAt >= ALERT_REPEAT_MS;
  } else if (wasOk === false) {
    // Back to healthy: say so once, so nobody wonders whether it is still down.
    alert = true;
  }

  if (alert) {
    await page(env, result);
  }

  if (!previous || previous.ok !== result.ok || alert) {
    await env.STATE.put(
      "state",
      JSON.stringify({ ok: result.ok, lastAlertAt: alert ? now : lastAlertAt }),
      { expirationTtl: 7 * 24 * 60 * 60 },
    );
  }

  return { ...result, alerted: alert };
}

async function probe(env) {
  try {
    const response = await fetch(env.EGRESS_URL, {
      headers: { Authorization: `Bearer ${env.EGRESS_TOKEN}` },
      signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
    });
    const body = await response.text();
    return { ok: response.ok, detail: `HTTP ${response.status} ${body}`.slice(0, 600) };
  } catch (error) {
    // A timeout from Cloudflare's side also counts: if the edge cannot reach the
    // app, the app is not serving.
    return { ok: false, detail: `${error.name}: ${error.message}` };
  }
}

async function page(env, result) {
  const text = result.ok
    ? "ChmabaPay: outbound network recovered.\n\nThe egress check passes again - webhook deliveries and alert messages can leave the host."
    : `ChmabaPay ALERT: the host cannot reach the internet.\n\n${result.detail}\n\nWebhook deliveries and Telegram alerts from the VPS will fail until this clears. Check DNS (/etc/resolv.conf) on the host.`;

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

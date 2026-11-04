# Egress monitor (Cloudflare Worker)

An off-box check that pages when the ChmabaPay host can no longer reach the
internet. It exists because of the 2026-11-04 incident: the host's outbound DNS
degraded, webhook deliveries timed out, and every Telegram alert failed over the
same broken resolver — while `/health` returned 200 the entire time.

Nothing running *on* the VPS can report that. This Worker can: every minute it
calls `GET /api/v1/internal/egress`, which performs a real DNS resolve and TCP
connect from inside the application container, and answers **503** when either
fails. On failure the Worker sends a Telegram message from Cloudflare's network.

It also covers the simpler case: if the box is down or Cloudflare cannot reach
it, the fetch fails and pages.

## Deploy

Requires a Cloudflare account with access to the `chmaba.com` zone (a Worker on
`*.workers.dev` needs no DNS change, so the zone itself is not required).

```bash
cd deploy/cloudflare/egress-monitor
npx wrangler login
npx wrangler kv namespace create STATE      # paste the id into wrangler.toml
npx wrangler secret put EGRESS_TOKEN        # = INTERNAL_PROBE_TOKEN from deploy/.env
npx wrangler secret put TELEGRAM_BOT_TOKEN  # the bot already used for alerts
# set OPS_CHAT_ID in wrangler.toml (OPS_TELEGRAM_CHAT_ID from deploy/.env)
npx wrangler deploy
```

The cron starts immediately after deploy. Watch it with:

```bash
npx wrangler tail
```

A manual run, without waiting for the schedule:

```bash
curl https://chmabapay-egress-monitor.<your-subdomain>.workers.dev/run
```

Expected healthy output:

```json
{ "ok": true, "detail": "HTTP 200 {...\"status\":\"ok\"...}", "alerted": false }
```

## Alert behaviour

| Situation | What happens |
|---|---|
| First failing run | One alert with the HTTP status and body |
| Still failing | A reminder at most once an hour |
| Recovers | One "recovered" message |
| Healthy, steady state | No KV writes (only a read per minute) |

The KV namespace is used only for that throttle. Writes happen on edges, so a
one-minute cron stays inside the free tier's 1,000 writes/day.

## What it does not do

It does not verify the payment rail: a passing check means DNS and egress work,
not that ABA is up or that a QR can be minted. Pair it with the hourly
`POST /api/v1/internal/payment-probe` if you want that covered too — see
`docs/internal-payment-probe.md`.

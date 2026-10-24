# ABA hosted-checkout endpoint health

The hosted-checkout API is the single most load-bearing external address in the
product. Both calls behind a PayWay payment go to it:

- `POST {base}/pw-app/v1/payment/gateway/list-payment-options` — mints the QR.
- `POST {base}/pw-app/v1/payment-link/check-payment-status` — says whether it was paid.

They share one base, so when ABA moves it, QR generation and detection break
together.

## What happened on 2026-10-24

`pwapp.ababank.com/api` started answering every request with nginx's plain-text
403. The PayWay page's own build had moved to `link.payway.com.kh/api`, which
accepted the identical body and hash. The fix was a one-line constant change, but
it was found by a merchant reporting a broken QR, not by the platform. Anything
already in flight could not be polled, so the failure looked like payments that
never settled.

The value was re-derived from the page bundle: the SPA ships
`API_BASE_PUBLIC_URL` in `_nuxt/*.js`, and that is the address it posts to.

## What runs now

`PaywayEndpointProbe` (`src/chmabapay/services/payway_endpoint_probe.py`) is
evaluated by the existing alert watcher (`AlertWatcher.from_settings`) and is
clamped to its own interval, `ALERT_PAYWAY_ENDPOINT_INTERVAL_SECONDS` (default 30
minutes). The watcher ticks every 30 seconds; between probe runs it reuses the
last verdict, so a move produces one alert edge, not one per tick.

The probe posts an **empty** body to the status endpoint. No payment is minted, no
token is used, and no session is touched. A validation refusal is the proof it
needs:

| Response | Meaning |
| --- | --- |
| 2xx, 400, 405, 409, 415, 422 | The route exists and executed. Healthy. |
| 401, 403, 406, 429 | ABA is refusing this network, not missing the route. Names `PAYWAY_PROXY_URL`. |
| 404 | The route is gone. The base almost certainly moved. |
| 5xx, timeout, DNS/TLS failure | Unreachable. Reported as down. |
| 3xx | Followed immediately: the `Location` names the new base. |

## Finding the new base

When the configured base is missing or unreachable, and no redirect pointed at a
candidate, the probe re-derives the current base the same way the 2026-10-24 fix
was found:

1. Fetch `https://link.payway.com.kh`.
2. Follow up to eight of its `_nuxt/*.js` bundles.
3. Extract every `API_BASE_PUBLIC_URL` value.
4. Probe each candidate with the same empty-body request.

The first candidate that answers becomes the alert's recommendation. Nothing is
switched automatically: rewriting the live payment path from a discovery heuristic
is a worse failure than a page.

## The alert

Raised and resolved like every other condition, over the same Telegram notifier to
`OPS_TELEGRAM_CHAT_ID`:

```
ChmabaPay · ALERT · ABA moved the hosted checkout API

https://link.payway.com.kh/api no longer answers (answered 404). The PayWay
page's own bundle points at https://…/api, which accepted the same request.

Why it matters: no QR can be minted and payments already in flight cannot be
confirmed
Time: 24 Oct 2026, 15:00 +07
```

A refusal reads differently and names the proxy:

```
ChmabaPay · ALERT · ABA refuses the hosted checkout from this network
… answered 403 … Set PAYWAY_PROXY_URL to route the ABA hop through an allowed
address, or ask ABA to allowlist this host.
```

## Operating it

Run the check by hand (it prints the verdict and every candidate found):

```bash
uv run python -m chmabapay.services.payway_endpoint_probe
```

- **Moved.** Set `PAYWAY_HOSTED_API_BASE` to the recommended value and restart the
  process that runs the workers. The mint and the status call both pick it up.
- **Refused.** Set `PAYWAY_PROXY_URL` (the probe and the QR fetches share it), or
  get the host allowlisted by ABA.
- **Down, no candidate.** Open the PayWay link page in a browser, read
  `API_BASE_PUBLIC_URL` from its bundle, set `PAYWAY_HOSTED_API_BASE`, and restart.
- **Disable.** Set `ALERT_PAYWAY_ENDPOINT_INTERVAL_SECONDS=0`.

## Limits

- It proves the endpoint exists and executes; it does not prove a particular
  session can be read. A successful mint against a real payment is still the only
  end-to-end proof.
- It runs in whichever process runs the alert watcher (`WORKERS_ENABLED`, normally
  the worker container). If that process is down, nothing is probed — the queue
  alerts are the signal there.
- Discovery depends on the page still shipping `API_BASE_PUBLIC_URL`. If a future
  build hides it, the check still reports the outage; it just cannot name the
  replacement.

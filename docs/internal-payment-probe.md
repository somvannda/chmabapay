# Internal payment probe

The uptime monitor used to create a real `$0.10` payment in a merchant's store every
hour. Those rows showed up in that merchant's history, and because the probe never
pays them, the expiry sweeper fired a `payment.expired` webhook every hour as though
a customer had abandoned a sale.

The hourly check now has an internal landing point:

```
POST /api/v1/internal/payment-probe
```

It mints a QR against the platform's own internal store and **stops there**. There is
no `Payment` row, no expiry sweep, no webhook, and nothing a merchant can see.

## What it needs

- A store with `is_internal=true`, owned by a platform admin account. The resolver is
  `services.billing.resolve_hq_store`: `CHMABAPAY_HQ_STORE_ID` wins, then a store named
  `ChmabaPay HQ` under a platform admin, then that admin's first active store.
- That store must have an active payment link.
- Either the scoped `INTERNAL_PROBE_TOKEN` (recommended) or an API key belonging to a
  **platform admin** account. The token is limited to this endpoint; a platform-admin
  key can do everything, so do not hand that to an external monitor. A merchant key
  gets `403 platform_admin_required`.

Generate the token once and put it in `deploy/.env`, then restart the API:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

```dotenv
INTERNAL_PROBE_TOKEN=<the generated value>
```

## Call it

```bash
curl -sS -X POST https://pay.chmaba.com/api/v1/internal/payment-probe \
  -H "Authorization: Bearer $INTERNAL_PROBE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount": "0.10", "hosted_qr": false}'
```

If `INTERNAL_PROBE_TOKEN` is unset, the endpoint still accepts a platform-admin API
key. It never accepts a merchant key.

`hosted_qr` defaults to `false`, which builds the QR with our own encoder. That
exercises auth, store resolution, the payment link and the KHQR encoder without
spending an ABA checkout every hour. Set `"hosted_qr": true` only when you want the
full ABA mint path; that spends one live hosted-checkout session per call. ABA's own
endpoint health is already covered every 30 minutes by `PaywayEndpointProbe` (see
`docs/payway-endpoint-health.md`).

The optional `store` and `merchant` fields are accepted and **ignored**, so an old
monitor body can be repointed by changing only its URL and key.

### Response

```json
{
  "status": "ok",
  "probe": "internal",
  "store": "hq_main_store",
  "currency": "USD",
  "amount": "0.10",
  "amount_cents": 10,
  "hosted": false,
  "qr_md5": "…",
  "expires_at": "2026-10-29T20:00:00+00:00"
}
```

The QR string is deliberately **not** returned: this is a health check, not a payable
link.

## Cutover

1. Set `INTERNAL_PROBE_TOKEN` in `deploy/.env` and restart the API/worker.
2. Deploy this code.
3. Change the monitor in the same window: URL to
   `/api/v1/internal/payment-probe`, `Authorization: Bearer <token>`, body
   `{"amount":"0.10","hosted_qr":false}`.
4. Confirm a `200` with `"status":"ok"`.

Until step 3 is done, the old job will get `400
synthetic_payment_requires_internal_store` and its check will read as failing. That
is deliberate: a probe that cannot use the internal store must not fall back to a
customer store.

## What happens to the old job

Two safeguards keep the old `health-smoke:` probe out of merchant stores even before
the monitor is repointed:

1. `POST /api/v1/payments` refuses a `health-smoke:` reference — or
   `metadata.synthetic=true` — on any store that is not internal. It answers
   `400 synthetic_payment_requires_internal_store` and logs the store and reference.
2. Merchant payment lists and reports exclude `health-smoke:` rows, so historical
   probe rows already in a store stop appearing in the portal and CSV/JSON exports.
   The admin payment feed still shows them for audit.

Once the monitor points at the new endpoint, there is nothing left to hide.

## Why this is hidden from the public API

`routers/probe.py` is mounted with `include_in_schema=False`. It is an operational
endpoint for the platform admin, not part of the merchant integration surface. The
public payments API and its error reference do not need to describe it.

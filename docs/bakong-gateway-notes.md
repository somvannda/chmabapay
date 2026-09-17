# Bakong gateway findings (Phase 0 spike)

Status: **done** (2026-09-17) — for the ABA PayWay link model. See §7 for what
this did *not* answer.

Owner: platform

> The original instruction was to run
> `uv run python -m chmabapay.tools.probe --config probe-config.json --report findings.json`.
> That spike was never run and is no longer the path that matters: the product
> settled on ABA PayWay share links, where ABA issues the QR and answers for it,
> so no Bakong Open API credential is involved at all. Everything below comes
> from a **real payment on the live rail** on 2026-09-17 — a genuine 0.10 USD
> payment to merchant `SOMVANNDA KONG` via
> `https://link.payway.com.kh/ABAPAYpe518710Y`. Where a question could not be
> answered without Bakong credentials, it is marked **open** rather than guessed.

## 1. Credential scope — can one credential set act for many merchant links?

**Not needed for this design.** No Bakong credential was used, and none is
configured (`BAKONG_API_TOKEN` and `BAKONG_DEVELOPER_EMAIL` are both empty).

The tenant model does not depend on one credential acting for many merchants.
Each store stores its own PayWay link, and ABA's own page supplies the state that
authorises the mint — the link *is* the authorisation. That is what makes the
multi-tenant story work without a Bakong account at all.

**Open:** whether one Bakong Open API credential could act on behalf of
sub-merchants. This matters only if we ever want to confirm a QR we build
ourselves (see §2).

## 2. KHQR generation

Two paths exist, and only one of them is payable.

- **Offline (`build_khqr_payload`)** — we construct the EMVCo TLV ourselves. The
  encoder is correct: `tests/test_khqr.py` pins our CRC-16 against a real captured
  ABA payload. But nothing on ABA's side has a record of the code, so a wallet
  answers **"QR not found"**. Not payable, and there is nothing to poll.
- **Hosted (`POST /v1/khqr/payway/checkout`, and `POST /v1/payments` with the
  default `hosted_qr`)** — ABA issues the code and the session. **This is the
  payable path.** It needs no PayWay API key, no signed request and no browser:
  the link page is fetched, its `aba_data`/`request_time` are extracted, and a
  SHA-512 hash over those fields is posted to ABA's hosted endpoint.

Observed payload (real, amount 0.10 USD):

```
00020101021230510016abaakhppxxx@abaa01151260716102430810208ABA Bank52048999530384
054030.15802KH5914SOMVANNDA KONG6003N/A625568510010PAYWAY@ABA0115518710-27272373
02090423646340301199670013178962870944001131792220709440672100170013F1BF016411FDA
6804PLIK630413BB
```

- tag `30` = `abaakhppxxx@abaa` (ABA's own Bakong account — ABA's, not the
  merchant's)
- tag `62.50` = `PAYWAY@ABA` private template, carrying the ABA tran reference
- tag `53` = `840` → **USD confirmed**
- tag `58` = `KH`; tag `59` = `SOMVANNDA KONG`

## 3. Confirmation endpoint(s)

Working call (the only one that has ever confirmed a payment):

```
POST /v1/khqr/payway/status
{"client_id": "2364634-518710-27272373",
 "request_time": "20260917070508",
 "token": "<the opaque session token ABA returns at mint — ~1.5 KB, redacted>"}
```

The token is a live session credential for that one checkout, so it is described
rather than copied. It lives on the payment row under
`gateway_status_raw.payway_hosted` for exactly as long as the retention window
allows, and nowhere else.

Response once the customer has paid:

```
{"action": "approved", "paid": true, "terminal": true,
 "tran_id": "1789628839192554",
 "receipt_url": "https://pwapp.ababank.com/api/payment-gateway/v1/payments/download-receipt?file=..."}
```

**Does it distinguish scanned from paid? Yes.** The same call on a code nobody
has scanned returns `action: "request_qr"` with `paid: false` — which is exactly
what `tests/test_live_aba.py` asserts. So "initiated" and "settled" are separable,
which is what makes a poll-only strategy safe.

**Push/webhook/callback: open.** Not offered by the hosted endpoint as used here,
and not searched for. We poll.

**Listing a merchant's recent transactions: open.** Not needed for the hosted
path — the session ABA hands back at mint time is a better key than a ledger
search, because it identifies the exact transaction rather than a candidate.

## 4. Detection latency

Tiny real payment test — QR paid, then:

- method used: **poll** (W1 `payments.detection`, plus the 30s orphan sweep)
- latency observed: **31.2 s** from mint to our row reading `paid`

Measured, from the live run:

| | |
|---|---|
| payment created | `2026-09-17T07:05:10.191951Z` |
| marked `paid` | `2026-09-17T07:05:41.365645Z` |
| difference | `00:00:31.173694` |
| ABA `tran_id` | `1789628839192554` |
| stored `bakong_ref` | `1789628740612824` |

**Read that number carefully.** It includes the customer's own time — the
platform cannot see the moment money moved, only the moment it looked. So 31.2s
is an upper bound on detection, not detection latency itself. With the orphan
sweep at 30s, a payment made immediately after a sweep is found up to ~30s later;
the floor is one sweep interval.

What was *not* observed: the create-time poll. It fires while the QR is still
unscanned, so on its own it can never settle a payment. The settlement here came
from the sweep.

A second live payment measured the parts separately, because the customer paid
late and the end-to-end figure would have measured them, not us:

| | |
|---|---|
| sweep started polling the row | `07:53:34.314792Z` |
| row written `paid` | `07:53:34.522063Z` |
| `payment.completed` delivered to the merchant | `07:53:34.626681Z` |

So the poll itself is ~0.2 s and delivery adds ~0.1 s. **The sweep interval is the
entire detection budget**: 30 s worst case, ~0 s best case. Shortening that
interval (or getting ABA to push) is the only lever that would move the number
customers notice.

## 5. Rules & limits

- bill-number charset/length limits: **n/a for this path** — the bill number lives
  in ABA's own template (tag 62.50), not in a field we format
- min / max amount: **open** — no rail limit was probed. `0.10 USD` was accepted.
  Our own floors apply: `amount_too_low` below 1 cent, plus per-link
  `min_amount_cents`/`max_amount_cents`
- USD on QR confirmed? **Yes** — tag 53 = `840`, `"currency":"USD"`
- QR expiry behaviour on the rail: **the code lives 180 s; the checkout does
  not.** ABA returned `expire_in_sec: 180` and our `expires_at` mirrors it
  (created + 179.4 s). But on the second live payment the customer paid **9.5
  minutes after our mirrored expiry** — ABA answered `approved` and the money
  moved. So the 180 s governs the *QR image*, not the merchant's ability to
  accept the sale, and our window is strictly the narrower one.

  That matters operationally. W4 sweeps the row to `expired` at the deadline and
  fires `payment.expired`, and the money can still arrive afterwards. The sweep
  therefore deliberately keeps re-polling `expired` rows and `mark_paid` accepts
  the promotion, so a real payment is never discarded — observed working: the row
  went `expired` → `paid` and `payment.completed` fired nine minutes after
  `payment.expired`. A merchant dashboard must render that as a recovery, not as
  a contradiction. `POST /v1/payments/{id}/reissue` mints a fresh code for a
  genuine non-payment, and reusing it on a row that later settles is safe:
  reissue reuses a live successor rather than stacking sessions.

## 6. Decision

Primary detection strategy: **B — check-transaction**, against ABA's hosted status
endpoint, keyed by the `client_id`/`token`/`request_time` triple ABA returns when
it mints the code. Strategy A (push) is not available to us; strategy C
(reconcile against a ledger) would need the Bakong credentials in §1.

Gateway adapter: **already built** — `src/chmabapay/services/payway_parser.py`
(`create_hosted_checkout`, `fetch_hosted_status`), driven by
`services/payments.py::mint_qr` and confirmed by
`workers/w1_payment_detection.py`.

Blockers / open items for the bank or Bakong support:

1. Does ABA expose a **callback** for hosted checkouts? Polling every 30s is
   adequate but a push would remove the sweep interval from the critical path.
2. What are the **min/max** amounts on a hosted checkout, and does the ceiling
   differ per link?
3. §1 — is one credential able to act for many merchants? Only relevant if the
   offline QR ever needs to become payable.

## 7. What this spike did NOT establish

Stated plainly, because the rest of this file is evidence and this section is not:

- Nothing here validates the **offline** QR as payable. It is not.
- No **Bakong Open API** call was made. `BAKONG_API_TOKEN` was empty throughout.
  W1 skips its credential guard when a payment has a hosted session, which is why
  the run worked — and is also why a live run on a non-PayWay link would fail.
- **Refunds, reversals and partial payments were not tested.** A settled payment
  is treated as terminal here; nothing observed contradicts that, but nothing
  observed proves it either.
- The **attempt-history defect** surfaced by this run (only the first poll was
  ever recorded) is fixed and covered by
  `tests/test_payments.py::test_every_detection_attempt_is_recorded_not_only_the_first`.
  It is noted here because the latency figure in §4 was read from a field that
  was silently under-recording at the time.

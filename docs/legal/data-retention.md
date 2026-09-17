# Data retention and stored credentials — policy

> **Status: policy, of which the enforceable part is implemented and tested.**
> The retention window is a business decision that has been made (90 days) and is
> described to merchants in `/privacy`. The security note in section 4 is an
> accepted risk, recorded deliberately.

`docs/roadmap.md` requires two things of us: *"You will store per-merchant Bakong
credentials: secure them (vault + envelope encryption), define data-retention, and
include in a privacy policy."* This is the define-and-enforce half.

## 1. What we actually store that is credential-shaped

Checked in the schema, not assumed. There are three, and only one of them is a
long-lived secret.

| Field | What it is | Lifetime |
|---|---|---|
| `payments.gateway_status_raw` | The raw ABA PayWay response. For a hosted checkout it contains `client_id`, `request_time` and the session **`token`**. | Purged after 90 days |
| `payment_links.payway_client_id` | ABA's client identifier for the merchant's link | Kept with the store |
| `webhook_endpoints.secret_key` | The merchant's webhook **signing secret** | Kept while the endpoint exists |

Not credentials, verified rather than assumed:

- `api_keys.key_hash` — a one-way scrypt hash. The key itself is not recoverable.
- `accounts.password_hash` — a one-way hash.
- `payments.attempt_history` — derived detection signals (`signals`,
  `matched_amount_cents`, `note`), not raw gateway responses. Retained.

## 2. The retention rule, and how it is enforced

**Rule: raw payment-rail responses are deleted 90 days after the payment was
created.**

- Implemented in `src/chmabapay/services/retention.py` (`purge_gateway_payloads`).
- Driven by W5, `RetentionSweeperWorker` on the `payments.retention` queue,
  scheduled once a day *and* once at every boot. The boot run is deliberate: a job
  that only ever fires a day after start never fires at all on a service that
  restarts more often than that, and "the policy is enforced" would quietly become
  false.
- The window comes from `RETENTION_GATEWAY_RAW_DAYS` (default 90), so changing
  the policy changes the behaviour.
- A non-positive window is **refused, not obeyed**. `0` must never be readable as
  "delete all history"; the function logs and returns without purging.
- Idempotent and dedup-keyed per day, so replicas cannot purge the same day twice
  once P2-3 scales the workers out.

Tests: `tests/test_compliance.py` — a 91-day-old payload is purged, an 89-day-old
one is kept, the accounting record survives, a `0` window refuses, the configured
window is honoured, and a second run finds nothing left to do.

## 3. What is deliberately *not* purged

Payment amounts, statuses, timestamps, `bakong_ref`, the QR string and the bill
number are retained for as long as the account exists. They are the accounting
record of money that genuinely moved, and deleting them would destroy the evidence
a dispute or a tax audit depends on. `docs/roadmap.md`'s "define data-retention"
is not a requirement to delete everything; it is a requirement to decide, and this
is the decision.

## 4. Accepted risk: the webhook signing secret is stored in plaintext

`webhook_endpoints.secret_key` is stored as-is and used as-is
(`sign_payload(payload, endpoint.secret_key)`). It **must** be recoverable, because
we have to compute a signature with it on every outbound delivery — so it cannot
be hashed like an API key.

Decision: **documented as an accepted risk for P1-4, not fixed here.** Encrypting
it would need envelope encryption with a managed key, a migration for existing
rows, and changes to the signing path, the rotation path and the reveal-once path
in the CLI. That is a real security change and it deserves its own item rather
than being smuggled into a compliance one.

What it means in practice, stated plainly: **anyone with read access to the
database can forge a webhook delivery to a merchant.** The mitigation today is
database access control, not cryptography.

## 5. What this policy does not yet cover

- **No retention schedule for anything other than gateway payloads.** Webhook
  delivery records, events and audit rows grow without bound. Not urgent, but it
  is unbounded growth and should get its own decision.
- **No deletion-on-account-closure path.** The privacy policy promises we will
  delete or anonymise on request; that is currently a manual procedure with no
  code behind it.
- **No key rotation for the JWT signing secret or any other platform secret.**

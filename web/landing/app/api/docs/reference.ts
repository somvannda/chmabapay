/**
 * The API reference's tabular content: the error catalogue, the rate limits, and the
 * conventions a caller has to follow.
 *
 * Kept as data rather than inline JSX for one reason: `tests/test_api_docs_errors.py`
 * reads this file and fails when a refusal the API can actually return is not written
 * down here. A reference that is written by hand drifts, and the drift is invisible until
 * a merchant codes against a code that is no longer produced.
 *
 * **Scope (decision D-7, 2026-09-23).** Only the surface a merchant reaches with an API
 * key is documented here. The dashboard's own routes — key management, billing, the
 * account profile — are session-only, are not integration surface, and are described in
 * `docs/api.md` instead. Their codes are listed in `NOT_IN_REFERENCE` in the drift test,
 * so leaving them out is a decision rather than an oversight.
 *
 * `code` is the exact machine token the API puts in `detail`. Where a refusal is prose
 * rather than a token (the three plan-limit messages, the two report date messages) the
 * value is the literal leading text, with a `(…)` where the API interpolates a number —
 * those are deliberately not token-shaped, because they are not stable identifiers.
 */

export type ErrorRow = {
  status: number;
  code: string;
  meaning: string;
};

export type ErrorGroup = {
  title: string;
  summary: string;
  rows: ErrorRow[];
};

export const errorGroups: ErrorGroup[] = [
  {
    title: "Access",
    summary:
      "Returned before the request reaches a handler, so they can appear on any endpoint.",
    rows: [
      {
        status: 401,
        code: "unauthorized",
        meaning:
          "No API key, or one that is malformed, unknown or revoked. Send `Authorization: Bearer ck_live_…`.",
      },
      {
        status: 403,
        code: "account_restricted",
        meaning:
          "The account is on hold for an unpaid plan invoice, so writes are refused. Settle the invoice or move to the free plan on the billing page.",
      },
      {
        status: 403,
        code: "account_suspended",
        meaning: "An operator suspended the account. Nothing a caller can undo.",
      },
    ],
  },
  {
    title: "Stores",
    summary:
      "Store lifecycle, the payment link, and store alerts. `store_disabled` and `store_billing_suspended` both look like \"this store cannot take payments\" and have different fixes.",
    rows: [
      {
        status: 404,
        code: "store_not_found",
        meaning: "No store with that `public_id` on this account.",
      },
      {
        status: 400,
        code: "store_disabled",
        meaning:
          "The merchant or an operator switched the store off. Re-enable it with `POST /api/v1/stores/{public_id}/enable`.",
      },
      {
        status: 400,
        code: "store_billing_suspended",
        meaning:
          "The platform is holding the store because the account has more live stores than its plan allows. Bring it back with `POST /api/v1/stores/{public_id}/activate`.",
      },
      {
        status: 403,
        code: "whitelabel_not_enabled",
        meaning:
          "A branding field was sent but the account has no white-label entitlement. Clear the branding fields, or ask for the entitlement.",
      },
      {
        status: 400,
        code: "payway_link_invalid",
        meaning:
          "The link is not a well-formed ABA PayWay share link. The check is on shape, not existence.",
      },
      {
        status: 400,
        code: "payway_link_not_found",
        meaning:
          "The link is well-formed but ABA PayWay has no such link, so it would take payments nowhere. Re-copy the share link from the ABA app — a mistyped slug is the usual cause.",
      },
      {
        status: 400,
        code: "telegram_chat_id_not_set",
        meaning: "The store has no Telegram chat configured to send a test to.",
      },
      {
        status: 502,
        code: "telegram_send_failed",
        meaning: "Telegram rejected the send. Check the chat id, then retry.",
      },
      {
        status: 503,
        code: "telegram_not_configured",
        meaning: "This deployment has no Telegram bot configured at all.",
      },
      {
        status: 400,
        code: "Max stores (…)",
        meaning:
          "The plan's store allowance is used up. Upgrade to add more, or move an existing store back. The message names the limit.",
      },
    ],
  },
  {
    title: "Payments",
    summary:
      "Minting a code, and reading one back. These are the refusals a checkout has to handle, and several of them are recoverable in one call.",
    rows: [
      {
        status: 404,
        code: "payment_not_found",
        meaning: "No payment with that id on this account.",
      },
      {
        status: 400,
        code: "store_required_or_merchant_required",
        meaning:
          "The account has zero or several active stores, so the request has to name one: pass `store=` or `merchant=`.",
      },
      {
        status: 404,
        code: "merchant_not_found",
        meaning: "`merchant=` matches no store on this account.",
      },
      {
        status: 400,
        code: "merchant_store_disabled",
        meaning: "The store `merchant=` resolved to is disabled.",
      },
      {
        status: 400,
        code: "payment_link_disabled",
        meaning:
          "The store has no active payment link, so there is no destination to mint a code against. Attach one with `PUT /api/v1/stores/{public_id}/link`.",
      },
      {
        status: 400,
        code: "payment_link_missing",
        meaning: "The link row behind the payment no longer exists.",
      },
      {
        status: 400,
        code: "offline_qr_requires_a_confirmation_source",
        meaning:
          "`hosted_qr: false` was requested but this deployment has no way to confirm a code it builds itself, so the payment could never be settled. Omit `hosted_qr` and let ABA issue the code.",
      },
      {
        status: 400,
        code: "not_a_payway_link",
        meaning:
          "`hosted_qr` was requested, but the store's link is not an ABA PayWay link.",
      },
      {
        status: 400,
        code: "amount_too_low",
        meaning:
          "Below one cent, or below the minimum set on the store's link.",
      },
      {
        status: 400,
        code: "amount_too_high",
        meaning: "Above the maximum set on the store's link.",
      },
      {
        status: 402,
        code: "quota_exceeded",
        meaning:
          "The plan's monthly paid-payment allowance is spent. Upgrade, or wait for the period to reset.",
      },
      {
        status: 409,
        code: "payment_already_paid",
        meaning:
          "Reissue was attempted on a settled payment. A new code would take the money twice.",
      },
      {
        status: 409,
        code: "payment_reversed",
        meaning: "Reissue was attempted on a refunded payment.",
      },
      {
        status: 409,
        code: "payment_not_expired",
        meaning:
          "Reissue was attempted while the code is still live and payable. Wait for it to expire, or use the code that is out.",
      },
      {
        status: 409,
        code: "payment_not_paid",
        meaning:
          "A refund was recorded against a payment that never settled. Only a paid payment can be reversed.",
      },
      {
        status: 409,
        code: "payment_already_reversed",
        meaning: "That payment is already recorded as refunded.",
      },
      {
        status: 502,
        code: "payway_hosted_error",
        meaning:
          "ABA PayWay refused or failed the hosted-checkout call while minting the code. Retry; the payload is safe because the mint is idempotent on `idempotency_key`.",
      },
    ],
  },
  {
    title: "KHQR codes",
    summary:
      "The code-building routes. Hosted-checkout failures during payment creation are listed under Payments, because that is where a client meets them.",
    rows: [
      {
        status: 400,
        code: "invalid_link",
        meaning:
          "The link could not be read into a slug at all — empty, or with no slug segment. It is a shape check; `payway_link_not_found` is the one that means ABA does not know the slug.",
      },
      {
        status: 400,
        code: "payload_too_long",
        meaning:
          "The `payload` sent to `GET /api/v1/khqr/render.svg` is larger than a QR code can hold.",
      },
      {
        status: 400,
        code: "invalid_payload",
        meaning:
          "The `payload` sent to `GET /api/v1/khqr/render.svg` cannot be encoded as a QR code.",
      },
    ],
  },
  {
    title: "Webhooks",
    summary:
      "Delivery endpoints. A delivery that keeps failing is retried with backoff, up to eight attempts.",
    rows: [
      {
        status: 404,
        code: "webhook_not_found",
        meaning: "No webhook endpoint with that id on this account.",
      },
      {
        status: 400,
        code: "Max webhook endpoints (…)",
        meaning: "The plan's webhook-endpoint allowance is used up.",
      },
    ],
  },
  {
    title: "Reconciliation",
    summary: "Re-checking a payment against the rail.",
    rows: [
      {
        status: 404,
        code: "tx_not_found_yet",
        meaning:
          "No transaction confirms this payment yet. Nothing is wrong — check again shortly. The response appends the signals that were examined.",
      },
      {
        status: 503,
        code: "bakong_not_configured",
        meaning:
          "This deployment has no platform Bakong credentials, so a payment with no ABA-hosted session cannot be looked up. A payment minted through `hosted_qr` still reconciles, because ABA answers for it.",
      },
    ],
  },
  {
    title: "Hosted checkout",
    summary: "The public, unauthenticated pages a payer lands on.",
    rows: [
      {
        status: 404,
        code: "payment_has_no_qr",
        meaning: "The payment exists but has no stored code to render.",
      },
      {
        status: 410,
        code: "payment_{expired|failed|superseded|reversed}",
        meaning:
          "The code is dead, so the QR is withdrawn rather than redrawn — a replacement is a new payment, via `POST /api/v1/payments/{public_id}/reissue`. This is a normal outcome, not a fault: a payment can still settle after `expired`, and the row is promoted to `paid` when it does.",
      },
    ],
  },
  {
    title: "Reports",
    summary: "Export filters. Two of the refusals are prose rather than codes.",
    rows: [
      {
        status: 400,
        code: "Invalid 'from' date: (…)",
        meaning: "The `from` filter is not a valid ISO date (`YYYY-MM-DD`).",
      },
      {
        status: 400,
        code: "Invalid 'to' date: (…)",
        meaning: "The `to` filter is not a valid ISO date (`YYYY-MM-DD`).",
      },
    ],
  },
  {
    title: "Internal",
    summary:
      "Reachable, but it means a bug on our side rather than a mistake in the request. A caller cannot cause or fix it; it is listed so a 500 that arrives is identifiable.",
    rows: [
      {
        status: 500,
        code: "qr_render_failed",
        meaning: "A stored code could not be rendered as SVG.",
      },
    ],
  },
];

export type RateLimit = {
  rule: string;
  scope: string;
  limit: string;
  applies: string;
};

/**
 * `src/chmabapay/config.py` is the source. Changing a limit there without changing it
 * here is the kind of drift that shows up as a support ticket rather than a test
 * failure, so the numbers are stated plainly rather than described.
 */
export const rateLimits: RateLimit[] = [
  {
    rule: "api",
    scope: "per API key",
    limit: "600 / minute",
    applies: "every authenticated API request",
  },
  {
    rule: "payment_create",
    scope: "per API key",
    limit: "60 / minute",
    applies: "`POST /api/v1/payments`",
  },
  {
    rule: "khqr",
    scope: "per IP address",
    limit: "60 / minute",
    applies: "the KHQR routes",
  },
  {
    rule: "checkout",
    scope: "per IP address",
    limit: "120 / minute",
    applies: "the public `/pay/{id}` pages",
  },
  {
    rule: "auth",
    scope: "per IP address",
    limit: "20 / minute",
    applies: "sign-in",
  },
];

export type Convention = {
  title: string;
  body: string;
  code?: string;
};

export const conventions: Convention[] = [
  {
    title: "Authentication",
    body: "One credential per request: an API key (`Authorization: Bearer ck_live_…`) authenticates every store on the account. Keys are created in the dashboard, shown once, stored hashed, and revoked rather than edited. The dashboard's own surface — key management, billing, the account profile — is session-authenticated and is not part of this API.",
  },
  {
    title: "Idempotency",
    body: "`POST /api/v1/payments` accepts an `idempotency_key` in the body. Retrying with the same value returns the original payment instead of minting a second one, which is what makes a network retry safe. Use your own order id. The header form is not read — it must be in the body.",
    code: `curl -X POST "$CHMABA_API/api/v1/payments" \\
  -H "Authorization: Bearer $CHMABA_KEY" \\
  -H "Content-Type: application/json" \\
  -d '{ "amount": 5.50, "idempotency_key": "order_1042", "store": "st_…" }'`,
  },
  {
    title: "Amounts",
    body: "Amounts are sent as a decimal string or number with at most two decimal places — `\"5.50\"` — never in cents. Responses that *sum* money use integer cents instead, so every `*_cents` field is an integer and every `amount` field is a decimal. Currency comes from the store's link, not the request.",
  },
  {
    title: "Pagination",
    body: "List endpoints take `limit` and `offset` and wrap their rows: `{ \"data\": [ … ] }`. Most cap `limit` at 100. `GET /api/v1/reports/payments.json` adds a `summary` and a `pagination` block; `GET /api/v1/stores` returns every store and is not paginated, because an account's store count is bounded by its plan.",
    code: `curl "$CHMABA_API/payments?status=paid&limit=50&offset=100" \\
  -H "Authorization: Bearer $CHMABA_KEY"`,
  },
  {
    title: "Versioning",
    body: "The API is versioned in the path — every route is under `/api/v1`. Additive changes (a new field, a new endpoint, a new error code) ship within `v1`; a breaking change would arrive as `/v2` alongside it rather than replacing it.",
  },
  {
    title: "Errors",
    body: "A refusal answers with an HTTP status and a JSON body whose `detail` is a stable machine token — `quota_exceeded`, `store_disabled` — so a client can branch on it. Brand it, do not parse it out of prose. Request-validation failures are the exception: they answer with FastAPI's array shape and no stable code.",
  },
];

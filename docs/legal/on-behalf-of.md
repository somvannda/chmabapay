# On-behalf-of usage — written position

> **Status: OPEN.** This is the one item in P1-4 that cannot be closed by writing
> code or a document. It needs an answer from the National Bank of Cambodia /
> Bakong, and a lawyer to interpret it. This paper exists so the question is asked
> precisely and the exposure is on the record rather than in someone's head.

## The question

`docs/roadmap.md` Phase 0, and `docs/detection.md` §7, both ask the same thing:

> **Credential scope** — do your `client_id`/`secret` act on one merchant account
> only, or can they act *on behalf of* many merchant accounts?

And `docs/roadmap.md` flags the consequence:

> NBC/Bakong may have merchant-API terms about acting for third parties; **check
> on-behalf-of usage is permitted**. This is the single biggest legal/technical
> risk.

In plain terms: is it permitted for a third-party platform to hold one set of
credentials and use them to query the payment status of *many merchants'*
transactions?

## The answer the architecture already gives

Mostly, the question has been **designed out rather than answered**, and that is
worth stating precisely because it changes the shape of the residual risk.

The platform holds **no standing credential that can read a merchant's
transactions.** Payment confirmation works like this (verified in
`src/chmabapay/services/status_reconciler.py` and
`src/chmabapay/services/payway_parser.py`):

1. The merchant supplies **its own ABA PayWay share link**.
2. For each payment, we ask ABA for a hosted checkout against *that link*. ABA
   returns a session handle — `client_id`, `request_time`, `token` — which is
   **scoped to that single checkout**.
3. We store that handle on that one payment and use it to ask whether that one
   checkout was paid.

There is no platform-wide key that unlocks a merchant's transaction history. The
session is minted per payment, from the merchant's own link, and it expires within
minutes. So the question "may one set of credentials act for many merchants?" is
answered by construction for the ABA path: **we never hold such a credential.**

That is a materially better position than the Phase 0 concern anticipated, and it
should be stated that way in any regulatory conversation.

## The residual, which is genuinely open

**The Bakong Open API is the correct source and we cannot use it.**

Every KHQR settles on the Bakong rail, and Bakong indexes settled QR payments by
the MD5 of the QR string — which we compute at creation and store as
`payments.qr_md5`. That is exactly the key the wallet presented, so it is the right
way to ask "was this paid?" without depending on any single bank.

It is implemented. It cannot be used, because it needs a Bakong **developer token**,
and the position recorded in the code on **2026-09-15** is that the NBC declines to
register developer emails, so `verify_receipt` answers `401`. Re-confirm this
before relying on it; it may be a matter of how the request was made rather than a
policy.

**This is the on-behalf-of question in practice.** Asking the NBC for a developer
token that would let us query receipts for many merchants' QRs *is* the request to
act on behalf of third parties. The answer so far has been no — which is the same
answer Phase 0 was worried about, arrived at empirically.

## What to ask, in writing

The exchange must be in writing with the NBC / Bakong, not a phone call. Ask:

1. May a software platform that is **not** a bank hold Bakong API credentials and
   use them to check the payment status of QRs belonging to **other, unrelated
   merchants**?
2. If yes: what is the registration route, and what does the platform have to be
   (licensed? registered? contracted with each merchant?) to qualify?
3. If no: what is the supported way for a merchant's own POS or e-commerce system
   to learn that its QR was paid, when the platform is the merchant's software
   vendor rather than the merchant?
4. Does the answer differ if the platform queries with **per-merchant credentials
   supplied by the merchant** rather than one shared platform credential? (We
   would prefer this shape: it removes on-behalf-of entirely, at the cost of each
   merchant having to obtain its own token.)
5. Are there additional terms for acting as a third party, and do they need to be
   flowed down to merchants?

Put the same question to **ABA PayWay**, since the only working confirmation path
today is ABA's hosted checkout. Confirm that programmatically polling a checkout
session that ABA itself issued, for a merchant's own link, is expected use of the
API rather than an abuse of it.

## Exposure if the answer is no

Stated honestly, because this is what the risk actually is:

- **We keep depending on ABA.** Confirmation works today only because ABA issued
  the session. If ABA's hosted checkout changed or was withdrawn, the platform
  would have no working source, and payments would sit unconfirmed.
- **We cannot verify receipts independently.** The Bakong path is the
  rail-of-record check that does not depend on one bank's API. Without it, "paid"
  means "ABA says paid".
- **No redesign removes this.** A different architecture does not help: any
  platform that reports on payments it does not process must read that status from
  somewhere, and every somewheres is governed by someone's terms.

## Current decision, and what would change it

**Decision: proceed on the ABA hosted-checkout path, which requires no
on-behalf-of credential, and treat the Bakong developer token as blocked pending a
written answer.** This keeps the live money path working while the question is
outstanding.

What would change it: a written answer from the NBC authorising on-behalf-of
receipt lookups, or per-merchant Bakong credentials becoming obtainable — at which
point `reconcile_payment` starts working as written and the dependency on ABA
becomes a fallback rather than the only source.

# On-behalf-of usage — written position

> **Status: RISK ACCEPTED, dated 2026-09-23.** The written answer from the National
> Bank of Cambodia / Bakong has **not** been obtained, and this document does not
> pretend otherwise. What follows is (a) what the public record actually says,
> researched on 2026-09-23, (b) the position the operator is adopting anyway, and
> (c) the specific conditions that would make that position untenable. Read (b) and
> (c) together; (b) without (c) is just optimism.
>
> **This is not legal advice.** It was compiled from the NBC's own published
> integration document, public Bakong API material and secondary law-firm summaries.
> No Cambodian lawyer has reviewed it and no regulator has confirmed it. Every
> secondary source is named as such, because the difference between "the NBC
> published this" and "a consultancy website says this" is the whole point.

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

## What the public record says — researched 2026-09-23

Five separate questions, and they have different answers. Keeping them apart is
most of the work; conflating them is how a company concludes it is fine. This
section is the researched counterpart to `docs/bakong-gateway-notes.md` §1, which
records the same question as **open** from the Phase 0 spike and notes it matters
only if we ever want to confirm a QR we build ourselves (§2 there — our own encoder
is not payable, nothing on ABA's side has a record of the code).

### 1. Is polling status written for the merchant, or for a platform acting for one?

**For the merchant, and third-party software is an anticipated reader.** The NBC's
own *QR Payment Integration* document (`bakong.nbc.gov.kh/download/KHQR/
integration/QR Payment Integration.pdf`, v1.0.3, 06.08.2021) describes the
supported flow and names its intended audience:

> "The expected readers are NBC technical team, third-party technical, POS service
> provider, software developers, etc."

and the step itself is the merchant's:

> "Merchant back-end sets a time interval in a specific second to call API Get
> transaction with MD Hash to the Bakong system to check payment results."

So polling by MD5 is the **documented, intended** mechanism, and a POS vendor or
"third-party technical" is expected to read the document. What the document does
**not** say — anywhere — is whether one integrator's credential may serve many
unrelated merchants. That silence is the actual gap, and it is the whole subject of
this paper.

### 2. Does a Bakong credential scope itself to one merchant?

**No. Mechanically it cannot.** Access works by registering a single **developer
email** with the NBC's developer portal (`https://api-bakong.nbc.gov.kh/register`),
exchanging it for a bearer token (`POST /v1/renew_token`), and then calling
`/v1/check_transaction_by_md5` with **the MD5 of any KHQR string**. The token is
keyed on the developer, not on a merchant account, and the lookup is keyed on the
QR — so one token can query the receipts of every QR whose MD5 you hold. Community
SDK documentation for the same API describes exactly this shape, and notes the
token must be renewed roughly every 90 days (the `bakong-khqr` PHP/Node/Ruby/Rust
clients all state this).

**This is the on-behalf-of question in mechanical form.** The API does not forbid
the pattern; it simply does not distinguish one merchant from another. Absence of a
prohibition is not permission, and should never be recorded as though it were.

### 3. Does the platform need an NBC licence to operate at all?

**Open, and the most consequential unanswered question** — it is item 3 of the
"open questions for legal review" in `docs/legal/merchant-agreement.md`.

What is documented. The NBC licenses two different things (secondary law-firm
summaries, consistently): a **Payment Service Institution (PSI)** under the 1999
Law on Banking and Financial Institutions and the *Prakas on the Management of
Payment Service Institutions* of 20 June 2017, and a **third-party processor**
under Prakas B9-010-151 of 25 August 2010, amended by Prakas B7-019-420 of
6 December 2019, whose five sub-categories are communication facility, interbank
clearing facility, mobile or other remittance provider, account-management agent,
and payment-order sending and receiving point. The PSI activity list is reported to
include holding payment accounts, deposits and withdrawals, transfers and
remittances, **payment-transaction processing**, e-money issuance, money changing,
and short-term credit tied to payments.

What that means here. ChmabaPay takes **no possession of funds at any point**: money
moves from the payer's wallet, over Bakong, into the **store's own** ABA account.
The platform never holds a balance, never aggregates merchant money, never settles,
and never issues stored value. The widely repeated formulation of the international
rule is that a **pure technology layer — merchants bring their own acquiring
relationship, the platform never touches or settles funds — may need no payment
licence at all**, and that the obligation begins the moment you settle funds, hold
balances, aggregate merchant transactions or issue e-money. That formulation is
from a general industry source, not from Cambodian authority, and is recorded here
as *reasoning that supports our reading*, not as an exemption we have been granted.

The honest residual: we **do** generate the QR, **do** poll for its status, and
**do** report that status to the merchant. `docs/legal/merchant-agreement.md`
already poses the right question — "does the software-only framing hold given we
*poll* a merchant's transactions and *report* status to them — or does reporting
make us a party to the payment?" Nobody has answered it. Reporting is arguably what
a POS vendor does; it is also arguably what a payment processor does. We are on the
line, and we should say so.

### 4. Data protection, if we ever do query Bakong

**Nothing bites today; two things would.** As of February 2026 Cambodia had **no
enacted comprehensive data protection law**: the draft Personal Data Protection Law
is still with the Ministry of Post and Telecommunications (latest consultation July
2025), Sub-Decree 252 of December 2021 covers only identification data held by the
Ministry of Interior, and the general protections are the constitutional right to
privacy plus provisions in the Civil Code, Criminal Code, E-Commerce Law and
Banking Law. There is no data protection authority and no registration requirement.

Two conditions change that, and both are worth watching:

- **If the draft PDP Law passes as circulated.** Articles 22–24 as made available
  for comment in 2023 would prohibit transfers of personal data out of Cambodia and
  require locally collected personal data to be stored in Cambodia. That would
  reach the platform's hosting, not just Bakong lookups.
- **If the platform ever becomes an NBC-licensed institution.** The NBC's Technology
  Risk Management Guidelines, which apply to licensed banks and financial
  institutions, restrict international transfer of data and require notification
  where outsourcing involves significant functions or storage of operational data
  abroad. Licensing would therefore pull in a data-residency question we do not
  have today.

A receipt lookup returns the payer's account id and the amount. That is personal
data in any reading of the term. The cheap mitigation is data minimisation, and it
is already the design: we store the MD5, the amount and the status — we do not
store the payer's identity, and we have no use for it.

### 5. A practical blocker, independent of the legal one

Multiple community SDK maintainers report **HTTP 403 from Bakong's API when requests
originate outside Cambodia**, and a paid relay service now exists specifically to
sell tokens that route around it. We should not be surprised by a 403 from a
non-Cambodian host, and we should not mistake it for a credentials problem. This
matters because it is a reason the Bakong path may stay unusable even if the
regulatory question is answered favourably — which reduces the value of resolving
the regulatory question *before* the hosting decision is settled.

## Position adopted — 2026-09-23

**Proceed on the ABA hosted-checkout path, which requires no on-behalf-of
credential. Treat the Bakong developer token as blocked, and do not pursue it
further until a merchant or the operator has a reason to spend the effort.**

Concretely, this accepts the following risks, knowingly:

| # | Risk accepted | Why it is tolerable now | What makes it intolerable |
| --- | --- | --- | --- |
| R1 | No independent rail-of-record confirmation. "Paid" mean "ABA says paid". | It works, it is per-payment, and it was proven end to end on 2026-09-17 against a real wallet. | ABA changes or withdraws the hosted-checkout API. |
| R2 | The unlicensed-technology-layer reading is untested against the NBC. | We hold no funds, settle nothing, and aggregate no merchant money. Nobody has been asked to object. | Any move into holding balances, netting payments across merchants, or charging the payer. Stop and get advice before that ship sails. |
| R3 | `verify_receipt` is implemented and unusable; the code path is a claim we cannot honour. | It is unadvertised — it is not in the public API reference (see decision D-7) — so no merchant is promised it. | Advertising it, or wiring it into the confirmation path as anything but a fallback. |
| R4 | Payer personal data is not currently subject to an omnibus regime. | The draft law is not enacted and we deliberately store no payer identity. | Enactment of the draft PDP Law's cross-border articles, or the platform becoming NBC-licensed. Re-read this section then. |

**Explicitly not accepted:** acting as a payment institution without a licence. If
the business model changes so that ChmabaPay holds, nets, settles, or aggregates
merchant funds — or charges payers — the analysis above does not cover it, and the
question goes to the NBC *before* the change ships, not after.

**Recorded evidence for R3.** The position recorded in the code on 2026-09-15 is
that the NBC declined to register the developer email, so `verify_receipt` answers
`401`. That is consistent with "the answer so far is no", and it is why the Bakong
path is treated as blocked rather than merely unavailable. Re-confirm before relying
on it; it may be a matter of how the request was made rather than a policy.

## What to ask, in writing — when there is a reason to ask

The exchange must be in writing with the NBC / Bakong, not a phone call. The list
is kept here so the question is already framed when it matters. Ask:

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
6. Separately: does a platform in ChmabaPay's shape — generating KHQRs, polling
   status, reporting to the merchant, never holding or settling funds — fall within
   the *Prakas on the Management of Payment Service Institutions* of 2017 at all,
   or within any third-party processor sub-category under Prakas B9-010-151?

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
  rail-of-record check that does not depend on one bank's API.
- **No redesign removes this.** A different architecture does not help: any
  platform that reports on payments it does not process must read that status from
  somewhere, and every somewheres is governed by someone's terms.

## What would change this

- A written answer from the NBC authorising on-behalf-of receipt lookups, **or**
  per-merchant Bakong credentials becoming obtainable — at which point
  `reconcile_payment` starts working as written and the dependency on ABA becomes a
  fallback rather than the only source.
- Enactment of the draft Personal Data Protection Law, or the platform becoming
  NBC-licensed. Either one re-opens R4 and §4 above.
- Any change to what the platform does with money: holding, netting, settling,
  aggregating, or charging the payer. That invalidates the whole position and is the
  one trigger that must stop a release rather than be noted after it.

## Sources

Researched 2026-09-23. Primary documents are the NBC's; everything else is named as
secondary so it can be discounted appropriately.

- **Primary.** National Bank of Cambodia, *QR Payment Integration* (KHQR dynamic QR
  specification), v1.0.3, 06.08.2021 —
  `bakong.nbc.gov.kh/download/KHQR/integration/QR Payment Integration.pdf`. Source of
  the merchant-side polling flow, the MD5 lookup, the 10-minute QR expiry guidance
  and the stated audience ("third-party technical, POS service provider, software
  developers").
- **Primary (API surface).** Bakong Open API developer portal and reference,
  `https://api-bakong.nbc.gov.kh/register` and `/document` — developer-email
  registration, `POST /v1/renew_token`, `check_transaction_by_md5`.
- **Secondary.** Community SDK documentation for that API (`bakong-khqr` for PHP,
  Node and Rust; `bakong-open-api` for Ruby) — corroborates the registration route,
  the ~90-day token renewal and the per-developer (not per-merchant) token scope.
- **Secondary.** Tilleke & Gibbins, *Cambodia Increases Oversight of E-Wallet
  Customer Programs*, 13 August 2026 — article 20 of the 2017 Prakas; the
  single-purpose e-money exemption and its limits.
- **Secondary.** Law-firm and licensing summaries of the NBC framework — the PSI
  route under the 1999 Banking Law and the 2017 Prakas, and the third-party
  processor route under Prakas B9-010-151 as amended by Prakas B7-019-420. Note one
  correction that recurs in secondary writing: there is **no** Cambodian "Law on
  Payment Systems of 2021".
- **Secondary.** DLA Piper, *Data Protection in Cambodia*, updated 13 February 2026
  — no enacted comprehensive law; draft PDP Law status; Sub-Decree 252's limited
  scope; the Technology Risk Management Guidelines' data-transfer provisions for
  NBC-licensed institutions.
- **Secondary.** Global Data Alliance, comments on the Draft Law on Personal Data
  Protection, 5 October 2023 — the text of Articles 22–24 as circulated.
- **Secondary.** `bakongrelay.com` (observed 2026-09-23) — a paid service that
  exists to work around HTTP 403 from non-Cambodian hosts, which is the evidence for
  §5.

# Merchant agreement — draft

> **Status: unreviewed first draft. Not legal advice. Not in force.**
>
> Written from the system's actual behaviour so there is a concrete document for a
> Cambodian lawyer to edit, rather than a blank page. It must be reviewed, and
> where the review disagrees with what is written here, the review wins.
>
> Nothing in this repository should be presented to a merchant as a binding
> agreement until that review is recorded (see P1-4 in
> `docs/production-readiness.md`).

## Why this document exists separately from the website Terms

`/terms` covers the same ground for a self-serve signup. A **merchant agreement**
is the signed contract behind a merchant account, and it needs three things the
website copy is a poor home for:

1. a **no-custody** clause that is unambiguous about the money never touching us;
2. a **restricted-business** clause the merchant actually accepts;
3. terms about **credentials and data** — what we hold, for how long, and what
   happens on termination.

`docs/roadmap.md` reaches the same conclusion from the CutLuy comparison: the
software-only model is defensible, but *"your contract with merchants must be
explicit that you never hold or move funds."*

## 1. The relationship

Chmaba ("we"), registered at #62, Street P-10D, Sangkat Veal Sbov, Khan Chmbar
Ampov, Phnom Penh, Cambodia, provides software that generates payment codes and
reports whether they were paid. The merchant ("you") uses it to collect payment
from your own customers.

## 2. No custody — the clause that must not be weakened

- Money moves **directly** from the payer to the bank account behind the payment
  link you provide. It does not pass through us, at any point, in any currency.
- We are not a payment service provider, acquirer, money transmitter, escrow
  agent or trustee. We do not hold a float, a balance, or funds in transit.
- We cannot refund a payer, reverse a payment, or release funds to you. We have no
  technical ability to do any of these, because we never have control of the
  funds.
- Refunds, disputes, chargebacks and customer complaints are yours, at your cost,
  under your own policies and your own relationship with the payer.

**Before signing off, a lawyer should confirm** that this is sufficient under
Cambodian law to keep the arrangement outside money-transmission and
payment-service licensing, and check the position of the National Bank of Cambodia
and Bakong on software that reports on transactions it does not process (see
`docs/legal/on-behalf-of.md`).

## 3. Restricted businesses

The merchant warrants that it will not use the service for: gambling, betting,
lotteries or games of chance; money laundering or terrorist financing; anything
subject to sanctions; unlicensed financial services including unlicensed lending,
deposit-taking or money transmission; goods or services illegal where either party
is located; counterfeit, stolen or IP-infringing goods; weapons, ammunition or
controlled substances; adult content or services where restricted; and deceptive
or pyramid-style schemes.

**Open points:**
- The list above is drawn from `docs/roadmap.md`, which says to mirror CutLuy's
  restricted-business list. **That list has not been copied or compared** — it
  should be, before this clause is relied on.
- The wording should distinguish a **warranty** (a statement of fact, actionable
  if false) from a **covenant** (a promise about future conduct). As drafted it
  blurs the two.
- **The platform cannot currently enforce any of this.** There is no
  business-category field, no verification step, and no automated block. All this
  clause has is contract. That is a deliberate decision recorded in P1-4, and it
  should be a conscious one rather than a gap someone discovers later.

## 4. Credentials and data

- **Merchant credentials.** The merchant must hold the right to receive money into
  any payment link it attaches, and must not attach a third party's link without
  authority.
- **API keys.** A key authenticates every store in the merchant's workspace. The
  merchant must keep keys and webhook signing secrets confidential, and revoke a
  key immediately if it may have leaked.
- **What we store, and for how long.** See `docs/legal/data-retention.md` and the
  published privacy policy. In summary: raw payment-rail responses (which contain
  a short-lived session token) are deleted after 90 days by an automated sweep;
  the accounting record of a payment is retained for as long as the account
  exists.
- **Our own access.** Platform operators can suspend an account and can see
  account data. Every privileged action is written to an audit trail naming the
  actor.
- **On termination.** Payment records needed for tax, accounting or a live dispute
  are retained; the rest is deleted or anonymised on request.

## 5. Service reality the agreement must not overstate

These are measured facts, not cautious hedging, and the agreement should reflect
them rather than promise more:

- **Confirmation depends on ABA.** The only working confirmation source today is
  ABA's hosted checkout session. If that rail is unavailable, payments are not
  confirmed — they are not "lost", but we cannot report them.
- **Bakong Open API is currently unavailable to us.** It is implemented, and it is
  the technically correct source, but it needs a developer token that the NBC has
  declined to issue. See `docs/legal/on-behalf-of.md`.
- **We cannot see the moment money moves.** We see the moment we looked and the
  rail agreed. Anything the agreement says about accuracy should be phrased as
  "we report what the rail reports".

**Do not add an uptime or latency commitment to this agreement** without a
decision to fund the availability work for it. There is no SLA-supporting
infrastructure in place, and none is planned before P2.

## 6. Governing law

Drafted as the Kingdom of Cambodia, courts of Cambodia, exclusive jurisdiction.
**A lawyer should confirm** — and should confirm whether the dispute-resolution
clause should specify arbitration instead, which is common for cross-border
payment software.

## 7. Open questions for legal review

1. Is the no-custody wording sufficient to avoid a money-transmission licence?
2. Does the software-only framing hold given we *poll* a merchant's transactions
   and *report* status to them — or does reporting make us a party to the payment?
3. Must we register, license, or notify any Cambodian authority to operate?
4. CutLuy's restricted-business list — obtain and compare.
5. Should disputes go to arbitration rather than the courts?
6. What must the agreement say about the plaintext storage of webhook signing
   secrets (see the accepted-risk note in `docs/legal/data-retention.md`)?

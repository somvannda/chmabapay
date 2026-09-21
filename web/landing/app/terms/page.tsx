import type { Metadata } from "next";

/**
 * Terms of Service.
 *
 * Two things to know before editing this file.
 *
 * 1. **This text has still not been reviewed by a lawyer.** Every page carrying
 *    terms, privacy or merchant-agreement language in this repository was written
 *    from the system's actual behaviour as a first draft, so there is a concrete
 *    artifact for a Cambodian lawyer to edit rather than a blank page — and that
 *    review is still outstanding (`P1-4` in `docs/production-readiness.md`).
 *
 *    A visible "Draft / not yet binding" banner used to sit at the top of this
 *    page. It was removed on 2026-09-18 by an explicit product decision to open
 *    the service to real merchant traffic, **not** because the text was
 *    reviewed. Do not read its absence as approval. When the review does happen,
 *    replace this comment and the same one in `privacy/page.tsx` at the same time
 *    as the text they describe.
 *
 * 2. **`TERMS_VERSION` in the backend must move with this page.** The recorded
 *    acceptance stores the version the merchant was shown, so the two are a pair:
 *    a substantive edit here without bumping `terms_version` in
 *    `src/chmabapay/config.py` would silently attribute agreement to this text to
 *    merchants who only ever saw the previous one.
 */
export const metadata: Metadata = {
  title: "Terms of Service — ChmabaPay",
  description:
    "The terms governing use of the ChmabaPay payment status platform.",
  // Without these the page inherits the landing page's card, so a link to the
  // merchant agreement shared with a lawyer or a customer previewed as the
  // marketing home page. Same shape as /api/docs.
  alternates: { canonical: "/terms" },
  openGraph: {
    type: "article",
    title: "Terms of Service — ChmabaPay",
    description:
      "What ChmabaPay is and is not, what a merchant is responsible for, and how plan fees work.",
    url: "/terms",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Terms of Service — ChmabaPay",
    description:
      "What ChmabaPay is and is not, what a merchant is responsible for, and how plan fees work.",
    images: ["/og-image.png"],
  },
};

export default function TermsPage() {
  return (
    <main className="legal-page">
      <div className="landing-shell">
        <div className="legal-shell">
          <span className="legal-eyebrow">Legal</span>
          <h1 className="legal-title">Terms of Service</h1>
          <p className="legal-lede">
            These terms govern your use of ChmabaPay, a payment status platform for
            KHQR, Bakong and ABA PayWay. They are between you and Chmaba, whose
            registered address is #62, Street P-10D, Sangkat Veal Sbov, Khan Chmbar
            Ampov, Phnom Penh, Cambodia.
          </p>
          <div className="legal-meta">
            Version 2 · Last updated 21 September 2026
          </div>

          <div className="legal-body">
            <section className="legal-section">
              <h2>1. What ChmabaPay is — and what it is not</h2>
              <p>
                ChmabaPay is software. It generates a payment code for an amount
                you choose, reports whether that code was paid, and sends your
                systems a notification when it is. That is the whole of the
                service.
              </p>
              <p>
                <strong>
                  We never hold, receive, control or transmit your money or your
                  customers&apos; money.
                </strong>{" "}
                A payment moves directly from the payer to the bank account behind
                the payment link you supplied. The funds do not pass through
                ChmabaPay at any point, we cannot reverse a payment, and we cannot
                release funds to you. We are not a payment service provider, an
                acquirer, a money transmitter or an escrow agent, and nothing in
                these terms should be read as making us one.
              </p>
              <p>
                Because money does not flow through us, we have no ability to
                refund a payer, charge back a transaction, or hold a balance on
                your behalf. Refunds, disputes, chargebacks and customer
                complaints are yours to handle under your own relationship with
                the payer.
              </p>
            </section>

            <section className="legal-section">
              <h2>2. Your responsibilities</h2>
              <ul>
                <li>
                  <strong>Lawful business only.</strong> You may use ChmabaPay only
                  for a lawful business, and only to collect payment for goods or
                  services you actually provide.
                </li>
                <li>
                  <strong>Your own due diligence.</strong> You are responsible for
                  your own compliance obligations — including knowing your own
                  customers, keeping your own records, and meeting your tax,
                  licensing and reporting duties. ChmabaPay does not perform those
                  obligations for you and does not verify your identity or your
                  business.
                </li>
                <li>
                  <strong>Refunds and disputes.</strong> You handle them, at your
                  own cost, under your own policies.
                </li>
                <li>
                  <strong>Your destination account.</strong> You must have the
                  right to receive money into the account behind any payment link
                  you attach. If you attach a link belonging to someone else
                  without their authority, that is your liability, not ours.
                </li>
                <li>
                  <strong>Your credentials.</strong> Keep your API keys and webhook
                  signing secrets secret. A key authenticates every store in your
                  workspace, so treat a leaked key as a leaked password and revoke
                  it immediately. We are not responsible for transactions you
                  authorised, or someone else authorised with your key.
                </li>
              </ul>
            </section>

            <section className="legal-section">
              <h2>3. Restricted businesses</h2>
              <p>
                You may not use ChmabaPay, directly or indirectly, in connection
                with:
              </p>
              <ul>
                <li>gambling, betting, lotteries or games of chance;</li>
                <li>
                  money laundering, terrorist financing, or any activity on a
                  sanctions list;
                </li>
                <li>
                  unlicensed financial services, including unlicensed lending,
                  deposit-taking or money transmission;
                </li>
                <li>
                  the sale of goods or services that are illegal where you or the
                  payer are located;
                </li>
                <li>
                  counterfeit goods, stolen goods, or goods infringing intellectual
                  property;
                </li>
                <li>weapons, ammunition or controlled substances;</li>
                <li>
                  adult content or services, where restricted or prohibited;
                </li>
                <li>
                  deceptive, fraudulent or pyramid-style schemes, including
                  anything that misrepresents what the payer is buying.
                </li>
              </ul>
              <p>
                We may suspend or terminate an account we reasonably believe is
                being used for any of the above. This list is not exhaustive, and we
                may add to it under section 9. If you are unsure whether your
                business falls under one of these categories, ask us before you
                start.
              </p>
            </section>

            <section className="legal-section">
              <h2>4. Confirmation is reported, not guaranteed</h2>
              <p>
                Payment confirmation depends on ABA PayWay, Bakong and the National
                Bank of Cambodia&apos;s systems, which we do not control. We report
                what those systems tell us, as promptly as we can. We do not
                guarantee that a confirmation will arrive within any particular
                time, that a notification will be delivered, or that the rail will
                be available.
              </p>
              <p>
                <strong>
                  Do not treat a ChmabaPay notification as the sole evidence that
                  you were paid.
                </strong>{" "}
                Reconcile against your own bank account. Where our report and your
                bank statement disagree, your bank statement governs.
              </p>
            </section>

            <section className="legal-section">
              <h2>5. Your account</h2>
              <p>
                You are responsible for the activity on your account and for the
                accuracy of the information you give us. Tell us promptly if you
                believe your account or a key has been compromised.
              </p>
              <p>
                You may close your account at any time. We may suspend or terminate
                an account that breaches these terms, that we are required to
                suspend by law or by a payment partner, or where we reasonably
                believe continued service would expose us or a third party to
                legal or financial risk.
              </p>
            </section>

            <section className="legal-section">
              <h2>6. Plans and fees</h2>
              <p>
                Plan prices, limits and features are shown in the pricing section of
                our website, are billed in United States dollars, and may change.
              </p>
              <p>
                Moving onto a paid plan is a purchase rather than an immediate
                switch. When you choose a paid plan we raise an invoice for its
                period and leave your current plan in place; the new plan takes
                effect once that invoice is paid. Until then the new plan grants
                nothing. Moving to the free plan takes effect immediately, because
                there is nothing to collect. Each period is invoiced when it begins.
              </p>
              <p>
                An invoice is issued once per account per calendar month, so a second
                move to a paid plan in the same month cannot be billed and is
                refused. We do not charge a fee per transaction: ChmabaPay is a
                subscription service, and your customers&apos; payments never pass
                through us to be deducted from.
              </p>
            </section>

            <section className="legal-section">
              <h2>7. Availability</h2>
              <p>
                We aim to keep ChmabaPay running, but we do not promise any
                particular level of uptime, and we do not offer a service level
                agreement in these terms. Planned and emergency maintenance may
                interrupt the service.
              </p>
            </section>

            <section className="legal-section">
              <h2>8. Limitation of liability</h2>
              <p>
                To the fullest extent permitted by law, ChmabaPay is not liable for
                lost profits, lost revenue, lost or corrupted data, or indirect or
                consequential loss arising from your use of the service, from a
                payment that was not confirmed or not reported, from a notification
                that was not delivered, or from the acts or omissions of ABA,
                Bakong or the National Bank of Cambodia.
              </p>
              <p>
                Nothing in these terms excludes liability that cannot lawfully be
                excluded.
              </p>
            </section>

            <section className="legal-section">
              <h2>9. Changes to these terms</h2>
              <p>
                We may update these terms. When we do, we increase the version
                number shown above and ask you to accept the new version before
                continuing to use the service. Your acceptance is recorded against
                the version you were shown.
              </p>
            </section>

            <section className="legal-section">
              <h2>10. Governing law</h2>
              <p>
                These terms are governed by the laws of the Kingdom of Cambodia,
                and the courts of Cambodia have exclusive jurisdiction over any
                dispute arising from them.
              </p>
            </section>

            <div className="legal-contact">
              Questions about these terms, or a request relating to your data, can
              be sent to <strong>legal@chmaba.com</strong>. For support, use{" "}
              <strong>support@chmaba.com</strong>.
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

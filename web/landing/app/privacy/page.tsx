import type { Metadata } from "next";

/**
 * Privacy Policy.
 *
 * The data inventory below is written from the schema in
 * `src/chmabapay/models.py` and the behaviour of the retention sweep in
 * `src/chmabapay/services/retention.py`, not from a template. If a column is
 * added, removed, or starts being purged, this page is part of that change.
 *
 * The draft banner works the same way as on /terms: it is the flag that this text
 * has not been reviewed, and removing it is the deliberate act that says review
 * happened.
 */
export const metadata: Metadata = {
  title: "Privacy Policy — ChmabaPay",
  description:
    "What ChmabaPay stores, how long it is kept, and who else sees it.",
};

export default function PrivacyPage() {
  return (
    <main className="legal-page">
      <div className="landing-shell">
        <div className="legal-shell">
          <div className="legal-draft" role="note">
            <span className="legal-draft-mark">Draft</span>
            <span className="legal-draft-text">
              This is a first draft written from the system&apos;s actual data
              model. It has not been reviewed by a qualified lawyer and must be
              reviewed and replaced before ChmabaPay accepts real merchant
              traffic.
            </span>
          </div>

          <span className="legal-eyebrow">Legal</span>
          <h1 className="legal-title">Privacy Policy</h1>
          <p className="legal-lede">
            This describes what ChmabaPay stores, why, how long we keep it, and who
            else sees it. It is written from the system itself, so it reflects what
            the software really does rather than what a policy template assumes.
          </p>
          <div className="legal-meta">
            Version 1 · Last updated 15 September 2026
          </div>

          <div className="legal-body">
            <section className="legal-section">
              <h2>1. Who we are</h2>
              <p>
                ChmabaPay Technologies operates a payment status platform for KHQR,
                Bakong and ABA PayWay. We are not a bank or a payment service
                provider: money moves directly between the payer and the merchant,
                and never through us. We therefore hold no balances, and we hold no
                card, bank account or wallet credentials for you or your customers.
              </p>
            </section>

            <section className="legal-section">
              <h2>2. What we store</h2>

              <h3>About your account</h3>
              <ul>
                <li>your email address and display name;</li>
                <li>
                  the Google account identifier, if you sign in with Google,
                  together with the session cookie described in section 6;
                </li>
                <li>
                  a one-way hash of your password, if you set one — never the
                  password itself;
                </li>
                <li>
                  your account status, account type, and whether you are a platform
                  administrator;
                </li>
                <li>
                  the version of our Terms you accepted and the moment you accepted
                  it;
                </li>
                <li>timestamps for creation and last update.</li>
              </ul>

              <h3>About your stores and payment links</h3>
              <ul>
                <li>
                  the store name, your external identifier for it, and its public
                  identifier;
                </li>
                <li>
                  the contact details you provide for a store: owner name, phone and
                  email, a support email, and a city;
                </li>
                <li>
                  the checkout appearance and behaviour you configure for a store:
                  brand colour, logo URL, custom CSS if you supply it, and the
                  success and failure URLs a payer is sent to after checkout;
                </li>
                <li>
                  the ABA PayWay link you attach, the merchant account identifier it
                  points at, the merchant name it carries, and the ABA client
                  identifier associated with it;
                </li>
                <li>
                  the link&apos;s currency, status and any amount limits you set;
                </li>
                <li>
                  the Telegram chat identifier, if you enable store alerts.
                </li>
              </ul>

              <h3>About each payment</h3>
              <ul>
                <li>
                  the amount, currency, status, bill number, your own reference, and
                  any metadata you choose to attach;
                </li>
                <li>
                  the payment code itself (the KHQR string) and a checksum of it;
                </li>
                <li>
                  timestamps: created, expiring, scanned, paid, approved;
                </li>
                <li>
                  the reference the banking rail returns for the transaction, and a
                  record of how each confirmation attempt went;
                </li>
                <li>
                  the raw response the payment rail returned for that payment. This
                  can include a short-lived session token and limited information
                  supplied by the banking system about the sending side of the
                  transfer, such as a bank name or an account name.{" "}
                  <strong>
                    We keep this raw response for at most 90 days and then delete
                    it
                  </strong>{" "}
                  (see section 4). The token is useless long before that, as it
                  expires within minutes.
                </li>
              </ul>

              <h3>About your integration</h3>
              <ul>
                <li>
                  API keys: a prefix for display, and a one-way hash of the key.
                  The key itself is shown once at creation and is not recoverable
                  from our systems;
                </li>
                <li>
                  for each key: its name, mode, status, when it was created, when it
                  was last used, and when it was revoked;
                </li>
                <li>
                  webhook endpoints: the URL, the events you subscribe to, and the
                  signing secret used to sign deliveries to you;
                </li>
                <li>
                  a delivery record per event: attempts, response codes and
                  timings.
                </li>
              </ul>

              <h3>About actions taken in your account</h3>
              <ul>
                <li>
                  an audit record of privileged changes — who acted, what they did,
                  which object it affected, and when. This includes key and webhook
                  creation, rotation and revocation, store changes, changes to where
                  a store&apos;s money is sent, and payment reissues. Credentials are
                  never written into this record.
                </li>
              </ul>
            </section>

            <section className="legal-section">
              <h2>3. What we do not collect</h2>
              <ul>
                <li>
                  We do not collect or store card numbers, bank account numbers,
                  wallet credentials, PINs or one-time codes for you or your
                  customers.
                </li>
                <li>
                  We do not run advertising or sell personal data, and we do not
                  share it with anyone for their own marketing.
                </li>
                <li>
                  We do not build profiles of payers. We see a payment and whether
                  it was paid; we do not try to identify who paid it beyond whatever
                  the banking system itself reports.
                </li>
              </ul>
              <p>
                Note that any personal data you put into a payment&apos;s{" "}
                <code>metadata</code> field, or into your store&apos;s contact
                fields, is data you have chosen to give us. You are responsible for
                having a lawful basis for it.
              </p>
            </section>

            <section className="legal-section">
              <h2>4. How long we keep it</h2>
              <ul>
                <li>
                  <strong>Raw payment-rail responses: at most 90 days.</strong> A
                  daily sweep deletes them once a payment is older than that. This
                  is enforced by the software, not by intention — the sweep runs on
                  a schedule and on every restart of the service.
                </li>
                <li>
                  <strong>
                    Everything else: for as long as your account exists.
                  </strong>{" "}
                  Payment amounts, statuses, timestamps, the rail reference and the
                  payment code are the accounting record of money that really moved,
                  so they are retained rather than deleted.
                </li>
                <li>
                  <strong>On account closure:</strong> we will delete or anonymise
                  your account data on request, except where we must keep records to
                  meet a legal, tax or accounting obligation, or to resolve a
                  dispute.
                </li>
              </ul>
            </section>

            <section className="legal-section">
              <h2>5. Who else sees it</h2>
              <p>
                We share data only where it is necessary to run the service you
                asked for:
              </p>
              <ul>
                <li>
                  <strong>ABA PayWay</strong> — to mint a payment code and to check
                  whether it has been paid;
                </li>
                <li>
                  <strong>Bakong / the National Bank of Cambodia</strong> — to query
                  whether a transfer has settled;
                </li>
                <li>
                  <strong>Google</strong> — if you choose to sign in with Google;
                </li>
                <li>
                  <strong>Telegram</strong> — only if you enable store alerts, and
                  only to deliver the alert you configured;
                </li>
                <li>
                  <strong>Our hosting and database providers</strong> — who store
                  the data on our behalf under contract.
                </li>
              </ul>
              <p>
                We may also disclose data where the law requires it, or to establish
                or defend a legal claim.
              </p>
            </section>

            <section className="legal-section">
              <h2>6. Cookies</h2>
              <p>
                We set one cookie, <code>chmabapay_session</code>, to keep you
                signed in. It is HttpOnly, SameSite=Lax, and marked Secure over
                HTTPS. It contains a signed session token — not your password, and
                not any payment data. Signing out clears it.
              </p>
            </section>

            <section className="legal-section">
              <h2>7. Security</h2>
              <p>
                Passwords and API keys are stored only as one-way hashes. Sessions
                are signed and expire. Access to privileged platform operations is
                recorded in an audit trail. Payment codes are single-use and
                expire, and raw payment-rail responses containing session tokens
                are deleted on the schedule in section 4.
              </p>
              <p>
                No system is perfect. If you believe your account or a key has been
                compromised, contact us immediately so we can revoke it.
              </p>
            </section>

            <section className="legal-section">
              <h2>8. Your rights</h2>
              <p>
                You can ask us for a copy of the personal data we hold about you,
                ask us to correct it, or ask us to delete it. We will respond within
                a reasonable period. Some requests are limited by the retention
                obligations in section 4.
              </p>
            </section>

            <section className="legal-section">
              <h2>9. Changes</h2>
              <p>
                If we change how we handle personal data, we will update this page
                and increase the version number above.
              </p>
            </section>

            <div className="legal-contact">
              Data requests and privacy questions can be sent to{" "}
              <strong>privacy@chmaba.com</strong>. For support, use{" "}
              <strong>support@chmaba.com</strong>.
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

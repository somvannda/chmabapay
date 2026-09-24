import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Contact — ChmabaPay",
  description:
    "How to reach ChmabaPay about an integration, a live payment, legal terms or privacy.",
  // Same shape as /api/docs: without these the page inherits the landing card.
  alternates: { canonical: "/contact" },
  openGraph: {
    type: "article",
    title: "Contact — ChmabaPay",
    description:
      "Support, legal and privacy contact addresses for ChmabaPay, and what to include so we can trace a payment.",
    url: "/contact",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Contact — ChmabaPay",
    description:
      "Support, legal and privacy contact addresses for ChmabaPay, and what to include so we can trace a payment.",
    images: ["/og-image.png"],
  },
};

const CHANNELS = [
  {
    title: "Support",
    email: "support@chmaba.com",
    copy: "Integration questions, webhook deliveries, or anything about a specific live payment. Include the payment id or your own reference id and we can trace it.",
  },
  {
    title: "Legal",
    email: "legal@chmaba.com",
    copy: "Terms of Service, contracts and legal notices.",
  },
  {
    title: "Privacy",
    email: "privacy@chmaba.com",
    copy: "Data requests, retention questions, and anything about the personal data we hold.",
  },
] as const;

export default function ContactPage() {
  return (
    <main>
      <section className="landing-section">
        <div className="landing-shell">
          <p className="landing-section-eyebrow">Contact</p>
          <h1 className="landing-section-title">Talk to ChmabaPay.</h1>
          <p className="contact-lede">
            Pick the address that fits, and include your store or payment id if your question is
            about a specific payment — it is the fastest way to an answer.
          </p>

          <div className="contact-grid">
            {CHANNELS.map((channel) => (
              <div key={channel.email} className="contact-card">
                <div className="contact-card-title">{channel.title}</div>
                <a className="contact-card-link" href={`mailto:${channel.email}`}>
                  {channel.email}
                </a>
                <p className="contact-card-copy">{channel.copy}</p>
              </div>
            ))}
          </div>

          <p className="contact-note">
            Support runs through your dashboard — there is no phone line, no live chat and no
            public status page yet. <strong>Pro carries a 24-hour first-response
            target</strong>, counted in calendar hours from the moment a request is opened,
            weekends included. <strong>Free and Starter are best-effort email with no target
            at all</strong>: we answer as quickly as we can and we will not publish a number
            we do not hold ourselves to. That target is stated on the plan, here, and on the
            Support page of your dashboard, so the three cannot drift apart. The terms offer
            no service level agreement beyond it (see section 7, Availability); what we can
            promise is that a payment id is enough to trace what the rail told us and what we
            recorded.
          </p>

          <p className="contact-note">
            ChmabaPay never holds funds and cannot move money back out of a bank account, so we
            cannot process a refund on your behalf — refunds settle between the payer and your
            bank. What we can do is reverse the record so your reporting is correct.
          </p>

          <p className="contact-note">
            ChmabaPay is operated by Chmaba, whose registered address is #62, Street P-10D,
            Sangkat Veal Sbov, Khan Chbar Ampov, Phnom Penh, Cambodia.
          </p>
        </div>
      </section>
    </main>
  );
}

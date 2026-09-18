import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Contact — ChmabaPay",
  description:
    "How to reach ChmabaPay about an integration, a live payment, legal terms or privacy.",
};

const CHANNELS = [
  {
    title: "Support",
    email: "support@chmaba.com",
    copy: "Integration questions, webhook deliveries, or anything about a specific live payment. Include the payment id (pay_…) or your reference id and we can trace it.",
  },
  {
    title: "Legal",
    email: "legal@chmaba.com",
    copy: "The merchant agreement, terms of service, and contracts.",
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
            ChmabaPay never holds funds and cannot move money back out of a bank account, so we
            cannot process a refund on your behalf — refunds settle between the payer and your
            bank. What we can do is reverse the record so your reporting is correct.
          </p>
        </div>
      </section>
    </main>
  );
}

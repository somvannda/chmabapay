"use client";

type FaqItem = {
  q: string;
  a: string;
};

const faqs: FaqItem[] = [
  {
    q: "How do I create my first store and accept payments?",
    a: "Step 1: Go to API keys → + Create key and copy the live key (shown once). Step 2: Go to Stores → + New store and enter your ABA PayWay share link — the store becomes active as soon as the destination is attached. Step 3: Go to Payments → + New payment to generate a KHQR. Step 4: Open the QR and scan it with Bakong or ABA Pay to test the full flow.",
  },
  {
    q: "How do API keys work across my stores?",
    a: "Every API key is workspace-scoped: one key authenticates every store in your account. Pass a store id when creating a payment to choose the destination. Your plan sets how many active keys you can have (Free 1, Starter 3, Pro 10).",
  },
  {
    q: "How do webhook signing secrets work?",
    a: "When you create a webhook endpoint we return a whsec_ signing secret once. Use it on your server to verify the X-ChmabaPay-Signature header on every POST. Rotate the secret at any time from the endpoint row — the new secret is also returned once only.",
  },
  {
    q: "How do I switch plans?",
    a: "Go to Billing → Choose your plan and select Free, Starter or Pro. Upgrades take effect immediately; downgrades apply from your next billing date. Your stores, keys and webhooks stay intact either way.",
  },
  {
    q: "What does each plan include?",
    a: "Every plan includes the same features — hosted checkout, webhook signing and CSV reports export. The plans differ only in price, stores, payments per month and API keys: Free — 3,000 payments, 1 store, 1 key. Starter ($9.99/mo) — 15,000 payments, 5 stores, 3 keys. Pro ($59.99/mo) — 1,000,000 payments, 50 stores, 10 keys, plus priority support.",
  },
  {
    q: "What support is available?",
    a: "Email support@chmaba.com for any plan. Every plan includes CSV exports for your finance team, and Pro adds priority support with a faster response time.",
  },
];

export default function DashboardHelpPage() {
  return (
    <>
      <div className="dash-page-head">
        <div>
          <h1 className="dash-page-title">Help center</h1>
          <div className="dash-page-subtitle">
            Common questions about ChmabaPay
          </div>
        </div>
      </div>

      {faqs.map((f) => (
        <div key={f.q} className="dash-panel">
          <details>
            <summary>{f.q}</summary>
            <p>{f.a}</p>
          </details>
        </div>
      ))}
    </>
  );
}

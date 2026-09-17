import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { brandColors } from "@shared/theme";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  preload: true,
  variable: "--font-inter",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "ChmabaPay — Your Payments, In A Better Flow",
  description:
    "ChmabaPay brings checkouts, payment visibility, and cleaner operations into one calm workspace.",
  keywords: [
    "Khmer payment",
    "KHQR",
    "Bakong",
    "ABA",
    "PayWay",
    "Cambodia payment gateway",
    "ChmabaPay",
    "checkout platform",
  ],
  openGraph: {
    title: "ChmabaPay — Your Payments, In A Better Flow",
    description:
      "ChmabaPay brings checkouts, payment visibility, and cleaner operations into one calm workspace.",
    type: "website",
    siteName: "ChmabaPay",
  },
  twitter: {
    card: "summary_large_image",
    title: "ChmabaPay — Your Payments, In A Better Flow",
    description: "A calmer payment workspace for KHQR, Bakong, and ABA PayWay.",
  },
};

export const viewport: Viewport = {
  themeColor: brandColors.surfaceLight,
  width: "device-width",
  initialScale: 1,
};

const signInHref = "/auth/google/login";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body className="font-sans landing-english landing-body">
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var p=window.location.pathname;if(p&&(p.indexOf('/dashboard')===0||p.indexOf('/onboarding')===0)){document.body.classList.add('dash-hide-landing-chrome','dash-path-dashboard');}}catch(e){}})();",
          }}
        />
        <header className="landing-header">
          <nav className="landing-header-nav">
            <a href="/" className="landing-brandmark landing-brandmark-link">
              <span className="landing-brandmark-mark" aria-hidden="true">
                <span className="landing-brandmark-ring landing-brandmark-ring-primary" />
                <span className="landing-brandmark-ring landing-brandmark-ring-secondary" />
              </span>
              <span className="landing-brandmark-word">
                <span className="landing-brandmark-name">chmaba</span>
                <span className="landing-brandmark-pay">Pay</span>
              </span>
            </a>

            <div className="landing-header-links">
              <a className="landing-header-link" href="#product">
                Product
              </a>
              <a className="landing-header-link" href="#how-it-works">
                How it works
              </a>
              <a className="landing-header-link" href="#plans">
                Plans
              </a>
              <a className="landing-header-link" href="/api/docs">
                API
              </a>
              <a className="landing-header-link" href="#customers">
                Customers
              </a>
            </div>

            {/* Right Nav */}
            <div className="landing-header-actions">
              <a className="landing-header-signin" href={signInHref}>
                Sign in
              </a>
              <a href={signInHref} className="nav-cta-primary">
                Start free
              </a>
            </div>
          </nav>
        </header>

        {children}

        <footer className="landing-footer">
          <div className="landing-shell">
            <div className="landing-footer-row">
              <div className="landing-footer-brand">
                <a className="landing-brandmark landing-footer-brandmark" href="/" aria-label="ChmabaPay home">
                  <span className="landing-brandmark-mark" aria-hidden="true">
                    <span className="landing-brandmark-ring landing-brandmark-ring-primary" />
                    <span className="landing-brandmark-ring landing-brandmark-ring-secondary" />
                  </span>
                  <span className="landing-brandmark-name">chmaba</span>
                </a>
              </div>
              <span className="landing-footer-copy">© 2026 ChmabaPay Technologies. Built with love for better days.</span>
              <div className="landing-footer-links">
                <a className="landing-footer-link" href="#plans">
                  Pricing
                </a>
                <a className="landing-footer-link" href="/api/docs">
                  API Docs
                </a>
                <a className="landing-footer-link" href="/privacy">
                  Privacy
                </a>
                <a className="landing-footer-link" href="/terms">
                  Terms
                </a>
                <a className="landing-footer-link" href="/contact">
                  Contact
                </a>
              </div>
            </div>
          </div>
        </footer>
      </body>
    </html>
  );
}

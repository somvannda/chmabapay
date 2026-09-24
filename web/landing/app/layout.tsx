import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { brandColors } from "@shared/theme";
import { MobileNav } from "@/components/MobileNav";
import "./globals.css";

// The latin subsets are vendored in `app/fonts/` instead of coming from
// `next/font/google`. That helper downloads the files during `next build`, which
// makes the build itself — CI and the production image alike — fail whenever
// fonts.gstatic.com is unreachable, and it has: a CI run died mid-build fetching
// JetBrains Mono on 2026-09-22. Both files are the upstream variable fonts, so one
// file per family still covers every weight these apps use. See `fonts/OFL.txt`.
const inter = localFont({
  src: "./fonts/inter-latin.woff2",
  weight: "100 900",
  style: "normal",
  display: "swap",
  preload: true,
  variable: "--font-inter",
});

const jetbrains = localFont({
  src: "./fonts/jetbrains-mono-latin.woff2",
  weight: "100 800",
  style: "normal",
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  // Relative social images have to be resolved to an absolute URL while the page is
  // being prerendered, and that happens on a build machine with no request to read
  // an origin from. Without this Next falls back to `http://localhost:3000` and ships
  // `og:image: http://localhost:3000/og-image.png` to every crawler.
  metadataBase: new URL(process.env.NEXT_PUBLIC_SITE_URL ?? "https://pay.chmaba.com"),
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
  icons: {
    icon: [
      { url: "/favicon.svg", type: "image/svg+xml" },
      { url: "/favicon.png", type: "image/png", sizes: "512x512" },
    ],
    apple: [{ url: "/favicon.png", sizes: "512x512" }],
  },
  openGraph: {
    title: "ChmabaPay — Your Payments, In A Better Flow",
    description:
      "ChmabaPay brings checkouts, payment visibility, and cleaner operations into one calm workspace.",
    type: "website",
    siteName: "ChmabaPay",
    images: [{ url: "/og-image.png", width: 1024, height: 1024, alt: "ChmabaPay" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "ChmabaPay — Your Payments, In A Better Flow",
    description: "A calmer payment workspace for KHQR payments over ABA PayWay.",
    images: ["/og-image.png"],
  },
};

export const viewport: Viewport = {
  themeColor: brandColors.surfaceLight,
  // Emits `<meta name="color-scheme" content="only light">`, paired with the same
  // declaration in globals.css. `only light` is the opt-out from Chromium's automatic
  // dark theme; plain `light` is not enough, and the repaint it permits inverts the
  // violet brand colour to a green at paint time.
  colorScheme: "only light",
  width: "device-width",
  initialScale: 1,
};

const signInHref = "/auth/google/login";

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      {/* The script below adds `dash-*` classes to this element during parse, so the
          DOM React hydrates into already differs from the className rendered here.
          `suppressHydrationWarning` is the documented opt-out for exactly that — the
          difference is deliberate, and without it every dashboard route logs a
          hydration mismatch. It suppresses the warning for this element only, one
          level deep, so a real mismatch anywhere else still reports. */}
      <body
        className="font-sans landing-english landing-body"
        suppressHydrationWarning
      >
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{if(window.location.pathname.indexOf('/dashboard')===0){document.body.classList.add('dash-hide-landing-chrome','dash-path-dashboard');}}catch(e){}})();",
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
              <a className="landing-header-link" href="/#product">
                Product
              </a>
              <a className="landing-header-link" href="/#how-it-works">
                How it works
              </a>
              <a className="landing-header-link" href="/#plans">
                Plans
              </a>
              <a className="landing-header-link" href="/api/docs">
                API
              </a>
              <a className="landing-header-link" href="/#late-payments">
                Late payments
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
              <MobileNav />
            </div>
          </nav>
        </header>

        {children}

        <footer className="landing-footer">
          <div className="landing-shell">
            <div className="landing-footer-row">
              <div className="landing-footer-brand">
                <a className="landing-brandmark landing-brandmark-link" href="/" aria-label="ChmabaPay home">
                  <span className="landing-brandmark-mark" aria-hidden="true">
                    <span className="landing-brandmark-ring landing-brandmark-ring-primary" />
                    <span className="landing-brandmark-ring landing-brandmark-ring-secondary" />
                  </span>
                  <span className="landing-brandmark-word">
                    <span className="landing-brandmark-name">chmaba</span>
                    <span className="landing-brandmark-pay">Pay</span>
                  </span>
                </a>
              </div>
              <span className="landing-footer-copy">© 2026 Chmaba. Built with love for better days.</span>
              <div className="landing-footer-links">
                <a className="landing-footer-link" href="/#plans">
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

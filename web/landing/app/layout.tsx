import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { brandColors } from "@shared/theme";
import { ChmabaHeader, ChmabaFooter } from "@shared/chrome";
import { HeaderAccount } from "@/components/HeaderAccount";
import { ThemeToggle } from "@/components/ThemeToggle";
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
  // `light dark` (not `only light`): the site now ships the Chmaba `.dark` token
  // set and a toggle. The old `only light` was an opt-out from Chromium's
  // automatic dark mode; with an explicit theme we declare support instead, so the
  // browser does not invert our palette at paint time.
  colorScheme: "light dark",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrains.variable}`}
      suppressHydrationWarning
    >
      {/* The script below adds `dash-*` / `dark` classes to this element during
          parse, so the DOM React hydrates into already differs from the className
          rendered here. `suppressHydrationWarning` is the documented opt-out for
          exactly that — the difference is deliberate. */}
      <body
        className="font-sans landing-english landing-body"
        suppressHydrationWarning
      >
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('chmaba-theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d){document.documentElement.classList.add('dark');}}catch(e){}})();",
          }}
        />
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{if(window.location.pathname.indexOf('/dashboard')===0){document.body.classList.add('dash-hide-landing-chrome','dash-path-dashboard');}}catch(e){}})();",
          }}
        />
        {/* Chmaba's master chrome (packages/ui/chrome.jsx) — same header/footer
            style as chmaba.com, carrying ChmabaPay's own links. Wrapped in
            `.chmaba-site-chrome` so the dashboard can hide it. */}
        <div className="chmaba-site-chrome">
          <ChmabaHeader
            nav={[
              { label: "Product", href: "/#product" },
              { label: "How it works", href: "/#how-it-works" },
              { label: "Plans", href: "/#plans" },
              { label: "API", href: "/api/docs" },
              { label: "Late payments", href: "/#late-payments" },
            ]}
            actions={
              <>
                <ThemeToggle />
                <HeaderAccount />
              </>
            }
          />
        </div>

        {children}

        <div className="chmaba-site-chrome chmaba-site-foot">
          <ChmabaFooter
            links={[
              { label: "Pricing", href: "/#plans" },
              { label: "API Docs", href: "/api/docs" },
              { label: "Privacy", href: "/privacy" },
              { label: "Terms", href: "/terms" },
              { label: "Contact", href: "/contact" },
            ]}
          />
        </div>
      </body>
    </html>
  );
}

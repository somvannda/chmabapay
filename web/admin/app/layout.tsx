import type { Metadata, Viewport } from "next";
import localFont from "next/font/local";
import { AdminShell } from "@/components/AdminShell";
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
  title: "ChmabaPay Admin",
  description: "Platform administration for ChmabaPay.",
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: "#f7f7fa",
  // Paired with `color-scheme: only light` in globals.css. This emits the meta tag,
  // which the browser sees before the stylesheet loads. `only light` is load-bearing:
  // plain `light` merely declares a scheme and Chromium's auto-dark still ran, and it
  // repaints at paint time — which is how the violet primary button and the account
  // menu rendered olive green on Chrome while Firefox drew them correctly. The
  // console has no dark theme by design.
  colorScheme: "only light",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${jetbrains.variable}`}>
      <body>
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}

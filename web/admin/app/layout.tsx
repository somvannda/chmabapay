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
  // `light dark`: the console now ships the Chmaba `.dark` token set and a toggle.
  // The old `only light` was an opt-out from Chromium's automatic dark mode; with
  // an explicit theme we declare support instead.
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
      <body>
        <script
          dangerouslySetInnerHTML={{
            __html:
              "(function(){try{var t=localStorage.getItem('chmaba-theme');var d=t?t==='dark':window.matchMedia('(prefers-color-scheme: dark)').matches;if(d){document.documentElement.classList.add('dark');}}catch(e){}})();",
          }}
        />
        <AdminShell>{children}</AdminShell>
      </body>
    </html>
  );
}

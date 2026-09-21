import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { AdminShell } from "@/components/AdminShell";
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

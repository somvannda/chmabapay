import type { Metadata, Viewport } from "next";
import { Inter, Noto_Sans_Khmer, JetBrains_Mono } from "next/font/google";
import { headers } from "next/headers";
import "./globals.css";
import { ChmabaLayoutClient } from "../components/ChmabaLayoutClient";
import { ModalProvider } from "@shared/components/ModalSystem";
import { fetchMe } from "../lib/api";

const km = Noto_Sans_Khmer({
  subsets: ["khmer"],
  weight: ["400", "500", "600", "700"],
  display: "swap",
  preload: true,
  variable: "--font-khmer",
});

const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  display: "swap",
  preload: true,
  variable: "--font-inter",
});

const jbMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  display: "swap",
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "ChmabaPay - Accept Bakong KHQR & ABA Payments",
  description:
    "ChmabaPay payment gateway for Khmer merchants. Accept Bakong KHQR, ABA PayWay, and bank transfers. Sub-Merchant SaaS for Cambodian businesses.",
  icons: { icon: "/favicon.svg" },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FAFBFC" },
    { media: "(prefers-color-scheme: dark)", color: "#0B0B0E" },
  ],
  width: "device-width",
  initialScale: 1,
};

export default async function RootLayout({ children }: { children: React.ReactNode }) {
  const h = headers();
  const cookieHeader = h.get("cookie") ?? undefined;
  const traceHeader = h.get("X-ChmabaPay-Trace") ?? h.get("x-chmabapay-trace") ?? null;
  const me = await fetchMe(cookieHeader);

  const accountType = me?.account_type ?? "individual";
  const isPlatformAdmin = me?.is_platform_admin ?? false;

  return (
    <html
      lang="en"
      className={`${km.variable} ${inter.variable} ${jbMono.variable}`}
      style={{ scrollBehavior: "smooth" }}
    >
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
      </head>
      <body style={{ margin: 0 }}>
        <ModalProvider>
          <ChmabaLayoutClient
            accountType={accountType}
            isPlatformAdmin={isPlatformAdmin}
            fullName={me?.full_name ?? null}
            email={me?.email ?? ""}
            traceId={traceHeader}
          >
            {children}
          </ChmabaLayoutClient>
        </ModalProvider>
      </body>
    </html>
  );
}

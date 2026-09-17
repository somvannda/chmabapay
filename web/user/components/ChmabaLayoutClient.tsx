"use client";

import React, { useState, useEffect, useMemo } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { brandColors, radius, shadows, fonts } from "@shared/theme";
import { shouldRenderDarkToggle, type AccountType } from "@shared/ux-laws";

interface SidebarItem {
  key: string;
  href: string;
  labelKm: string;
  labelEn: string;
  icon: string;
  requireBusiness?: boolean;
}

const SIDEBAR: SidebarItem[] = [
  { key: "dashboard", href: "/dashboard", labelKm: "ផ្ទាំងគ្រប់គ្រង", labelEn: "Dashboard", icon: "D" },
  { key: "stores", href: "/stores", labelKm: "ហាង", labelEn: "Stores", icon: "S" },
  { key: "payments", href: "/payments", labelKm: "ការទូទាត់", labelEn: "Payments", icon: "P" },
  { key: "keys", href: "/keys", labelKm: "កូនសោ API", labelEn: "API Keys", icon: "K", requireBusiness: true },
  { key: "settings", href: "/settings", labelKm: "ការកំណត់", labelEn: "Settings", icon: "G" },
];

function DarkToggle({ isDark, setIsDark }: { isDark: boolean; setIsDark: (v: boolean) => void }) {
  return (
    <button
      type="button"
      onClick={() => setIsDark(!isDark)}
      aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "6px 10px",
        backgroundColor: isDark ? brandColors.dark.surfaceCard : brandColors.surfaceLight,
        border: `1px solid ${isDark ? brandColors.dark.border : "#E5E7EB"}`,
        borderRadius: `${radius.card}px`,
        cursor: "pointer",
        fontSize: "12px",
        fontWeight: 600,
        color: isDark ? brandColors.dark.textPrimary : brandColors.brandInk,
        transition: "all 0.15s ease",
      }}
    >
      <span>{isDark ? "\u{1F319}" : "\u2600\uFE0F"}</span>
      <span>{isDark ? "Dark" : "Light"}</span>
    </button>
  );
}

interface ChmabaLayoutClientProps {
  children: React.ReactNode;
  accountType: AccountType;
  isPlatformAdmin: boolean;
  fullName: string | null;
  email: string;
  traceId: string | null;
}

export const ChmabaLayoutClient: React.FC<ChmabaLayoutClientProps> = ({
  children,
  accountType,
  isPlatformAdmin,
  fullName,
  email,
  traceId,
}) => {
  const pathname = usePathname();
  const [isDark, setIsDark] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  const showDark = useMemo(
    () => shouldRenderDarkToggle(accountType, isPlatformAdmin),
    [accountType, isPlatformAdmin]
  );

  useEffect(() => {
    if (!showDark) return;
    const stored = window.localStorage.getItem("chmaba-theme");
    const initial = stored === "dark";
    setIsDark(initial);
    document.documentElement.classList.toggle("dark", initial);
  }, [showDark]);

  useEffect(() => {
    if (!showDark) return;
    document.documentElement.classList.toggle("dark", isDark);
    window.localStorage.setItem("chmaba-theme", isDark ? "dark" : "light");
  }, [isDark, showDark]);

  if (pathname === "/login") {
    return <>{children}</>;
  }

  const visibleItems = SIDEBAR.filter((i) => !i.requireBusiness || accountType === "business");

  const shellBg = isDark ? brandColors.dark.headerBg : brandColors.white;
  const sidebarBg = isDark ? brandColors.dark.surfaceCard : brandColors.white;
  const shellBorder = isDark ? brandColors.dark.border : "#F1F5F9";
  const navLabel = isDark ? brandColors.dark.navLink : brandColors.subtleText;
  const navActiveFg = brandColors.brandViolet;
  const navActiveBg = brandColors.brandViolet + "12";
  const headerBg = isDark ? brandColors.dark.headerBg : brandColors.surfaceLight;
  const bodyBg = isDark ? brandColors.dark.bodyBg : brandColors.surfaceLight;
  const titleFg = isDark ? brandColors.dark.textPrimary : brandColors.brandInk;

  return (
    <div
      style={{
        display: "flex",
        minHeight: "100vh",
        backgroundColor: bodyBg,
        fontFamily: fonts.sans,
      }}
    >
      <aside
        style={{
          width: "240px",
          flexShrink: 0,
          backgroundColor: sidebarBg,
          borderRight: `1px solid ${shellBorder}`,
          display: "flex",
          flexDirection: "column",
          padding: "20px 14px",
          gap: "20px",
          position: "sticky",
          top: 0,
          height: "100vh",
          overflowY: "auto",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "4px 6px" }}>
          <div
            style={{
              width: "36px",
              height: "36px",
              borderRadius: `${radius.lg}px`,
              backgroundColor: brandColors.brandViolet,
              color: brandColors.white,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontSize: "16px",
              boxShadow: shadows.primaryButton,
            }}
          >
            C
          </div>
          <div style={{ lineHeight: 1.1 }}>
            <div style={{ fontWeight: 800, fontSize: "16px", color: titleFg, letterSpacing: "-0.01em" }}>
              Chmaba
            </div>
            <div style={{ fontWeight: 600, fontSize: "10px", color: brandColors.brandViolet, letterSpacing: "0.08em", textTransform: "uppercase" }}>
              Pay Portal
            </div>
          </div>
        </div>

        <nav style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
          {visibleItems.map((item) => {
            const active = pathname === item.href || pathname?.startsWith(item.href + "/");
            return (
              <Link
                key={item.key}
                href={item.href}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "10px",
                  padding: "10px 12px",
                  borderRadius: `${radius.lg}px`,
                  backgroundColor: active ? navActiveBg : "transparent",
                  color: active ? navActiveFg : navLabel,
                  fontSize: "13px",
                  fontWeight: active ? 700 : 500,
                  textDecoration: "none",
                  transition: "all 0.12s ease",
                }}
                onMouseEnter={(e) => {
                  if (!active) {
                    (e.currentTarget as HTMLElement).style.backgroundColor = isDark ? brandColors.dark.bodyBg : "#F8FAFC";
                    (e.currentTarget as HTMLElement).style.color = titleFg;
                  }
                }}
                onMouseLeave={(e) => {
                  if (!active) {
                    (e.currentTarget as HTMLElement).style.backgroundColor = "transparent";
                    (e.currentTarget as HTMLElement).style.color = navLabel;
                  }
                }}
              >
                <span style={{
                  width: "22px", height: "22px", borderRadius: "6px",
                  backgroundColor: active ? brandColors.brandViolet : "transparent",
                  color: active ? brandColors.white : "inherit",
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  fontSize: "11px", fontWeight: 700,
                }}>
                  {item.icon}
                </span>
                <span>{item.labelEn}</span>
              </Link>
            );
          })}
        </nav>

        <div style={{ marginTop: "auto", display: "flex", flexDirection: "column", gap: "10px" }}>
          <div
            style={{
              padding: "12px",
              borderRadius: `${radius.card}px`,
              backgroundColor: isDark ? brandColors.dark.bodyBg : "#F8FAFC",
              border: `1px solid ${shellBorder}`,
            }}
          >
            <div style={{
              fontSize: "13px",
              fontWeight: 700,
              color: titleFg,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}>
              {fullName ?? email}
            </div>
            <div style={{ fontSize: "11px", color: navLabel, marginTop: "2px" }}>{email}</div>
            <div style={{
              marginTop: "8px",
              display: "inline-block",
              padding: "3px 8px",
              borderRadius: `${radius.pill}px`,
              fontSize: "10px",
              fontWeight: 700,
              letterSpacing: "0.05em",
              textTransform: "uppercase",
              backgroundColor: accountType === "business" ? brandColors.brandViolet : "#F0FDD3",
              color: accountType === "business" ? brandColors.white : "#365314",
            }}>
              {accountType}
            </div>
          </div>

          {showDark && <DarkToggle isDark={isDark} setIsDark={setIsDark} />}
        </div>
      </aside>

      <div style={{ flex: 1, display: "flex", flexDirection: "column", minWidth: 0 }}>
        <header
          style={{
            position: "sticky",
            top: 0,
            zIndex: 50,
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "12px 28px",
            backgroundColor: headerBg,
            borderBottom: `1px solid ${shellBorder}`,
            gap: "16px",
          }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: "16px" }}>
            <div style={{ fontSize: "18px", fontWeight: 800, color: titleFg, letterSpacing: "-0.01em" }}>
              ChmabaPay
            </div>
            {traceId && (
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: "6px",
                  padding: "4px 8px",
                  backgroundColor: isDark ? brandColors.dark.terminalBg : "#F1F5F9",
                  borderRadius: `${radius.sm}px`,
                  fontFamily: fonts.mono,
                  fontSize: "11px",
                  color: isDark ? brandColors.dark.navLink : brandColors.subtleText,
                }}
              >
                <span style={{ fontWeight: 700 }}>Trace:</span>
                <span>{traceId.slice(0, 12)}...</span>
              </div>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
            <form action="/api/auth/logout" method="POST">
              <button
                type="submit"
                style={{
                  padding: "6px 12px",
                  borderRadius: `${radius.lg}px`,
                  border: `1px solid ${shellBorder}`,
                  backgroundColor: "transparent",
                  color: navLabel,
                  fontSize: "12px",
                  fontWeight: 600,
                  cursor: "pointer",
                }}
              >
                Sign out
              </button>
            </form>
          </div>
        </header>

        <main style={{ padding: "28px", flex: 1, minWidth: 0 }}>{children}</main>
      </div>
    </div>
  );
};

export default ChmabaLayoutClient;

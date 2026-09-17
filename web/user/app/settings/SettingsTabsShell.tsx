"use client";

import React from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { brandColors, radius, shadows } from "@shared/theme";
import { showKhmerFirst } from "@shared/ux-laws";

const TABS = [
  { key: "profile", href: "/settings/profile", labelKm: "គណនីផ្ទាល់ខ្លួន", labelEn: "Profile", icon: "P", pageKey: "settings-profile" as const },
  { key: "billing", href: "/settings/billing", labelKm: "វិក័យប័ត្រ", labelEn: "Billing", icon: "B", pageKey: "settings-billing" as const },
];

interface SettingsTabsShellProps {
  children: React.ReactNode;
}

export const SettingsTabsShell: React.FC<SettingsTabsShellProps> = ({ children }) => {
  const pathname = usePathname();
  const kmFirst = showKhmerFirst("settings-profile");

  return (
    <div style={{ maxWidth: "960px", margin: "0 auto" }}>
      <div style={{ marginBottom: "24px" }}>
        <h1 style={{
          margin: 0,
          fontSize: "30px",
          fontWeight: 800,
          color: brandColors.brandInk,
          letterSpacing: "-0.02em",
        }}>
          {kmFirst ? "ការកំណត់គណនី" : "Account Settings"}
        </h1>
        {kmFirst && (
          <p style={{ marginTop: "4px", color: brandColors.textBody, fontSize: "13px" }}>
            Account Settings
          </p>
        )}
      </div>

      <div style={{
        display: "flex",
        gap: "4px",
        padding: "4px",
        marginBottom: "24px",
        backgroundColor: brandColors.surfaceCard,
        borderRadius: `${radius.card}px`,
        border: `1px solid rgba(15,23,42,0.05)`,
        boxShadow: shadows.card,
        flexWrap: "wrap",
      }}>
        {TABS.map((tab) => {
          const active = pathname === tab.href || pathname === "/settings" && tab.key === "profile";
          return (
            <Link
              key={tab.key}
              href={tab.href}
              style={{
                flex: 1,
                minWidth: "140px",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                gap: "8px",
                padding: "10px 14px",
                borderRadius: `${radius.lg}px`,
                backgroundColor: active ? brandColors.brandViolet : "transparent",
                color: active ? brandColors.white : brandColors.brandInk,
                fontSize: "13px",
                fontWeight: active ? 700 : 600,
                textDecoration: "none",
                transition: "all 0.15s ease",
              }}
            >
              <span style={{
                width: "18px", height: "18px",
                borderRadius: "5px",
                display: "inline-flex", alignItems: "center", justifyContent: "center",
                fontSize: "10px", fontWeight: 800,
                backgroundColor: active ? brandColors.white : brandColors.brandViolet + "18",
                color: active ? brandColors.brandViolet : brandColors.brandViolet,
              }}>
                {tab.icon}
              </span>
              <span>
                {kmFirst ? tab.labelKm : tab.labelEn}
                {kmFirst && (
                  <div style={{ fontSize: "10px", fontWeight: 500, opacity: 0.8, lineHeight: 1.1 }}>
                    {tab.labelEn}
                  </div>
                )}
              </span>
            </Link>
          );
        })}
      </div>

      {children}
    </div>
  );
};

export default SettingsTabsShell;

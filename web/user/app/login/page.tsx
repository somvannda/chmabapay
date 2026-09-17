"use client";

import React from "react";
import { brandColors, radius, shadows } from "@shared/theme";
import { showKhmerFirst } from "@shared/ux-laws";

export default function LoginPage() {
  const kmFirst = showKhmerFirst("login" as any);

  return (
    <div
      style={{
        minHeight: "100vh",
        backgroundColor: brandColors.surfaceLight,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "24px",
      }}
    >
      <div
        style={{
          width: "100%",
          maxWidth: "440px",
          backgroundColor: brandColors.white,
          borderRadius: `${radius.card}px`,
          boxShadow: shadows.card,
          padding: "36px 32px",
        }}
      >
        <div style={{ textAlign: "center", marginBottom: "28px" }}>
          <div style={{
            width: "56px", height: "56px",
            borderRadius: `${radius.lg}px`,
            backgroundColor: brandColors.brandViolet,
            color: brandColors.white,
            display: "inline-flex", alignItems: "center", justifyContent: "center",
            fontWeight: 800, fontSize: "24px",
            boxShadow: shadows.primaryButton,
            marginBottom: "16px",
          }}>
            C
          </div>
          <h1 style={{
            margin: 0,
            fontSize: "26px",
            fontWeight: 800,
            color: brandColors.brandInk,
            letterSpacing: "-0.02em",
          }}>
            {kmFirst ? "ចូលប្រើ ChmabaPay" : "Sign in to ChmabaPay"}
          </h1>
          {kmFirst && (
            <p style={{ marginTop: "6px", fontSize: "13px", color: brandColors.textBody }}>
              Sign in to ChmabaPay
            </p>
          )}
        </div>

        <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginBottom: "24px" }}>
          <a
            href="/api/auth/google/login"
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "10px",
              padding: "12px 20px",
              borderRadius: `${radius.lg}px`,
              border: `1px solid #DEDEE7`,
              backgroundColor: brandColors.white,
              color: brandColors.brandInk,
              fontWeight: 600,
              fontSize: "14px",
              textDecoration: "none",
              transition: "all 0.15s ease",
            }}
          >
            <span style={{ fontSize: "16px" }}>G</span>
            {kmFirst ? "បន្តជាមួយ Google" : "Continue with Google"}
          </a>

          <a
            href="/api/auth/_dev/login?email=sokha@example.com"
            style={{
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              gap: "10px",
              padding: "12px 20px",
              borderRadius: `${radius.lg}px`,
              backgroundColor: brandColors.brandViolet,
              color: brandColors.white,
              border: `1px solid rgba(255,255,255,0.08)`,
              fontWeight: 600,
              fontSize: "14px",
              textDecoration: "none",
              boxShadow: shadows.primaryButton,
              transition: "background-color 0.15s ease",
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLAnchorElement).style.backgroundColor = "#5A47E0";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLAnchorElement).style.backgroundColor = brandColors.brandViolet;
            }}
          >
            {kmFirst ? "ចូល (Dev Sokha)" : "Dev Login — sokha@example.com"}
          </a>
        </div>

        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: "10px",
            justifyContent: "center",
            padding: "14px",
            borderRadius: `${radius.lg}px`,
            backgroundColor: "#FAFBFC",
            border: `1px solid #F0F0F4`,
          }}
        >
          <span style={{
            width: "8px", height: "8px", borderRadius: "50%", backgroundColor: brandColors.trustAbaGreen,
          }} />
          <span style={{ fontSize: "11px", fontWeight: 600, color: brandColors.trustAbaGreen }}>
            Secured by ABA
          </span>
          <span style={{
            width: "1px", height: "12px", backgroundColor: "#E5E7EB",
          }} />
          <span style={{
            width: "8px", height: "8px", borderRadius: "50%", backgroundColor: brandColors.trustBakongTeal,
          }} />
          <span style={{ fontSize: "11px", fontWeight: 600, color: brandColors.trustBakongTeal }}>
            Bakong Open API
          </span>
        </div>

        <p style={{
          marginTop: "20px",
          fontSize: "11px",
          color: brandColors.subtleText,
          textAlign: "center",
          lineHeight: 1.6,
        }}>
          Licensed Bakong Open API Integration &middot; ABA PayWay Partner &middot;
          256-bit AES encrypted
        </p>
      </div>
    </div>
  );
}

"use client";

import React, { useState } from "react";
import { brandColors, radius, shadows } from "@shared/theme";
import { StatusBadge } from "@shared/components/StatusBadge";
import { fetchMe, type MeResponse } from "../../../lib/api";
import { showKhmerFirst } from "@shared/ux-laws";

interface ProfilePageClientProps {
  me: MeResponse | null;
}

export const ProfilePageClient: React.FC<ProfilePageClientProps> = ({ me }) => {
  const kmFirst = showKhmerFirst("settings-profile");
  const [fullName, setFullName] = useState(me?.full_name ?? "");
  const [email] = useState(me?.email ?? "");
  const [phone, setPhone] = useState("");
  const [saved, setSaved] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaved(true);
    try {
      await fetch("/api/v1/me", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        credentials: "include",
        body: JSON.stringify({ full_name: fullName, phone }),
      });
    } catch {
      // demo ok
    }
    setTimeout(() => setSaved(false), 2200);
  };

  return (
    <div style={{
      backgroundColor: brandColors.surfaceCard,
      borderRadius: `${radius.card}px`,
      boxShadow: shadows.card,
      padding: "28px 32px",
      border: `1px solid rgba(15,23,42,0.05)`,
    }}>
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: "16px",
        marginBottom: "24px",
        paddingBottom: "20px",
        borderBottom: `1px solid #F0F0F4`,
      }}>
        <div>
          <h2 style={{
            margin: 0, fontSize: "18px", fontWeight: 800, color: brandColors.brandInk,
          }}>
            {kmFirst ? "ព័ត៌មានផ្ទាល់ខ្លួន" : "Profile Information"}
          </h2>
          {kmFirst && (
            <div style={{ fontSize: "12px", color: brandColors.textBody, marginTop: "2px" }}>
              Profile Information
            </div>
          )}
        </div>
        <StatusBadge
          kind={me?.account_type === "business" ? "plan-growth" : "plan-starter"}
          label={me?.account_type.toUpperCase() ?? "INDIVIDUAL"}
        />
      </div>

      <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>
              {kmFirst ? "ឈ្មោះពេញ" : "Full name"}
            </label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              placeholder={kmFirst ? "ឈ្មោះអ្នកប្រើប្រាស់" : "Your full name"}
              style={{
                width: "100%",
                padding: "11px 14px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                fontSize: "14px",
                color: brandColors.brandInk,
                outline: "none",
              }}
            />
          </div>

          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>
              {kmFirst ? "អ៊ីមែល" : "Email"}
            </label>
            <input
              type="email"
              value={email}
              disabled
              style={{
                width: "100%",
                padding: "11px 14px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #E5E7EB`,
                fontSize: "14px",
                color: brandColors.subtleText,
                backgroundColor: "#FAFBFC",
                outline: "none",
                cursor: "not-allowed",
              }}
            />
          </div>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "16px" }}>
          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>
              {kmFirst ? "លេខទូរស័ព្ទ" : "Phone"}
            </label>
            <input
              type="tel"
              value={phone}
              onChange={(e) => setPhone(e.target.value)}
              placeholder="+855 96 123 4567"
              style={{
                width: "100%",
                padding: "11px 14px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                fontSize: "14px",
                color: brandColors.brandInk,
                outline: "none",
              }}
            />
          </div>

          <div>
            <label style={{
              display: "block", fontSize: "12px", fontWeight: 700,
              color: brandColors.brandInk, marginBottom: "6px",
            }}>
              {kmFirst ? "ភាសាផ្លូវការ" : "Default language"}
            </label>
            <select
              defaultValue={me?.account_type === "business" ? "en" : "km"}
              style={{
                width: "100%",
                padding: "11px 14px",
                borderRadius: `${radius.lg}px`,
                border: `1px solid #DEDEE7`,
                fontSize: "14px",
                color: brandColors.brandInk,
                outline: "none",
                backgroundColor: brandColors.white,
                cursor: "pointer",
              }}
            >
              <option value="km">&#x1F1F0;&#x1F1ED; Khmer &mdash; ខ្មែរ</option>
              <option value="en">&#x1F1EC;&#x1F1E7; English</option>
            </select>
          </div>
        </div>

        <div style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          marginTop: "8px",
          paddingTop: "20px",
          borderTop: `1px solid #F0F0F4`,
          gap: "12px",
        }}>
          <div style={{ fontSize: "12px", color: brandColors.textBody, lineHeight: 1.5 }}>
            {kmFirst
              ? "ព័ត៌មាននេះត្រូវបានប្រើដើម្បីផ្ទៀងផ្ទាត់គណនី និងផ្ញើការជូនដំណឹងសំខាន់ៗ។"
              : "This information is used for important account notifications."}
          </div>
          <button
            type="submit"
            style={{
              flexShrink: 0,
              padding: "11px 22px",
              borderRadius: `${radius.lg}px`,
              border: `1px solid rgba(255,255,255,0.08)`,
              backgroundColor: saved ? brandColors.statusPaid : brandColors.brandViolet,
              color: brandColors.white,
              fontWeight: 600,
              fontSize: "14px",
              cursor: saved ? "default" : "pointer",
              boxShadow: saved ? "none" : shadows.primaryButton,
              transition: "background-color 0.15s ease",
            }}
          >
            {saved ? "\u2713 Saved" : "Save changes"}
          </button>
        </div>
      </form>
    </div>
  );
};

export default ProfilePageClient;

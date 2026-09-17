import React from "react";
import { brandColors, radius } from "../theme";

export type StatusKind =
  | "paid"
  | "pending"
  | "scanned"
  | "failed"
  | "expired"
  | "plan-starter"
  | "plan-growth"
  | "plan-scale"
  | "plan-enterprise";

interface StatusBadgeProps {
  kind: StatusKind;
  label?: string;
  className?: string;
}

const styleMap: Record<StatusKind, { bg: string; fg: string; dot: string }> = {
  paid:            { bg: "#ECFDF5", fg: "#065F46", dot: brandColors.statusPaid },
  pending:         { bg: "#FFFBEB", fg: "#92400E", dot: brandColors.statusPending },
  scanned:         { bg: "#EFF6FF", fg: "#1E40AF", dot: brandColors.statusScanned },
  failed:          { bg: "#FEF2F2", fg: "#991B1B", dot: brandColors.statusFailed },
  expired:         { bg: "#F3F4F6", fg: "#374151", dot: brandColors.statusExpired },
  "plan-starter":  { bg: brandColors.brandViolet, fg: "#FFFFFF", dot: brandColors.brandViolet },
  "plan-growth":   { bg: "#EFF6FF", fg: "#1E40AF", dot: brandColors.statusScanned },
  "plan-scale":    { bg: "#CCFBF1", fg: "#134E4A", dot: brandColors.trustBakongTeal },
  "plan-enterprise": { bg: brandColors.goldVerified, fg: "#FFFFFF", dot: brandColors.goldVerified },
};

const labelMap: Record<StatusKind, string> = {
  paid: "PAID",
  pending: "PENDING",
  scanned: "SCANNED",
  failed: "FAILED",
  expired: "EXPIRED",
  "plan-starter": "Starter",
  "plan-growth": "Growth",
  "plan-scale": "Scale",
  "plan-enterprise": "Enterprise",
};

export const StatusBadge: React.FC<StatusBadgeProps> = ({ kind, label, className }) => {
  const style = styleMap[kind];
  const text = label ?? labelMap[kind];
  return (
    <span
      className={className}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "6px",
        padding: "6px 12px 6px 10px",
        borderRadius: `${radius.pill}px`,
        backgroundColor: style.bg,
        color: style.fg,
        fontSize: "12px",
        fontWeight: 500,
        lineHeight: 1,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          backgroundColor: style.dot,
          display: "inline-block",
        }}
      />
      {text}
    </span>
  );
};

export default StatusBadge;

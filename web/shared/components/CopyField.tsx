"use client";

import React, { useState } from "react";
import { brandColors, radius, fonts } from "../theme";

interface CopyFieldProps {
  value: string;
  label?: string;
  masked?: boolean;
  maskLength?: number;
  className?: string;
}

export const CopyField: React.FC<CopyFieldProps> = ({
  value,
  label,
  masked = false,
  maskLength = 8,
  className,
}) => {
  const [copied, setCopied] = useState(false);
  const [revealed, setRevealed] = useState(false);

  const display = masked && !revealed
    ? `${value.slice(0, maskLength)}${"•".repeat(Math.max(0, value.length - maskLength))}`
    : value;

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      setCopied(false);
    }
  };

  return (
    <div className={className} style={{ width: "100%" }}>
      {label && (
        <div style={{
          fontSize: "12px",
          fontWeight: 500,
          color: brandColors.subtleText,
          marginBottom: "6px",
          letterSpacing: "0.02em",
        }}>
          {label}
        </div>
      )}
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "8px",
        padding: "10px 12px",
        backgroundColor: brandColors.dark.terminalBg,
        borderRadius: `${radius.lg}px`,
        border: `1px solid ${brandColors.dark.border}`,
      }}>
        <code style={{
          flex: 1,
          fontFamily: fonts.mono,
          fontSize: "13px",
          color: "#E4E4E8",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}>
          {display}
        </code>
        {masked && (
          <button
            type="button"
            onClick={() => setRevealed(r => !r)}
            style={{
              background: "transparent",
              border: "none",
              color: brandColors.subtleTextDark,
              cursor: "pointer",
              fontSize: "12px",
              fontWeight: 500,
              padding: "4px 8px",
              borderRadius: `${radius.sm}px`,
            }}
          >
            {revealed ? "Hide" : "Reveal"}
          </button>
        )}
        <button
          type="button"
          onClick={handleCopy}
          style={{
            background: copied ? brandColors.brandLime : "transparent",
            border: `1px solid ${copied ? brandColors.brandLime : brandColors.dark.border}`,
            color: copied ? brandColors.brandInk : brandColors.subtleTextDark,
            cursor: "pointer",
            fontSize: "12px",
            fontWeight: 600,
            padding: "4px 10px",
            borderRadius: `${radius.sm}px`,
            transition: "all 0.15s ease",
          }}
        >
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
};

export default CopyField;

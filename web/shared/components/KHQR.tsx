import React from "react";
import { brandColors, radius, shadows } from "../theme";
import { bankBrandChips, type BankCode } from "../ux-laws";

export interface KHQRData {
  amount?: string;
  bakongId?: string;
  bankCode?: BankCode;
  currency?: "KHR" | "USD";
  merchantName?: string;
  amountDisplay?: string;
}

interface KHQRProps {
  data: KHQRData;
  /**
   * URL of an <img>-renderable encoding of this payment's KHQR payload — in
   * this platform, `/v1/khqr/render.svg?payload=<qr_string>`. Required for the
   * code to be scannable: the card used to synthesize a decorative hash pattern
   * from an arbitrary string, which looked like a QR and decoded as nothing.
   */
  qrSrc?: string;
  size?: number;
  showDetails?: boolean;
  className?: string;
}

export const KHQR: React.FC<KHQRProps> = ({
  data,
  qrSrc,
  size = 220,
  showDetails = true,
  className,
}) => {
  const chip = data.bankCode ? bankBrandChips(data.bankCode) : null;

  return (
    <div
      className={className}
      style={{
        backgroundColor: brandColors.surfaceCard,
        borderRadius: `${radius.card}px`,
        boxShadow: shadows.khqrCard,
        padding: "20px",
        display: "inline-flex",
        flexDirection: "column",
        alignItems: "center",
        gap: "14px",
        width: "fit-content",
        maxWidth: size + 60,
      }}
    >
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        padding: "4px 10px",
        backgroundColor: brandColors.trustBakongTeal + "18",
        borderRadius: `${radius.pill}px`,
      }}>
        <span style={{
          width: "8px", height: "8px", borderRadius: "50%", backgroundColor: brandColors.trustBakongTeal }} />
        <span style={{
          fontSize: "11px", fontWeight: 600, color: brandColors.trustBakongTeal, letterSpacing: "0.04em",
        }}>
          BAKONG KHQR
        </span>
      </div>

      <div style={{
        padding: "12px",
        backgroundColor: "#FFFFFF",
        borderRadius: `${radius.lg}px`,
        border: `1px solid #F0F0F4`,
      }}>
        {qrSrc ? (
          <img
            src={qrSrc}
            alt="KHQR code"
            width={size}
            height={size}
            style={{ display: "block", imageRendering: "pixelated" }}
          />
        ) : (
          <div
            style={{
              width: size,
              height: size,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              textAlign: "center",
              padding: "0 12px",
              fontSize: "12px",
              color: brandColors.subtleText,
              backgroundColor: "#FAFAFC",
              border: "1px dashed #DEDEE7",
              borderRadius: `${radius.lg}px`,
            }}
          >
            No QR payload
          </div>
        )}
      </div>

      {showDetails && (
        <div style={{ textAlign: "center", width: "100%" }}>
          {data.amountDisplay && (
            <div style={{
              fontSize: "26px",
              fontWeight: 800,
              color: brandColors.brandInk,
              lineHeight: 1.1,
              marginBottom: "4px",
            }}>
              {data.amountDisplay}
            </div>
          )}
          {data.merchantName && (
            <div style={{
              fontSize: "13px",
              color: brandColors.textBody,
              fontWeight: 500,
              marginBottom: "6px",
            }}>
              {data.merchantName}
            </div>
          )}
          {chip && (
            <div style={{
              display: "inline-flex",
              alignItems: "center",
              gap: "6px",
              padding: "4px 10px",
              borderRadius: `${radius.pill}px`,
              backgroundColor: chip.color + "18",
              fontSize: "11px",
              fontWeight: 600,
              color: chip.color,
            }}>
              <span style={{ width: "6px", height: "6px", borderRadius: "50%", backgroundColor: chip.color }} />
              {chip.label}
            </div>
          )}
          {data.bakongId && (
            <div style={{
              fontFamily: "'JetBrains Mono', monospace",
              fontSize: "11px",
              color: brandColors.subtleText,
              marginTop: "6px",
              wordBreak: "break-all",
            }}>
              ID: {data.bakongId}
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default KHQR;

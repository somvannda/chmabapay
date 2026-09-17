import React from "react";
import { brandColors, radius } from "../theme";
import { showKhmerFirst, type TrustPageKey, type TechPageKey } from "../ux-laws";

interface StepProgressProps {
  steps: number;
  current: number;
  pageKey: TrustPageKey | TechPageKey;
  className?: string;
}

const STEP_LABELS_KM = ["ព័ត៌មានអាជីវកម្ម", "ជម្រើសការទូទាត់", "រួចរាល់"];
const STEP_LABELS_EN = ["Business Info", "Payment Setup", "Done"];

export const StepProgress: React.FC<StepProgressProps> = ({
  steps,
  current,
  pageKey,
  className,
}) => {
  const kmFirst = showKhmerFirst(pageKey);

  return (
    <div
      className={className}
      style={{
        width: "100%",
        padding: "24px 0 8px 0",
      }}
    >
      <div style={{
        display: "flex",
        alignItems: "flex-start",
        justifyContent: "space-between",
        position: "relative",
        width: "100%",
        maxWidth: "600px",
        margin: "0 auto",
      }}>
        <div
          aria-hidden
          style={{
            position: "absolute",
            top: "18px",
            left: "24px",
            right: "24px",
            height: "2px",
            backgroundColor: "#E5E7EB",
            zIndex: 0,
          }}
        />
        <div
          aria-hidden
          style={{
            position: "absolute",
            top: "18px",
            left: "24px",
            width: `calc(${(Math.max(0, current - 1) / Math.max(1, steps - 1)) * 100}% * (100% - 48px) / 100%)`,
            maxWidth: "calc(100% - 48px)",
            height: "2px",
            backgroundColor: brandColors.brandViolet,
            zIndex: 1,
            transition: "width 0.3s ease",
          }}
        />
        {Array.from({ length: steps }).map((_, i) => {
          const stepNum = i + 1;
          const isDone = stepNum < current;
          const isActive = stepNum === current;
          const kmLabel = STEP_LABELS_KM[i] ?? `ជំហាន ${stepNum}`;
          const enLabel = STEP_LABELS_EN[i] ?? `Step ${stepNum}`;

          return (
            <div
              key={i}
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: "8px",
                zIndex: 2,
                flex: 1,
                textAlign: "center",
              }}
            >
              <div
                style={{
                  width: "36px",
                  height: "36px",
                  borderRadius: "50%",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  fontWeight: 700,
                  fontSize: "14px",
                  backgroundColor: isDone
                    ? brandColors.statusPaid
                    : isActive
                    ? brandColors.brandViolet
                    : "#FFFFFF",
                  color: isDone || isActive ? "#FFFFFF" : brandColors.subtleText,
                  border: `2px solid ${
                    isDone
                      ? brandColors.statusPaid
                      : isActive
                      ? brandColors.brandViolet
                      : "#E5E7EB"
                  }`,
                  transition: "all 0.2s ease",
                }}
              >
                {isDone ? "\u2713" : stepNum}
              </div>
              <div style={{ lineHeight: 1.3 }}>
                {kmFirst ? (
                  <>
                    <div style={{
                      fontWeight: 700,
                      fontSize: "13px",
                      color: isActive ? brandColors.brandViolet : brandColors.brandInk,
                    }}>
                      {kmLabel}
                    </div>
                    <div style={{
                      fontWeight: 400,
                      fontSize: "11px",
                      color: brandColors.textBody,
                      marginTop: "2px",
                    }}>
                      {enLabel}
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{
                      fontWeight: 700,
                      fontSize: "13px",
                      color: isActive ? brandColors.brandViolet : brandColors.brandInk,
                    }}>
                      {enLabel}
                    </div>
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{
        textAlign: "center",
        marginTop: "12px",
        fontSize: "12px",
        fontWeight: 500,
        color: brandColors.subtleText,
      }}>
        {kmFirst
          ? `ជំហាន ${current} នៃ ${steps} · Step ${current} of ${steps}`
          : `Step ${current} of ${steps}`}
      </div>
    </div>
  );
};

export default StepProgress;

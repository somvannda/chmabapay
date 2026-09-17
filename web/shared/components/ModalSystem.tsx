"use client";

import React, { createContext, useContext, useState, useCallback, useEffect } from "react";
import { brandColors, radius, shadows } from "../theme";

interface ModalConfig {
  id: string;
  title: string;
  content: React.ReactNode;
  onConfirm?: () => void | Promise<void>;
  onCancel?: () => void;
  confirmText?: string;
  cancelText?: string;
  variant?: "default" | "danger" | "trust-aba";
  showFooter?: boolean;
  width?: number;
}

interface ModalContextValue {
  open: (config: ModalConfig) => void;
  close: (id?: string) => void;
  closeAll: () => void;
}

const ModalContext = createContext<ModalContextValue | null>(null);

export const useModal = () => {
  const ctx = useContext(ModalContext);
  if (!ctx) throw new Error("useModal must be used inside ModalProvider");
  return ctx;
};

interface ModalProviderProps {
  children: React.ReactNode;
}

const variantStyles: Record<NonNullable<ModalConfig["variant"]>, { bg: string; hover: string; shadow: string }> = {
  default: {
    bg: brandColors.brandViolet,
    hover: "#5A47E0",
    shadow: shadows.primaryButton,
  },
  danger: {
    bg: brandColors.statusFailed,
    hover: "#DC2626",
    shadow: "0 7px 16px rgba(239,68,68,0.20)",
  },
  "trust-aba": {
    bg: brandColors.trustAbaGreen,
    hover: "#007A2E",
    shadow: "0 7px 16px rgba(0, 150, 57, 0.18)",
  },
};

export const ModalProvider: React.FC<ModalProviderProps> = ({ children }) => {
  const [stack, setStack] = useState<ModalConfig[]>([]);
  const [confirming, setConfirming] = useState(false);

  const open = useCallback((config: ModalConfig) => {
    setStack((prev) => [...prev, config]);
  }, []);

  const close = useCallback((id?: string) => {
    setStack((prev) => {
      if (!id) return prev.slice(0, -1);
      return prev.filter((m) => m.id !== id);
    });
  }, []);

  const closeAll = useCallback(() => setStack([]), []);

  const top = stack[stack.length - 1];

  const handleConfirm = async () => {
    if (!top) return;
    try {
      setConfirming(true);
      await top.onConfirm?.();
      close(top.id);
    } finally {
      setConfirming(false);
    }
  };

  return (
    <ModalContext.Provider value={{ open, close, closeAll }}>
      {children}
      {top && (
        <div
          aria-modal="true"
          role="dialog"
          style={{
            position: "fixed",
            inset: 0,
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: "16px",
          }}
        >
          <div
            onClick={() => close(top.id)}
            style={{
              position: "absolute",
              inset: 0,
              backgroundColor: "rgba(15, 23, 42, 0.50)",
              backdropFilter: "blur(2px)",
            }}
          />
          <div
            style={{
              position: "relative",
            zIndex: 1,
            backgroundColor: brandColors.surfaceCard,
            borderRadius: `${radius.card}px`,
            boxShadow: shadows.card,
            width: "100%",
            maxWidth: top.width ?? 480,
            maxHeight: "90vh",
            overflow: "auto",
            padding: "24px",
          }}>
            <div style={{
              display: "flex",
              alignItems: "flex-start",
              justifyContent: "space-between",
              marginBottom: "16px",
              gap: "16px",
            }}>
              <h2 style={{
                margin: 0,
                fontSize: "18px",
                fontWeight: 700,
                color: brandColors.brandInk,
              }}>
                {top.title}
              </h2>
              <button
                type="button"
                onClick={() => close(top.id)}
                style={{
                  background: "transparent",
                  border: "none",
                  fontSize: "20px",
                  lineHeight: 1,
                  cursor: "pointer",
                  color: brandColors.subtleText,
                  padding: "4px",
                  borderRadius: `${radius.sm}px`,
                }}
                aria-label="Close"
              >
                &times;
              </button>
            </div>
            <div style={{ fontSize: "14px", color: brandColors.textBody, lineHeight: 1.6 }}>
              {top.content}
            </div>
            {(top.showFooter !== false) && (
              <div style={{
                display: "flex",
                justifyContent: "flex-end",
                gap: "10px",
                marginTop: "24px",
              }}>
                <button
                  type="button"
                  onClick={() => { top.onCancel?.(); close(top.id); }}
                  style={{
                    padding: "10px 18px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid #DEDEE7`,
                    backgroundColor: brandColors.white,
                    color: brandColors.brandInk,
                    fontWeight: 600,
                    fontSize: "14px",
                    cursor: "pointer",
                  }}
                >
                  {top.cancelText ?? "Cancel"}
                </button>
                <button
                  type="button"
                  onClick={handleConfirm}
                  disabled={confirming}
                  style={{
                    padding: "10px 18px",
                    borderRadius: `${radius.lg}px`,
                    border: `1px solid rgba(255,255,255,0.08)`,
                    backgroundColor: variantStyles[top.variant ?? "default"].bg,
                    color: brandColors.white,
                    fontWeight: 600,
                    fontSize: "14px",
                    cursor: confirming ? "progress" : "pointer",
                    boxShadow: variantStyles[top.variant ?? "default"].shadow,
                    transition: "background-color 0.15s ease",
                  }}
                  onMouseEnter={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.backgroundColor =
                      variantStyles[top.variant ?? "default"].hover;
                  }}
                  onMouseLeave={(e) => {
                    (e.currentTarget as HTMLButtonElement).style.backgroundColor =
                      variantStyles[top.variant ?? "default"].bg;
                  }}
                >
                  {confirming ? "..." : (top.confirmText ?? "Confirm")}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </ModalContext.Provider>
  );
};

export default ModalProvider;

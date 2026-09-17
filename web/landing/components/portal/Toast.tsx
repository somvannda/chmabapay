"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

type ToastTone = "success" | "error";

type ToastState = { id: number; message: string; tone: ToastTone } | null;

type ToastApi = {
  notify: (message: string, tone?: ToastTone) => void;
};

const ToastContext = createContext<ToastApi>({ notify: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

const ICONS: Record<ToastTone, React.ReactNode> = {
  success: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="m8.5 12.5 2.5 2.5 4.5-5.5" />
    </>
  ),
  error: (
    <>
      <path d="M12 4.5 3 19.5h18L12 4.5Z" />
      <path d="M12 10.5v4" />
      <path d="M12 17.5h.01" />
    </>
  ),
};

/**
 * Toast host for the dashboard, styled after chmaba.com's notify(): a dark
 * bottom-right badge that clears itself. Errors linger longer than successes.
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toast, setToast] = useState<ToastState>(null);
  const timer = useRef<number | null>(null);

  const notify = useCallback((message: string, tone: ToastTone = "success") => {
    setToast({ id: Date.now(), message, tone });
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(
      () => setToast(null),
      tone === "error" ? 5000 : 2600,
    );
  }, []);

  useEffect(() => {
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, []);

  return (
    <ToastContext.Provider value={{ notify }}>
      {children}
      {toast && (
        <div
          className={`cp-toast cp-toast-${toast.tone}`}
          role="status"
          aria-live="polite"
          key={toast.id}
        >
          <svg
            className="cp-toast-icon"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="1.8"
            strokeLinecap="round"
            strokeLinejoin="round"
            aria-hidden="true"
          >
            {ICONS[toast.tone]}
          </svg>
          <span className="cp-toast-text">{toast.message}</span>
        </div>
      )}
    </ToastContext.Provider>
  );
}

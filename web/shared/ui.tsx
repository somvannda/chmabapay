"use client";

import { useEffect, useRef, useState } from "react";

/**
 * ChmabaPay UI primitives — a port of `Chmaba/apps/web/src/components/ui.jsx`
 * (Button, IconButton, Badge, Field, Modal, Dropdown, ThemeToggle, Logo).
 *
 * The class strings are copied verbatim so the rendered look matches Chmaba
 * exactly; the only changes are removing app coupling (no `api`, `dateFormat`),
 * swapping `lucide-react`/`@headlessui` for inline SVG + local state so no new
 * dependency is required, and adding TypeScript types.
 */

/* ------------------------------------------------------------------ Icons */

type IconProps = { size?: number; className?: string };

const svg = (path: React.ReactNode, { size = 16, className = "" }: IconProps) => (
  <svg
    viewBox="0 0 24 24"
    width={size}
    height={size}
    className={className}
    fill="none"
    stroke="currentColor"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    {path}
  </svg>
);

export const IconCheck = (p: IconProps) => svg(<path d="M20 6 9 17l-5-5" />, p);
export const IconChevronDown = (p: IconProps) => svg(<path d="m6 9 6 6 6-6" />, p);
export const IconX = (p: IconProps) => svg(<path d="M18 6 6 18M6 6l12 12" />, p);
export const IconSun = (p: IconProps) =>
  svg(
    <>
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
    </>,
    p,
  );
export const IconMoon = (p: IconProps) => svg(<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />, p);
export const IconEye = (p: IconProps) =>
  svg(
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </>,
    p,
  );
export const IconEyeOff = (p: IconProps) =>
  svg(
    <>
      <path d="M9.9 4.2A10.9 10.9 0 0 1 12 4c6.5 0 10 7 10 7a17 17 0 0 1-3.2 4.3M6.6 6.6A17 17 0 0 0 2 11s3.5 7 10 7a10.9 10.9 0 0 0 4.1-.8" />
      <path d="M14.1 14.1a3 3 0 1 1-4.2-4.2M2 2l20 20" />
    </>,
    p,
  );

/* ---------------------------------------------------------------- Button */

type ButtonVariant =
  | "primary"
  | "dark"
  | "soft"
  | "outline"
  | "outline-dark"
  | "ghost"
  | "danger"
  | "lime";
type ButtonSize = "xs" | "sm" | "md" | "lg" | "icon";

const BUTTON_VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-[#6957f5] text-white shadow-[0_7px_16px_rgba(105,87,245,.2)] hover:bg-[#5845e7]",
  dark: "bg-[#17181c] text-white hover:bg-[#2d2e34]",
  soft: "bg-[#f0efff] text-[#5b4be3] hover:bg-[#e7e4ff]",
  outline: "border border-[#dedee7] bg-white text-[#282930] hover:border-[#bdbbc9] hover:bg-[#fafafd]",
  "outline-dark": "border border-[#4a4b51] bg-transparent text-white hover:border-[#5c5d66] hover:bg-[#303137]",
  ghost: "text-[#696a74] hover:bg-[#f2f2f6] hover:text-[#24252b]",
  danger: "bg-[#fff0ee] text-[#d0574b] hover:bg-[#ffe5e2]",
  lime: "bg-[#c4f27c] text-[#1b2715] hover:bg-[#b8ea6d]",
};

const BUTTON_SIZES: Record<ButtonSize, string> = {
  xs: "h-8 rounded-lg px-2.5 text-xs",
  sm: "h-9 rounded-lg px-3 text-xs",
  md: "h-10 rounded-xl px-4 text-sm",
  lg: "h-12 rounded-xl px-5 text-sm",
  icon: "h-9 w-9 rounded-lg p-0",
};

export function Button({
  children,
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
}) {
  return (
    <button
      className={`inline-flex items-center justify-center gap-2 font-semibold transition-all duration-200 disabled:cursor-not-allowed disabled:opacity-50 ${BUTTON_VARIANTS[variant]} ${BUTTON_SIZES[size]} ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

export function IconButton({
  label,
  children,
  className = "",
  ...props
}: React.ButtonHTMLAttributes<HTMLButtonElement> & { label: string }) {
  return (
    <button
      aria-label={label}
      title={label}
      className={`inline-flex h-9 w-9 items-center justify-center rounded-lg text-[#70717a] transition hover:bg-[#f0eff5] hover:text-[#272831] disabled:cursor-not-allowed disabled:opacity-40 disabled:hover:bg-transparent disabled:hover:text-[#70717a] ${className}`}
      {...props}
    >
      {children}
    </button>
  );
}

/* ----------------------------------------------------------------- Badge */

type BadgeTone = "neutral" | "green" | "yellow" | "red" | "violet" | "blue";

const BADGE_TONES: Record<BadgeTone, string> = {
  neutral: "bg-[#f1f1f5] text-[#686974]",
  green: "bg-[#edf9e4] text-[#4f8b32]",
  yellow: "bg-[#fff6df] text-[#ad7d1c]",
  red: "bg-[#fff0ee] text-[#c2564b]",
  violet: "bg-[#f0efff] text-[#6555df]",
  blue: "bg-[#eaf4ff] text-[#3579b8]",
};

const BADGE_DOTS: Record<BadgeTone, string> = {
  green: "bg-[#77bb4b]",
  yellow: "bg-[#dca93c]",
  red: "bg-[#dc6b60]",
  violet: "bg-[#7969ec]",
  blue: "bg-[#63a2d8]",
  neutral: "bg-[#9899a4]",
};

export function Badge({
  children,
  tone = "neutral",
  dot = false,
}: {
  children: React.ReactNode;
  tone?: BadgeTone;
  dot?: boolean;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-semibold ${BADGE_TONES[tone]}`}
    >
      {dot && <span className={`h-1.5 w-1.5 rounded-full ${BADGE_DOTS[tone]}`} />}
      {children}
    </span>
  );
}

/* ----------------------------------------------------------------- Field */

export function Field({
  label,
  hint,
  type = "text",
  ...props
}: React.InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  const isPassword = type === "password";
  const [showPassword, setShowPassword] = useState(false);
  const input = (
    <input
      type={isPassword && showPassword ? "text" : type}
      className={`h-11 w-full rounded-xl border border-[#dfdfe8] bg-white px-3.5 text-sm text-[#22232a] outline-none transition placeholder:text-[#aaabb4] focus:border-[#887bf3] focus:ring-4 focus:ring-[#6957f5]/10 dark:border-[#363740] dark:bg-[#1f2025] dark:text-[#e4e4e8]${isPassword ? " pr-10" : ""}`}
      {...props}
    />
  );
  return (
    <label className="block">
      <span className="mb-1.5 block text-xs font-semibold text-[#4f5059] dark:text-[#9a9aa4]">{label}</span>
      {isPassword ? (
        <div className="relative">
          {input}
          <button
            type="button"
            aria-label={showPassword ? "Hide password" : "Show password"}
            onClick={() => setShowPassword((visible) => !visible)}
            className="absolute right-2.5 top-2.5 flex h-6 w-6 items-center justify-center rounded-md text-[#b0b1ba] transition hover:bg-[#f2f2f6] hover:text-[#6957f5]"
          >
            {showPassword ? <IconEyeOff size={15} /> : <IconEye size={15} />}
          </button>
        </div>
      ) : (
        input
      )}
      {hint && <span className="mt-1.5 block text-[11px] text-[#92939d]">{hint}</span>}
    </label>
  );
}

/* ----------------------------------------------------------------- Modal */

export function Modal({
  open,
  title,
  description,
  onClose,
  children,
  width = "max-w-lg",
  dismissOnBackdrop = true,
}: {
  open: boolean;
  title: string;
  description?: string;
  onClose: () => void;
  children: React.ReactNode;
  width?: string;
  dismissOnBackdrop?: boolean;
}) {
  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-[#17181c]/45 p-4 backdrop-blur-[3px]"
      onMouseDown={(event) => {
        if (dismissOnBackdrop && event.target === event.currentTarget) onClose();
      }}
    >
      <div className={`max-h-[92vh] w-full overflow-y-auto rounded-2xl bg-white shadow-[0_24px_80px_rgba(20,21,28,.22)] dark:bg-[#1a1b1f] ${width}`}>
        <div className="flex items-start justify-between border-b border-[#eeeeF2] px-5 py-4 dark:border-[#2a2b30]">
          <div>
            <h2 className="text-base font-bold text-[#202128] dark:text-[#e4e4e8]">{title}</h2>
            {description && <p className="mt-1 text-xs text-[#898a95]">{description}</p>}
          </div>
          <IconButton label="Close" onClick={onClose}>
            <IconX size={17} />
          </IconButton>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- Tooltip */

export function Tooltip({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <span title={label} className="inline-flex">
      {children}
    </span>
  );
}

/* -------------------------------------------------------------- Dropdown */

export type DropdownOption = { value: string | number; label: string; disabled?: boolean };

export const SELECT_TRIGGER =
  "h-11 w-full rounded-xl border border-[#dfdfe8] bg-white px-3.5 text-sm text-[#292a31] dark:border-[#363740] dark:bg-[#1f2025] dark:text-[#e4e4e8]";

export function Dropdown({
  value,
  onChange,
  options = [],
  placeholder = "Select",
  disabled = false,
  triggerClass = SELECT_TRIGGER,
  align = "left",
}: {
  value: string | number;
  onChange: (value: string | number) => void;
  options?: DropdownOption[];
  placeholder?: string;
  disabled?: boolean;
  triggerClass?: string;
  align?: "left" | "right";
}) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return undefined;
    const dismiss = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", dismiss);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("mousedown", dismiss);
      document.removeEventListener("keydown", escape);
    };
  }, [open]);
  const current = options.find((o) => o.value === value);
  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((v) => !v)}
        className={`flex min-w-0 items-center justify-between gap-2 outline-none transition ${disabled ? "cursor-not-allowed opacity-50" : ""} ${triggerClass}`}
      >
        <span className={`${current ? "" : "opacity-60"} truncate`}>{current ? current.label : placeholder}</span>
        <IconChevronDown size={13} className={`shrink-0 text-[#92939d] transition-transform duration-150 ${open ? "rotate-180" : ""}`} />
      </button>
      {open ? (
        <div
          className={`absolute ${align === "right" ? "right-0" : "left-0"} top-full z-40 mt-1.5 max-h-64 w-max min-w-[180px] max-w-[min(92vw,340px)] overflow-auto rounded-xl border border-[#e9e9ef] bg-white p-1.5 shadow-[0_18px_40px_rgba(28,31,42,.12)] dark:border-[#2a2b30] dark:bg-[#17181c]`}
        >
          {options.map((option) => {
            const selected = option.value === value;
            return (
              <button
                key={String(option.value)}
                type="button"
                disabled={option.disabled}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
                className="flex w-full cursor-pointer items-center justify-between gap-3 rounded-lg px-2.5 py-2 text-left text-sm text-[#4f5059] transition hover:bg-[#f4f4f9] hover:text-[#17181c] dark:text-[#9a9aa4] dark:hover:bg-[#22232a] dark:hover:text-white"
              >
                <span className="min-w-0 truncate">{option.label}</span>
                {selected && <IconCheck size={13} className="shrink-0 text-[#6957f5]" />}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

/* ----------------------------------------------------------- ThemeToggle */

export const THEME_STORAGE_KEY = "chmaba-theme";

export function ThemeToggle() {
  const [dark, setDark] = useState(false);
  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);
  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, next ? "dark" : "light");
    } catch {
      // storage blocked
    }
    setDark(next);
  }
  return (
    <button
      type="button"
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
      onClick={toggle}
      className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[#70717a] transition hover:bg-[#f0f0f5] hover:text-[#272831] dark:text-[#9a9aa4] dark:hover:bg-[#2a2b32] dark:hover:text-[#e4e4e8]"
    >
      {dark ? <IconSun size={17} /> : <IconMoon size={17} />}
    </button>
  );
}

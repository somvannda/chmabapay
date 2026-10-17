"use client";

import { useEffect, useRef, useState } from "react";

/**
 * Chmaba marketing chrome — a straight port of
 * `Chmaba/packages/ui/chrome.jsx` (ChmabaLogo / ChmabaHeader / ChmabaFooter).
 *
 * It depends on nothing but React and Tailwind classes and takes everything
 * host-specific as props, which is why it ports cleanly: the palette utilities it
 * uses (`bg-brand`, `text-ink-soft`, …) come from our shared preset, and the
 * `dark:` variants light up because the portals run `darkMode: "class"`.
 */

const NAV_LINK =
  "transition hover:text-[#17181c] dark:text-[#9a9aa4] dark:hover:text-[#e4e4e8]";

export function ChmabaLogo({ light = false }: { light?: boolean }) {
  const chip = light ? "bg-[#c4f27c]" : "bg-[#17181c] dark:bg-[#c4f27c]";
  const ring = light ? "border-[#17181c]" : "border-white dark:border-[#17181c]";
  const word = light ? "text-white" : "text-[#17181c] dark:text-white";
  return (
    <div className="flex items-center gap-2.5">
      <div
        className={`relative flex h-8 w-8 items-center justify-center overflow-hidden rounded-[10px] ${chip}`}
      >
        <span className={`absolute h-3 w-3 rounded-full border-[2.5px] ${ring}`} />
        <span className={`absolute -right-0.5 top-1.5 h-3 w-3 rounded-full border-[2.5px] ${ring}`} />
      </div>
      <span className={`text-[19px] font-extrabold tracking-[-.04em] ${word}`}>chmaba</span>
    </div>
  );
}

function HeaderAction({
  primary = false,
  href,
  onClick,
  children,
}: {
  primary?: boolean;
  href?: string;
  onClick?: () => void;
  children: React.ReactNode;
}) {
  const base =
    "inline-flex items-center justify-center gap-2 rounded-lg font-semibold transition-all duration-200";
  const look = primary
    ? "h-9 bg-[#6957f5] px-3 text-xs text-white shadow-[0_7px_16px_rgba(105,87,245,.2)] hover:bg-[#5845e7]"
    : "px-3 py-2 text-sm text-[#60616c] hover:text-[#17181c] dark:text-[#9a9aa4] dark:hover:text-[#e4e4e8]";
  const className = `${base} ${look}`;
  if (href) {
    return (
      <a href={href} className={className} onClick={onClick}>
        {children}
      </a>
    );
  }
  return (
    <button type="button" className={className} onClick={onClick}>
      {children}
    </button>
  );
}

function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      viewBox="0 0 12 12"
      aria-hidden="true"
      className={`h-3 w-3 transition-transform ${open ? "rotate-180" : ""}`}
    >
      <path
        d="M2 4.5 6 8.5 10 4.5"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export type NavItem =
  | { label: string; href: string; external?: boolean; type?: undefined }
  | { label: string; type: "categories" };

export type CategoryItem = { label: string; href: string };

function CategoriesMenu({ label, categories }: { label: string; categories: CategoryItem[] }) {
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
  if (!categories.length) return null;
  return (
    <div className="relative" ref={box}>
      <button
        type="button"
        aria-haspopup="true"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
        className={`${NAV_LINK} inline-flex items-center gap-1`}
      >
        {label}
        <Chevron open={open} />
      </button>
      {open ? (
        <div className="absolute left-0 top-full z-50 mt-3 w-60 rounded-xl border border-[#e9e9ef] bg-white p-1.5 shadow-[0_18px_40px_rgba(28,31,42,.12)] dark:border-[#2a2b30] dark:bg-[#17181c]">
          {categories.map((category) => (
            <a
              key={category.href}
              href={category.href}
              onClick={() => setOpen(false)}
              className="block truncate rounded-lg px-3 py-2 text-sm font-medium text-[#4f5059] transition hover:bg-[#f4f4f9] hover:text-[#17181c] dark:text-[#9a9aa4] dark:hover:bg-[#22232a] dark:hover:text-white"
            >
              {category.label}
            </a>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function ChmabaHeader({
  homeHref = "/",
  onHomeClick,
  nav = [],
  categories = [],
  onNavClick,
  actions = null,
  signIn,
  cta,
}: {
  homeHref?: string;
  onHomeClick?: () => void;
  nav?: NavItem[];
  categories?: CategoryItem[];
  onNavClick?: (event: React.MouseEvent, item: NavItem) => void;
  actions?: React.ReactNode;
  signIn?: { label: string; href?: string; onClick?: () => void } | null;
  cta?: { label: string; href?: string; onClick?: () => void } | null;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const box = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!menuOpen) return undefined;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    const dismiss = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener("keydown", escape);
    document.addEventListener("mousedown", dismiss);
    return () => {
      document.removeEventListener("keydown", escape);
      document.removeEventListener("mousedown", dismiss);
    };
  }, [menuOpen]);
  return (
    <header
      ref={box}
      className="sticky top-0 z-40 border-b border-[#e9e9ef]/70 bg-[#fafafd]/85 backdrop-blur-xl dark:border-[#2a2b30]/80 dark:bg-[#111114]/85"
    >
      <div className="mx-auto flex h-[76px] max-w-[1240px] items-center justify-between gap-4 px-5 lg:px-8">
        <a href={homeHref} aria-label="Chmaba home" className="transition hover:opacity-80" onClick={onHomeClick}>
          <ChmabaLogo />
        </a>
        <nav className="hidden items-center gap-8 text-sm font-medium text-[#6f707a] md:flex">
          {nav.map((item) =>
            item.type === "categories" ? (
              <CategoriesMenu key={item.label} label={item.label} categories={categories} />
            ) : (
              <a
                key={item.label}
                href={item.href}
                className={NAV_LINK}
                {...(item.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                onClick={(event) => onNavClick?.(event, item)}
              >
                {item.label}
              </a>
            ),
          )}
        </nav>
        <div className="flex items-center gap-2.5">
          {actions}
          {signIn ? (
            <span className="hidden sm:block">
              <HeaderAction href={signIn.href} onClick={signIn.onClick}>
                {signIn.label}
              </HeaderAction>
            </span>
          ) : null}
          {cta ? (
            <HeaderAction primary href={cta.href} onClick={cta.onClick}>
              {cta.label}
            </HeaderAction>
          ) : null}
          <button
            type="button"
            aria-label={menuOpen ? "Close menu" : "Open menu"}
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((value) => !value)}
            className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-[#60616c] transition hover:bg-[#f0f0f5] hover:text-[#17181c] dark:text-[#9a9aa4] dark:hover:bg-[#2a2b32] md:hidden"
          >
            <svg
              viewBox="0 0 24 24"
              aria-hidden="true"
              className="h-5 w-5"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.8"
              strokeLinecap="round"
            >
              {menuOpen ? <path d="M6 6l12 12M18 6L6 18" /> : <path d="M4 7h16M4 12h16M4 17h16" />}
            </svg>
          </button>
        </div>
      </div>
      {menuOpen ? (
        <div className="border-t border-[#e9e9ef]/70 bg-white px-5 pb-4 pt-2 dark:border-[#2a2b30]/80 dark:bg-[#17181c] md:hidden">
          <nav className="flex flex-col">
            {nav.map((item) =>
              item.type === "categories" ? (
                <div key={item.label} className="py-1">
                  <p className="px-1 py-1.5 text-[10px] font-bold uppercase tracking-wide text-[#a1a2ab]">
                    {item.label}
                  </p>
                  {categories.map((category) => (
                    <a
                      key={category.href}
                      href={category.href}
                      onClick={() => setMenuOpen(false)}
                      className="block rounded-lg px-2 py-2 text-sm font-medium text-[#4f5059] transition hover:bg-[#f4f4f9] dark:text-[#9a9aa4] dark:hover:bg-[#22232a]"
                    >
                      {category.label}
                    </a>
                  ))}
                </div>
              ) : (
                <a
                  key={item.label}
                  href={item.href}
                  className="rounded-lg px-2 py-2.5 text-sm font-medium text-[#4f5059] transition hover:bg-[#f4f4f9] dark:text-[#9a9aa4] dark:hover:bg-[#22232a]"
                  {...(item.external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
                  onClick={(event) => {
                    setMenuOpen(false);
                    onNavClick?.(event, item);
                  }}
                >
                  {item.label}
                </a>
              ),
            )}
          </nav>
          {signIn || cta ? (
            <div className="mt-3 flex items-center gap-2 border-t border-[#f0f0f3] pt-3 dark:border-[#2a2b30]">
              {signIn
                ? signIn.href
                  ? (
                    <a
                      href={signIn.href}
                      className="inline-flex h-9 flex-1 items-center justify-center rounded-lg border border-[#dedee7] text-xs font-semibold text-[#282930] dark:border-[#363740] dark:text-[#e4e4e8]"
                      onClick={(event) => {
                        setMenuOpen(false);
                        signIn.onClick?.();
                      }}
                    >
                      {signIn.label}
                    </a>
                  )
                  : (
                    <button
                      type="button"
                      className="inline-flex h-9 flex-1 items-center justify-center rounded-lg border border-[#dedee7] text-xs font-semibold text-[#282930] dark:border-[#363740] dark:text-[#e4e4e8]"
                      onClick={() => {
                        setMenuOpen(false);
                        signIn.onClick?.();
                      }}
                    >
                      {signIn.label}
                    </button>
                  )
                : null}
              {cta
                ? cta.href
                  ? (
                    <a
                      href={cta.href}
                      className="inline-flex h-9 flex-1 items-center justify-center rounded-lg bg-[#6957f5] text-xs font-semibold text-white"
                      onClick={() => setMenuOpen(false)}
                    >
                      {cta.label}
                    </a>
                  )
                  : (
                    <button
                      type="button"
                      className="inline-flex h-9 flex-1 items-center justify-center rounded-lg bg-[#6957f5] text-xs font-semibold text-white"
                      onClick={() => setMenuOpen(false)}
                    >
                      {cta.label}
                    </button>
                  )
                : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </header>
  );
}

export function ChmabaFooter({
  homeHref = "/",
  onHomeClick,
  links = [],
  note,
}: {
  homeHref?: string;
  onHomeClick?: () => void;
  links?: { label: string; href: string; onClick?: () => void; external?: boolean; strong?: boolean }[];
  note?: React.ReactNode;
}) {
  return (
    <footer className="border-t border-[#eaeaef] px-5 py-7 lg:px-8 dark:border-[#2a2b30]">
      <div className="mx-auto flex max-w-[1240px] flex-col gap-4 text-xs text-[#92939d] sm:flex-row sm:items-center sm:justify-between">
        <a href={homeHref} aria-label="Chmaba home" className="transition hover:opacity-80" onClick={onHomeClick}>
          <ChmabaLogo />
        </a>
        <span>{note || `© ${new Date().getFullYear()} Chmaba Technologies. Built with love for better days.`}</span>
        <div className="flex gap-5">
          {links.map(({ label, href, onClick, external, strong }) => (
            <a
              key={label}
              href={href}
              className={strong ? `font-semibold ${NAV_LINK}` : NAV_LINK}
              {...(external ? { target: "_blank", rel: "noopener noreferrer" } : {})}
              onClick={onClick}
            >
              {label}
            </a>
          ))}
        </div>
      </div>
    </footer>
  );
}

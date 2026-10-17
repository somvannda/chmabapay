"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "chmaba-theme";

/**
 * Light/dark switch for the shared Chmaba token set.
 *
 * The class is applied to `<html>` before paint by the inline script in the root
 * layout, so this component only mirrors that state and writes the user's choice
 * back to `localStorage`. The system preference is the default until a choice is
 * made.
 */
export function ThemeToggle() {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    setDark(document.documentElement.classList.contains("dark"));
  }, []);

  function toggle() {
    const next = !document.documentElement.classList.contains("dark");
    document.documentElement.classList.toggle("dark", next);
    try {
      localStorage.setItem(STORAGE_KEY, next ? "dark" : "light");
    } catch {
      // Private mode / storage blocked: the class still flips for this page.
    }
    setDark(next);
  }

  return (
    <button
      type="button"
      className="landing-header-link landing-theme-toggle"
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
    >
      <span aria-hidden="true">{dark ? "☀" : "☾"}</span>
    </button>
  );
}

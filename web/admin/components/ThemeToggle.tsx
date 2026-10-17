"use client";

import { useEffect, useState } from "react";

const STORAGE_KEY = "chmaba-theme";

/** Light/dark switch for the console. Mirrors the `<html>.dark` class applied by
 *  the inline script in the root layout, and persists the operator's choice. */
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
      // storage blocked; the class still flips for this page
    }
    setDark(next);
  }

  return (
    <button
      type="button"
      className="cp-theme-toggle"
      onClick={toggle}
      aria-label={dark ? "Switch to light mode" : "Switch to dark mode"}
      title={dark ? "Switch to light mode" : "Switch to dark mode"}
    >
      <span aria-hidden="true">{dark ? "☀" : "☾"}</span>
    </button>
  );
}

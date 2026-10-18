"use client";

import { useRef, useState } from "react";
import CodeBlock from "./CodeBlock";

type Panel = { title: string; lang: string; code: string };

/**
 * The six quick-start steps as tabs.
 *
 * Stacked, six panels pushed the section a screen and a half down; one visible at a time
 * keeps the whole flow in view. Every panel stays in the DOM — inactive ones carry
 * `hidden` — so a snippet is on the page rather than fetched on click, and nothing is
 * lost to a reader who tabs through or to anything reading the markup. Arrow keys move
 * between steps, the way a tablist is expected to behave.
 */
export default function QuickStartTabs({ panels }: { panels: readonly Panel[] }) {
  const [active, setActive] = useState(0);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const focusTab = (index: number) => {
    const next = (index + panels.length) % panels.length;
    setActive(next);
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="docs-qs-tabs">
      <div
        className="docs-qs-tablist"
        role="tablist"
        aria-label="Quick start steps"
        onKeyDown={(event) => {
          if (event.key === "ArrowRight") {
            event.preventDefault();
            focusTab(active + 1);
          } else if (event.key === "ArrowLeft") {
            event.preventDefault();
            focusTab(active - 1);
          }
        }}
      >
        {panels.map((panel, index) => {
          const selected = index === active;
          return (
            <button
              key={panel.title}
              type="button"
              role="tab"
              id={`qs-tab-${index}`}
              aria-selected={selected}
              aria-controls={`qs-panel-${index}`}
              tabIndex={selected ? 0 : -1}
              ref={(el) => {
                tabRefs.current[index] = el;
              }}
              className={`docs-qs-tab${selected ? " is-active" : ""}`}
              onClick={() => setActive(index)}
            >
              <span className="docs-qs-tab-index">{String(index + 1).padStart(2, "0")}</span>
              <span>{panel.title}</span>
            </button>
          );
        })}
      </div>
      {panels.map((panel, index) => (
        <div
          key={panel.title}
          role="tabpanel"
          id={`qs-panel-${index}`}
          aria-labelledby={`qs-tab-${index}`}
          hidden={index !== active}
        >
          <CodeBlock code={panel.code} lang={panel.lang} className="docs-quick-step" />
        </div>
      ))}
    </div>
  );
}

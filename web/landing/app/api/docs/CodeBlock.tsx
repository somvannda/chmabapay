"use client";

import { useState } from "react";

/**
 * A code panel a reader can copy from in one click, with its title above it.
 *
 * The Copy button is the reason this is a client component: the rest of the API page is
 * server-rendered, and the page's own promise is "six copy-paste steps" — a step a reader
 * has to select by hand is not one. Everything else about the panel is presentation, so
 * this component owns no layout of its own and takes its styling from `globals.css`.
 */

function CopyButton({ text, title }: { text: string; title: string }) {
  const [state, setState] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setState("copied");
    } catch {
      // Not a secure context, or the permission was refused. Saying so is better than a
      // button that appears to work and silently does nothing.
      setState("failed");
    }
    window.setTimeout(() => setState("idle"), 2000);
  };

  return (
    <button
      type="button"
      className="docs-code-copy"
      onClick={() => void copy()}
      aria-label={`Copy ${title}`}
    >
      {state === "copied" ? "Copied" : state === "failed" ? "Copy failed" : "Copy"}
    </button>
  );
}

export default function CodeBlock({
  code,
  lang,
  title,
  langTag = false,
  className,
}: {
  code: string;
  /** Sets the `code` element's class, which is what the language colouring hooks onto. */
  lang?: string;
  /** The label in the panel's header. Falls back to the language, then to `bash`. */
  title?: string;
  /**
   * Also show the language as a tag in the header. Opt-in, because a panel whose title
   * already names the language ("Example: Node.js") would only repeat itself — the
   * Quick-start steps are the callers that spend the header on the step name alone.
   */
  langTag?: boolean;
  className?: string;
}) {
  const label = title ?? lang ?? "bash";

  return (
    <div className={`docs-code-block${className ? ` ${className}` : ""}`}>
      <div className="docs-code-head">
        <span className="docs-code-title">{label}</span>
        <div className="docs-code-head-right">
          {langTag && lang ? <span className="docs-code-lang">{lang}</span> : null}
          <CopyButton text={code} title={label} />
        </div>
      </div>
      <pre className="docs-code-pre">
        <code className={lang}>{code}</code>
      </pre>
    </div>
  );
}

"use client";

import { useCallback, useState } from "react";

/**
 * A value with a copy button.
 *
 * Used for the identifiers a merchant has to move out of this page and into their
 * own code — a store's public id and its `external_id` — where a misread character
 * is a 404 on a live integration.
 */
export function CopyField({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const onCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard access can be refused (insecure origin, denied permission). The
      // value is still on screen to read, so there is nothing to report.
    }
  }, [value]);

  return (
    <div className="dash-copy-field">
      <div className="dash-copy-field-value">{value}</div>
      <button type="button" className="dash-copy-btn" onClick={onCopy}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

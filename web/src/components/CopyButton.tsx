"use client";

import { useState } from "react";

interface CopyButtonProps {
  text: string;
  label?: string;
  variant?: "default" | "inline";
  className?: string;
}

export function CopyButton({
  text,
  label,
  variant = "default",
  className = "",
}: CopyButtonProps) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API not available or permission denied — silently ignore
    }
  }

  const btnClass = variant === "inline" ? "copy-btn-inline" : "copy-btn";

  return (
    <button
      type="button"
      onClick={handleCopy}
      className={`${btnClass} ${copied ? "copied" : ""} ${className}`.trim()}
      title={copied ? "Copied!" : label ? `Copy ${label}` : "Copy to clipboard"}
      aria-label={copied ? "Copied!" : label ? `Copy ${label}` : "Copy to clipboard"}
    >
      {copied ? (
        /* Checkmark icon */
        <svg
          className="copy-btn-icon"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="20 6 9 17 4 12" />
        </svg>
      ) : (
        /* Clipboard icon */
        <svg
          className="copy-btn-icon"
          xmlns="http://www.w3.org/2000/svg"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <rect x="9" y="2" width="13" height="13" rx="2" ry="2" />
          <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
        </svg>
      )}
      <span className="copy-btn-label">
        {copied ? "Copied!" : label ?? "Copy"}
      </span>
    </button>
  );
}

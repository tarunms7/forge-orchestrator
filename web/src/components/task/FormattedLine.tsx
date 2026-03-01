"use client";

export function stripAnsi(str: string): string {
  return str.replace(/\x1B\[[0-9;]*[a-zA-Z]/g, "");
}

/** Renders a single output line with basic markdown-like formatting. */
export function FormattedLine({ text }: { text: string }) {
  const clean = stripAnsi(text);

  if (/^#{1,3}\s/.test(clean)) {
    const level = clean.match(/^(#+)/)?.[1].length ?? 1;
    const content = clean.replace(/^#+\s*/, "");
    const sizes = ["text-sm font-bold text-text-primary", "text-sm font-semibold text-text-primary", "text-xs font-semibold text-text-secondary"];
    return <div className={`mt-2 mb-1 ${sizes[Math.min(level - 1, 2)]}`}>{content}</div>;
  }
  if (/^[-=*]{3,}\s*$/.test(clean)) {
    return <div className="my-1 border-t border-border-color" />;
  }
  if (/^\s*[-*]\s/.test(clean)) {
    const content = clean.replace(/^\s*[-*]\s/, "");
    return (
      <div className="flex gap-2 text-text-tertiary">
        <span className="text-text-dim select-none">&#x2022;</span>
        <span>{content}</span>
      </div>
    );
  }
  if (/^\s*\d+[.)]\s/.test(clean)) {
    const match = clean.match(/^\s*(\d+)[.)]\s(.*)/);
    if (match) {
      return (
        <div className="flex gap-2 text-text-tertiary">
          <span className="text-text-dim select-none min-w-[1.2rem] text-right">{match[1]}.</span>
          <span>{match[2]}</span>
        </div>
      );
    }
  }
  if (/^```/.test(clean)) {
    const lang = clean.replace(/^```\s*/, "");
    if (lang) return <div className="mt-1 text-xs text-text-dim">{lang}</div>;
    return <div className="my-0.5" />;
  }
  if (!clean.trim()) return <div className="h-1" />;
  return <div className="whitespace-pre-wrap break-words text-text-tertiary">{clean}</div>;
}

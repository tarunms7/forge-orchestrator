"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTaskStore } from "@/stores/taskStore";
import { FormattedLine } from "./FormattedLine";
import { CopyButton } from "@/components/CopyButton";

/* ── Planner Log Modal ─────────────────────────────────────────────── */

function PlannerLogModal({ lines, onClose }: { lines: string[]; onClose: () => void }) {
  const bodyRef = useRef<HTMLDivElement>(null);

  // Lock body scroll
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Scroll to bottom on open
  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, []);

  return createPortal(
    <div className="log-modal-overlay" onClick={onClose}>
      <div className="log-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="log-modal-header">
          <div>
            <span className="log-modal-title">Planner Output</span>
            <span className="log-modal-subtitle">{lines.length} lines</span>
          </div>
          <div className="log-modal-header-actions">
            <CopyButton text={lines.join("\n")} variant="default" label="Copy" />
            <button className="log-modal-close" onClick={onClose}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div ref={bodyRef} className="log-modal-body">
          {lines.map((line, i) => (
            <div key={i} className="log-modal-line">
              <FormattedLine text={line} />
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Planner Card ──────────────────────────────────────────────────── */

export default function PlannerCard() {
  const rawPlannerOutput = useTaskStore((s) => s.plannerOutput);
  const phase = useTaskStore((s) => s.phase);
  const plannerStartedAt = useTaskStore((s) => s.plannerStartedAt);
  const plannerLastActivityAt = useTaskStore((s) => s.plannerLastActivityAt);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showModal, setShowModal] = useState(false);
  const [nowMs, setNowMs] = useState(() => Date.now());

  const isActive = phase === "planning";

  useEffect(() => {
    if (!isActive) return;
    const timer = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [isActive]);

  // Strip JSON code blocks from planner output — the raw task graph JSON is
  // noisy and the user already sees it as the structured plan panel.
  // Keep repeated lines and only collapse consecutive duplicates so visible
  // planner activity is not mistaken for a frozen run.
  const visiblePlannerLines = useMemo(() => {
    const result: string[] = [];
    let inJsonFence = false;
    for (const line of rawPlannerOutput) {
      const trimmed = line.trim();
      if (/^```json\b/.test(trimmed)) {
        inJsonFence = true;
        continue;
      }
      if (inJsonFence && trimmed === "```") {
        inJsonFence = false;
        continue;
      }
      if (inJsonFence) continue;
      if (trimmed === "```") continue;
      if (trimmed.startsWith("{") && trimmed.length > 100) continue;
      result.push(line);
    }
    return result;
  }, [rawPlannerOutput]);

  const plannerOutput = useMemo(() => {
    const result: Array<{ text: string; count: number }> = [];
    for (const line of visiblePlannerLines) {
      const trimmed = line.trim();
      const last = result[result.length - 1];
      if (last && last.text.trim() === trimmed) {
        last.count += 1;
      } else {
        result.push({ text: line, count: 1 });
      }
    }
    return result;
  }, [visiblePlannerLines]);
  const lineCount = plannerOutput.reduce((sum, line) => sum + line.count, 0);

  const elapsedMs = isActive && plannerStartedAt ? Math.max(0, nowMs - plannerStartedAt) : 0;
  const idleMs = isActive && plannerLastActivityAt ? Math.max(0, nowMs - plannerLastActivityAt) : 0;

  const formatDuration = (ms: number) => {
    const totalSeconds = Math.floor(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    return minutes > 0 ? `${minutes}m ${seconds}s` : `${seconds}s`;
  };

  const latestLine = plannerOutput.at(-1)?.text?.trim() ?? "";
  const reassurance =
    idleMs >= 20000
      ? "Planner is still working. Silent stretches are normal while it reads files and thinks."
      : null;

  // Auto-scroll during active planning
  useEffect(() => {
    if (isActive && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plannerOutput, isActive]);

  // Show the card when actively planning, OR when we have output to display
  // (even after planning completes). Also show during contracts phase.
  if (phase !== "planning" && plannerOutput.length === 0) return null;

  return (
    <>
      <div className={`planner-card mb-8${isActive ? " active" : ""}`}>
        {/* Header */}
        <div className="planner-header">
          {/* Status icon */}
          <div className={`planner-status-icon ${isActive ? "active" : "done"}`}>
            {isActive ? (
              <svg className="animate-spin" width="14" height="14" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>

          {/* Title */}
          <span className="planner-title">
            {isActive ? "Planning" : "Planning Complete"}
            {!isActive && lineCount > 0 && (
              <span className="subtitle">{lineCount} lines</span>
            )}
          </span>

          {/* Show log in modal when planning is done */}
          {!isActive && lineCount > 0 && (
            <button
              onClick={() => setShowModal(true)}
              className="btn btn-ghost btn-sm"
            >
              Show log
            </button>
          )}
        </div>

        {isActive && (
          <div className="planner-meta">
            <span>{elapsedMs > 0 ? `Elapsed ${formatDuration(elapsedMs)}` : "Starting..."}</span>
            <span>{plannerLastActivityAt ? `Last update ${formatDuration(idleMs)} ago` : "Waiting for first planner output"}</span>
            {latestLine && <span>Latest: {latestLine}</span>}
          </div>
        )}

        {/* Streaming output — only visible during active planning */}
        {isActive && lineCount > 0 && (
          <div ref={scrollRef} className="planner-body">
            {plannerOutput.map((line, i) => (
              <div
                key={`${i}-${line.text.trim()}`}
                className={`terminal-line ${i === plannerOutput.length - 1 ? "active" : ""}`}
              >
                <FormattedLine text={line.text} />
                {line.count > 1 && <span className="planner-line-count">×{line.count}</span>}
              </div>
            ))}
            {reassurance && <div className="planner-hint">{reassurance}</div>}
          </div>
        )}

        {/* Loading indicator when no output yet */}
        {isActive && lineCount === 0 && (
          <div className="planner-body" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span className="terminal-line active">
              Analyzing project and decomposing task...
              {plannerStartedAt && ` ${formatDuration(elapsedMs)}`}
            </span>
          </div>
        )}
      </div>

      {/* Planner Log Modal */}
      {showModal && (
        <PlannerLogModal
          lines={visiblePlannerLines}
          onClose={() => setShowModal(false)}
        />
      )}
    </>
  );
}

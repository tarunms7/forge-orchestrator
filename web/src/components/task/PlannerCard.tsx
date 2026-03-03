"use client";

import { useEffect, useRef, useState } from "react";
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
          <span onClick={(e) => e.stopPropagation()}>
            <CopyButton
              text={lines.join("\n")}
              label="log"
              variant="icon-only"
            />
          </span>
          <button className="log-modal-close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
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
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showModal, setShowModal] = useState(false);

  const isActive = phase === "planning";

  // Deduplicate lines — the SDK emits the same content as both
  // AssistantMessage (streaming) and ResultMessage (final), which
  // causes identical JSON to appear twice in the output.
  const plannerOutput = rawPlannerOutput.filter((line, i, arr) => {
    const trimmed = line.trim();
    return arr.findIndex((l) => l.trim() === trimmed) === i;
  });
  const lineCount = plannerOutput.length;

  // Auto-scroll during active planning
  useEffect(() => {
    if (isActive && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plannerOutput, isActive]);

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

        {/* Streaming output — only visible during active planning */}
        {isActive && lineCount > 0 && (
          <div ref={scrollRef} className="planner-body">
            {plannerOutput.map((line, i) => (
              <div
                key={i}
                className={`terminal-line ${i === lineCount - 1 ? "active" : ""}`}
              >
                <FormattedLine text={line} />
              </div>
            ))}
          </div>
        )}

        {/* Loading indicator when no output yet */}
        {isActive && lineCount === 0 && (
          <div className="planner-body" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span className="terminal-line active">Analyzing project and decomposing task...</span>
          </div>
        )}
      </div>

      {/* Planner Log Modal */}
      {showModal && (
        <PlannerLogModal lines={plannerOutput} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}

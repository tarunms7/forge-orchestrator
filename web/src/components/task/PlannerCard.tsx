"use client";

import { useEffect, useRef, useState } from "react";
import { useTaskStore } from "@/stores/taskStore";

export default function PlannerCard() {
  const plannerOutput = useTaskStore((s) => s.plannerOutput);
  const phase = useTaskStore((s) => s.phase);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [showLog, setShowLog] = useState(true);

  const isActive = phase === "planning";
  const lineCount = plannerOutput.length;

  // Auto-scroll during active planning
  useEffect(() => {
    if (isActive && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plannerOutput, isActive]);

  // Collapse the log when planning finishes
  useEffect(() => {
    if (!isActive && plannerOutput.length > 0) {
      setShowLog(false);
    }
  }, [isActive, plannerOutput.length]);

  if (phase !== "planning" && plannerOutput.length === 0) return null;

  return (
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

        {/* Toggle log visibility when planning is done */}
        {!isActive && lineCount > 0 && (
          <button
            onClick={() => setShowLog((v) => !v)}
            className="btn btn-ghost btn-sm"
          >
            {showLog ? "Hide log" : "Show log"}
          </button>
        )}
      </div>

      {/* Streaming output (always visible during planning, toggleable after) */}
      {lineCount > 0 && (isActive || showLog) && (
        <div ref={scrollRef} className="planner-body">
          {plannerOutput.map((line, i) => (
            <div
              key={i}
              className={`terminal-line ${isActive && i === lineCount - 1 ? "active" : ""}`}
            >
              {line}
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
  );
}

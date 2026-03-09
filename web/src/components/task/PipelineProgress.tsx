"use client";

import { Fragment } from "react";
import type { PipelineState } from "@/stores/taskStore";

const STEPS: { key: PipelineState["phase"]; label: string }[] = [
  { key: "planning", label: "Plan" },
  { key: "planned", label: "Review" },
  { key: "contracts", label: "Contracts" },
  { key: "executing", label: "Execute" },
  { key: "reviewing", label: "QA" },
  { key: "complete", label: "Done" },
];

const PHASE_ORDER: Record<PipelineState["phase"], number> = {
  idle: -1,
  planning: 0,
  planned: 1,
  contracts: 2,
  executing: 3,
  reviewing: 4,
  paused: 3,
  complete: 5,
  cancelled: -2,
  error: -3,
};

function PauseIcon() {
  return (
    <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" style={{ color: "var(--amber)" }}>
      <rect x="6" y="4" width="4" height="16" rx="1" />
      <rect x="14" y="4" width="4" height="16" rx="1" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      className="h-4 w-4 text-white"
      fill="none"
      viewBox="0 0 24 24"
      stroke="currentColor"
    >
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth={2}
        d="M5 13l4 4L19 7"
      />
    </svg>
  );
}

export default function PipelineProgress({
  phase,
}: {
  phase: PipelineState["phase"];
}) {
  const currentIndex = PHASE_ORDER[phase];
  const isPaused = phase === "paused";

  return (
    <div className="progress-track">
      <div className="progress-steps">
        {STEPS.map((step, i) => {
          const stepIndex = PHASE_ORDER[step.key];
          // The final "Done" step should show a checkmark (completed)
          // when the phase is "complete", not a spinner (active).
          const isFinalComplete = step.key === "complete" && phase === "complete";
          const isCompleted = stepIndex < currentIndex || isFinalComplete;
          const isActive = stepIndex === currentIndex && !isFinalComplete;
          // "paused" maps to the Execute step (index 2)
          const isPausedStep = isPaused && step.key === "executing";

          return (
            <Fragment key={step.key}>
              <div
                className={`step ${isCompleted ? "completed" : isActive || isPausedStep ? "current" : ""}`}
                style={isPausedStep ? { color: "var(--amber)" } : undefined}
              >
                <div
                  className="step-indicator"
                  style={isPausedStep ? { borderColor: "var(--amber)", background: "rgba(245,158,11,0.15)", boxShadow: "0 0 8px rgba(245,158,11,0.3)" } : undefined}
                >
                  {isCompleted ? (
                    <CheckIcon />
                  ) : isPausedStep ? (
                    <PauseIcon />
                  ) : isActive ? (
                    <svg className="animate-spin h-4 w-4" fill="none" viewBox="0 0 24 24">
                      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
                    </svg>
                  ) : (
                    <span className="step-number">{i + 1}</span>
                  )}
                </div>
                <div className="step-label" style={isPausedStep ? { color: "var(--amber)" } : undefined}>
                  {isPausedStep ? "Paused" : step.label}
                </div>
              </div>

              {i < STEPS.length - 1 && (
                <div
                  className={`step-connector ${isCompleted ? "done" : isActive ? "active" : ""}`}
                />
              )}
            </Fragment>
          );
        })}
      </div>
    </div>
  );
}

"use client";

import { Fragment } from "react";
import type { PipelineState } from "@/stores/taskStore";

const STEPS: { key: PipelineState["phase"]; label: string }[] = [
  { key: "planning", label: "Plan" },
  { key: "planned", label: "Review" },
  { key: "executing", label: "Execute" },
  { key: "reviewing", label: "QA" },
  { key: "complete", label: "Done" },
];

const PHASE_ORDER: Record<PipelineState["phase"], number> = {
  idle: -1,
  planning: 0,
  planned: 1,
  executing: 2,
  reviewing: 3,
  complete: 4,
  cancelled: -2,
};

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

  return (
    <div className="progress-track">
      <div className="progress-steps">
        {STEPS.map((step, i) => {
          const stepIndex = PHASE_ORDER[step.key];
          const isCompleted = stepIndex < currentIndex;
          const isActive = stepIndex === currentIndex;

          return (
            <Fragment key={step.key}>
              <div
                className={`step ${isCompleted ? "completed" : isActive ? "current" : ""}`}
              >
                <div className="step-indicator">
                  {isCompleted ? (
                    <CheckIcon />
                  ) : (
                    <span className="step-number">{i + 1}</span>
                  )}
                </div>
                <div className="step-label">{step.label}</div>
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

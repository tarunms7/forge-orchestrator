"use client";

import type { PipelineState } from "@/stores/taskStore";

const STEPS: { key: PipelineState["phase"]; label: string }[] = [
  { key: "planning", label: "Planning" },
  { key: "planned", label: "Review" },
  { key: "executing", label: "Executing" },
  { key: "reviewing", label: "Reviewing" },
  { key: "complete", label: "Complete" },
];

const PHASE_ORDER: Record<PipelineState["phase"], number> = {
  idle: -1,
  planning: 0,
  planned: 1,
  executing: 2,
  reviewing: 3,
  complete: 4,
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
    <div className="flex w-full items-center justify-between gap-2">
      {STEPS.map((step, i) => {
        const stepIndex = PHASE_ORDER[step.key];
        const isCompleted = stepIndex < currentIndex;
        const isActive = stepIndex === currentIndex;
        const isUpcoming = stepIndex > currentIndex;

        return (
          <div key={step.key} className="flex flex-1 items-center">
            {/* Step indicator */}
            <div className="flex flex-col items-center gap-1">
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full text-xs font-bold transition-colors ${
                  isCompleted
                    ? "bg-green-600 text-white"
                    : isActive
                      ? "bg-blue-600 text-white animate-pulse"
                      : "bg-zinc-700 text-zinc-400"
                }`}
              >
                {isCompleted ? <CheckIcon /> : i + 1}
              </div>
              <span
                className={`text-xs font-medium ${
                  isCompleted
                    ? "text-green-400"
                    : isActive
                      ? "text-blue-400"
                      : "text-zinc-500"
                }`}
              >
                {step.label}
              </span>
            </div>

            {/* Connecting line */}
            {i < STEPS.length - 1 && (
              <div
                className={`mx-2 h-0.5 flex-1 rounded ${
                  isCompleted
                    ? "bg-green-600"
                    : isActive
                      ? "bg-blue-600/50"
                      : isUpcoming
                        ? "bg-zinc-700"
                        : "bg-zinc-700"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

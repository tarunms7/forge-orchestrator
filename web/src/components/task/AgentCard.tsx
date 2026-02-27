"use client";

import { useEffect, useRef } from "react";
import type { TaskState } from "@/stores/taskStore";

const STATE_BADGE: Record<
  TaskState["state"],
  { label: string; classes: string }
> = {
  pending: { label: "Pending", classes: "bg-zinc-700 text-zinc-300" },
  working: {
    label: "Working",
    classes: "bg-blue-600 text-blue-100 animate-pulse",
  },
  in_review: { label: "In Review", classes: "bg-yellow-600 text-yellow-100" },
  done: { label: "Done", classes: "bg-green-600 text-green-100" },
  error: { label: "Error", classes: "bg-red-600 text-red-100" },
  retrying: {
    label: "Retrying",
    classes: "bg-orange-600 text-orange-100 animate-pulse",
  },
};

function ReviewGateIcon({ result }: { result: string }) {
  if (result === "pass") {
    return (
      <svg
        className="h-4 w-4 text-green-400"
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
  if (result === "fail") {
    return (
      <svg
        className="h-4 w-4 text-red-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M6 18L18 6M6 6l12 12"
        />
      </svg>
    );
  }
  return (
    <svg
      className="h-4 w-4 animate-spin text-yellow-400"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function stripAnsi(str: string): string {
  return str.replace(/\x1B\[[0-9;]*[a-zA-Z]/g, "");
}

export default function AgentCard({ task }: { task: TaskState }) {
  const outputRef = useRef<HTMLDivElement>(null);
  const badge = STATE_BADGE[task.state];

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [task.output]);

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-4 flex flex-col gap-3">
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-zinc-100">
            {task.title}
          </h3>
          <span className="mt-1 inline-block rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-zinc-400">
            {task.branch}
          </span>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.classes}`}
        >
          {badge.label}
        </span>
      </div>

      {/* Files changed */}
      {task.files.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500">
            Files changed ({task.files.length})
          </p>
          <ul className="space-y-0.5">
            {task.files.map((file) => (
              <li
                key={file}
                className="truncate font-mono text-xs text-zinc-400"
              >
                {file}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Terminal output */}
      {task.output.length > 0 && (
        <div
          ref={outputRef}
          className="max-h-64 overflow-y-auto rounded-lg bg-zinc-950 p-3 font-mono text-sm text-green-400"
        >
          {task.output.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap break-all">
              {stripAnsi(line)}
            </div>
          ))}
        </div>
      )}

      {/* Review gates */}
      {task.reviewGates.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500">
            Review Gates
          </p>
          <div className="flex items-center gap-2">
            {task.reviewGates.map((gate) => (
              <div
                key={gate.gate}
                className="flex items-center gap-1"
                title={gate.details}
              >
                <ReviewGateIcon result={gate.result} />
                <span className="text-xs text-zinc-400">
                  Gate {gate.gate}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Merge result */}
      {task.mergeResult && (
        <div
          className={`rounded-lg px-3 py-2 text-xs ${
            task.mergeResult.success
              ? "bg-green-950 text-green-300"
              : "bg-red-950 text-red-300"
          }`}
        >
          {task.mergeResult.success ? (
            <span>
              Merged (+{task.mergeResult.linesAdded ?? 0} / -
              {task.mergeResult.linesRemoved ?? 0})
            </span>
          ) : (
            <span>Merge failed: {task.mergeResult.error}</span>
          )}
        </div>
      )}
    </div>
  );
}

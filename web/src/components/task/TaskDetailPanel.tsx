"use client";

import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";

const STATE_BADGE: Record<string, { label: string; color: string }> = {
  pending: { label: "Pending", color: "bg-zinc-700 text-zinc-300" },
  working: { label: "Working", color: "bg-yellow-900 text-yellow-300" },
  in_review: { label: "In Review", color: "bg-blue-900 text-blue-300" },
  done: { label: "Done", color: "bg-green-900 text-green-300" },
  error: { label: "Error", color: "bg-red-900 text-red-300" },
  retrying: { label: "Retrying", color: "bg-orange-900 text-orange-300" },
};

export default function TaskDetailPanel({
  task,
  onClose,
}: {
  task: TaskState;
  onClose: () => void;
}) {
  const badge = STATE_BADGE[task.state] ?? STATE_BADGE.pending;
  const timeline = useTaskStore((s) => s.timeline);
  const taskTimeline = timeline.filter(e => e.taskId === task.id);

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0 z-40 bg-black/50"
        onClick={onClose}
      />
      {/* Panel */}
      <div className="fixed right-0 top-0 z-50 h-full w-full max-w-2xl overflow-y-auto border-l border-zinc-800 bg-zinc-950 p-6 shadow-2xl">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute right-4 top-4 rounded p-1 text-zinc-400 hover:bg-zinc-800 hover:text-zinc-200"
        >
          <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>

        {/* Header */}
        <div className="mb-6">
          <div className="flex items-center gap-3 mb-2">
            <h2 className="text-lg font-semibold text-white">{task.title}</h2>
            <span className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.color}`}>
              {badge.label}
            </span>
          </div>
          <p className="text-sm text-zinc-400">{task.id}</p>
          {task.costUsd != null && task.costUsd > 0 && (
            <p className="mt-1 text-sm text-zinc-500">Cost: ${task.costUsd.toFixed(4)}</p>
          )}
        </div>

        {/* Description */}
        {task.description && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">Description</h3>
            <p className="text-sm text-zinc-400 whitespace-pre-wrap">{task.description}</p>
          </section>
        )}

        {/* Agent Output */}
        {task.output.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">
              Agent Output ({task.output.length} lines)
            </h3>
            <div className="max-h-64 overflow-y-auto rounded-lg bg-zinc-900 p-3 font-mono text-xs text-zinc-400">
              {task.output.map((line, i) => (
                <div key={i} className="whitespace-pre-wrap">{line}</div>
              ))}
            </div>
          </section>
        )}

        {/* Activity Log */}
        {taskTimeline.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">
              Activity ({taskTimeline.length} events)
            </h3>
            <div className="space-y-1.5 max-h-48 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900 p-3">
              {taskTimeline.map((ev, i) => {
                const label = (() => {
                  const p = ev.payload;
                  switch (ev.type) {
                    case "task:state_changed": return `State → ${p.state}`;
                    case "task:review_update": return `${p.gate} ${p.passed ? "✓ passed" : "✗ failed"}`;
                    case "task:merge_result": return p.success ? "Merged successfully" : `Merge failed: ${p.error || "unknown"}`;
                    case "task:cost_update": return `Cost: $${(p.cost_usd as number)?.toFixed(4)}`;
                    case "task:files_changed": return `${(p.files as string[])?.length || 0} files changed`;
                    default: return ev.type.split(":")[1] || ev.type;
                  }
                })();
                return (
                  <div key={i} className="flex items-start gap-2 text-xs">
                    <span className="shrink-0 font-mono text-zinc-600">
                      {new Date(ev.timestamp).toLocaleTimeString()}
                    </span>
                    <span className="text-zinc-400">
                      {label}
                    </span>
                  </div>
                );
              })}
            </div>
          </section>
        )}

        {/* Review Gates */}
        {task.reviewGates.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">Review Gates</h3>
            <div className="space-y-2">
              {task.reviewGates.map((gate, i) => (
                <div key={i} className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="text-sm font-medium text-zinc-300">{gate.gate}</span>
                    <span className={gate.result === "pass" ? "text-green-400" : "text-red-400"}>
                      {gate.result === "pass" ? "Pass" : "Fail"}
                    </span>
                  </div>
                  {gate.details && (
                    <p className="text-xs text-zinc-500 whitespace-pre-wrap">{gate.details}</p>
                  )}
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Merge Result */}
        {task.mergeResult && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">Merge Result</h3>
            <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-3">
              <p className={`text-sm font-medium ${task.mergeResult.success ? "text-green-400" : "text-red-400"}`}>
                {task.mergeResult.success ? "Merged" : "Failed"}
              </p>
              {task.mergeResult.error && (
                <p className="mt-1 text-xs text-red-400">{task.mergeResult.error}</p>
              )}
              {task.mergeResult.linesAdded != null && (
                <p className="mt-1 text-xs text-zinc-500">
                  +{task.mergeResult.linesAdded} / -{task.mergeResult.linesRemoved || 0}
                </p>
              )}
            </div>
          </section>
        )}

        {/* Files Changed */}
        {task.files.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">
              Files Changed ({task.files.length})
            </h3>
            <ul className="space-y-0.5">
              {task.files.map((f) => (
                <li key={f} className="truncate font-mono text-xs text-zinc-400">{f}</li>
              ))}
            </ul>
          </section>
        )}

        {/* Target Files */}
        {task.targetFiles && task.targetFiles.length > 0 && (
          <section className="mb-6">
            <h3 className="mb-2 text-sm font-semibold text-zinc-300">
              Target Files ({task.targetFiles.length})
            </h3>
            <ul className="space-y-0.5">
              {task.targetFiles.map((f) => (
                <li key={f} className="truncate font-mono text-xs text-zinc-400">{f}</li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </>
  );
}

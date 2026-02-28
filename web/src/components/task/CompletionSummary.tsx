"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";

function StatusDot({ state }: { state: TaskState["state"] }) {
  const colors: Record<TaskState["state"], string> = {
    pending: "bg-zinc-500",
    working: "bg-blue-500",
    in_review: "bg-yellow-500",
    done: "bg-green-500",
    error: "bg-red-500",
    retrying: "bg-orange-500",
  };

  return <span className={`inline-block h-2 w-2 rounded-full ${colors[state]}`} />;
}

export default function CompletionSummary({
  tasks,
  pipelineId,
}: {
  tasks: Record<string, TaskState>;
  pipelineId: string;
}) {
  const token = useAuthStore((s) => s.token);
  const prUrl = useTaskStore((s) => s.prUrl);
  const prLoading = useTaskStore((s) => s.prLoading);
  const prError = useTaskStore((s) => s.prError);

  // Local fallback for manual PR creation (if auto-PR fails)
  const [manualPrLoading, setManualPrLoading] = useState(false);
  const [manualPrError, setManualPrError] = useState<string | null>(null);

  const taskList = Object.values(tasks);
  const totalTasks = taskList.length;
  const passedCount = taskList.filter((t) => t.state === "done").length;
  const failedCount = taskList.filter((t) => t.state === "error").length;
  const totalFiles = taskList.reduce((sum, t) => sum + t.files.length, 0);
  const totalAdded = taskList.reduce(
    (sum, t) => sum + (t.mergeResult?.linesAdded ?? 0),
    0,
  );
  const totalRemoved = taskList.reduce(
    (sum, t) => sum + (t.mergeResult?.linesRemoved ?? 0),
    0,
  );
  const allPassed = failedCount === 0 && passedCount === totalTasks;

  const handleRetryPR = async () => {
    if (!token || !pipelineId) return;
    setManualPrLoading(true);
    setManualPrError(null);
    try {
      const data = await apiPost(`/tasks/${pipelineId}/pr`, {}, token);
      // Store handles pr_url via WS events, but set it as fallback
      useTaskStore.setState({ prUrl: data.pr_url, prLoading: false, prError: null });
    } catch (err: unknown) {
      setManualPrError(err instanceof Error ? err.message : "Failed to create PR");
    } finally {
      setManualPrLoading(false);
    }
  };

  const isCreatingPR = prLoading || manualPrLoading;
  const displayError = prError || manualPrError;

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
      {/* Status banner */}
      <div
        className={`mb-6 rounded-lg px-4 py-3 text-center text-sm font-semibold ${
          failedCount > 0
            ? "bg-red-950 text-red-300 border border-red-800"
            : allPassed
              ? "bg-green-950 text-green-300 border border-green-800"
              : "bg-yellow-950 text-yellow-300 border border-yellow-800"
        }`}
      >
        {failedCount > 0
          ? `${failedCount} task${failedCount !== 1 ? "s" : ""} failed`
          : allPassed
            ? "All tasks completed successfully!"
            : `Pipeline complete — ${passedCount} of ${totalTasks} tasks finished`}
      </div>

      {/* Stats grid */}
      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-5">
        <div className="rounded-lg bg-zinc-800 p-3 text-center">
          <div className="text-2xl font-bold text-zinc-100">{totalTasks}</div>
          <div className="text-xs text-zinc-400">Total Tasks</div>
        </div>
        <div className="rounded-lg bg-zinc-800 p-3 text-center">
          <div className="text-2xl font-bold text-green-400">{passedCount}</div>
          <div className="text-xs text-zinc-400">Passed</div>
        </div>
        <div className="rounded-lg bg-zinc-800 p-3 text-center">
          <div className="text-2xl font-bold text-red-400">{failedCount}</div>
          <div className="text-xs text-zinc-400">Failed</div>
        </div>
        <div className="rounded-lg bg-zinc-800 p-3 text-center">
          <div className="text-2xl font-bold text-zinc-100">{totalFiles}</div>
          <div className="text-xs text-zinc-400">Files Changed</div>
        </div>
        <div className="rounded-lg bg-zinc-800 p-3 text-center">
          <div className="text-lg font-bold">
            <span className="text-green-400">+{totalAdded}</span>
            <span className="text-zinc-500 mx-1">/</span>
            <span className="text-red-400">-{totalRemoved}</span>
          </div>
          <div className="text-xs text-zinc-400">Lines Changed</div>
        </div>
      </div>

      {/* Task list */}
      <div className="mb-6">
        <h3 className="mb-3 text-sm font-semibold text-zinc-300">
          Task Results
        </h3>
        <div className="space-y-2">
          {taskList.map((task) => (
            <div
              key={task.id}
              className="flex items-center justify-between rounded-lg bg-zinc-800 px-4 py-2.5"
            >
              <div className="flex items-center gap-3 min-w-0">
                <StatusDot state={task.state} />
                <span className="truncate text-sm text-zinc-200">
                  {task.title}
                </span>
              </div>
              <div className="flex items-center gap-3 shrink-0">
                {task.mergeResult?.success && (
                  <span className="text-xs">
                    <span className="text-green-400">+{task.mergeResult.linesAdded ?? 0}</span>
                    <span className="text-zinc-600 mx-0.5">/</span>
                    <span className="text-red-400">-{task.mergeResult.linesRemoved ?? 0}</span>
                  </span>
                )}
                <span className="text-xs text-zinc-500">
                  {task.files.length} file{task.files.length !== 1 ? "s" : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* PR Section */}
      <div className="flex flex-wrap items-center gap-3">
        {prUrl ? (
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-lg bg-green-600 px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-green-500"
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 16 16">
              <path d="M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z" />
            </svg>
            View PR on GitHub
          </a>
        ) : isCreatingPR ? (
          <div className="inline-flex items-center gap-2 rounded-lg border border-zinc-700 bg-zinc-800 px-5 py-2.5 text-sm font-medium text-zinc-300">
            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
            Creating PR...
          </div>
        ) : displayError ? (
          <div className="flex items-center gap-2">
            <span className="text-sm text-red-400">{displayError}</span>
            <button
              type="button"
              onClick={handleRetryPR}
              className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700"
            >
              Retry PR
            </button>
          </div>
        ) : (
          <button
            type="button"
            onClick={handleRetryPR}
            className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700"
          >
            Create PR
          </button>
        )}
        <Link
          href="/tasks/new"
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-blue-500"
        >
          New Task
        </Link>
      </div>
    </div>
  );
}

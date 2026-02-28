"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
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
  const [prUrl, setPrUrl] = useState<string | null>(null);
  const [prLoading, setPrLoading] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);

  const taskList = Object.values(tasks);
  const totalTasks = taskList.length;
  const passedCount = taskList.filter((t) => t.state === "done").length;
  const failedCount = taskList.filter((t) => t.state === "error").length;
  const totalFiles = taskList.reduce((sum, t) => sum + t.files.length, 0);
  const allPassed = failedCount === 0 && passedCount === totalTasks;

  const handleCreatePR = async () => {
    if (!token || !pipelineId) return;
    setPrLoading(true);
    setPrError(null);
    try {
      const data = await apiPost(`/tasks/${pipelineId}/pr`, {}, token);
      setPrUrl(data.pr_url);
    } catch (err: unknown) {
      setPrError(err instanceof Error ? err.message : "Failed to create PR");
    } finally {
      setPrLoading(false);
    }
  };

  return (
    <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
      {/* Status banner */}
      <div
        className={`mb-6 rounded-lg px-4 py-3 text-center text-sm font-semibold ${
          allPassed
            ? "bg-green-950 text-green-300 border border-green-800"
            : "bg-red-950 text-red-300 border border-red-800"
        }`}
      >
        {allPassed
          ? "All tasks completed successfully!"
          : `${failedCount} task${failedCount !== 1 ? "s" : ""} failed`}
      </div>

      {/* Stats grid */}
      <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-4">
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
                <span className="text-xs text-zinc-500">
                  {task.files.length} file{task.files.length !== 1 ? "s" : ""}
                </span>
                <span className="text-xs text-zinc-500">
                  {task.output.length} line{task.output.length !== 1 ? "s" : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex flex-wrap gap-3">
        {prUrl ? (
          <a
            href={prUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="rounded-lg bg-green-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-green-500"
          >
            View PR on GitHub
          </a>
        ) : (
          <button
            type="button"
            disabled={prLoading}
            className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300 transition-colors hover:bg-zinc-700 disabled:opacity-50"
            onClick={handleCreatePR}
          >
            {prLoading ? "Creating PR..." : "Create PR"}
          </button>
        )}
        {prError && (
          <span className="self-center text-sm text-red-400">{prError}</span>
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

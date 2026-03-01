"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";

function StatusDot({ state }: { state: TaskState["state"] }) {
  const colors: Record<TaskState["state"], string> = {
    pending: "blocked-status",
    working: "pass",
    in_review: "pass",
    done: "pass",
    error: "fail",
    retrying: "pass",
  };

  return <span className={`result-status ${colors[state]}`} />;
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
    <div className="complete-container">
      {/* Status banner */}
      <div className={`success-banner ${failedCount > 0 ? "error-banner" : ""}`}>
        <div className="success-text">
          <h2>
            {failedCount > 0
              ? `${failedCount} task${failedCount !== 1 ? "s" : ""} failed`
              : allPassed
                ? "All tasks completed successfully!"
                : `Pipeline complete \u2014 ${passedCount} of ${totalTasks} tasks finished`}
          </h2>
          <p>
            {failedCount > 0
              ? "Some tasks encountered errors during execution."
              : "All changes have been merged into the working branch."}
          </p>
        </div>
        <div className="success-actions">
          {prUrl ? (
            <a
              href={prUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary"
            >
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 16 16">
                <path d="M7.177 3.073L9.573.677A.25.25 0 0110 .854v4.792a.25.25 0 01-.427.177L7.177 3.427a.25.25 0 010-.354zM3.75 2.5a.75.75 0 100 1.5.75.75 0 000-1.5zm-2.25.75a2.25 2.25 0 113 2.122v5.256a2.251 2.251 0 11-1.5 0V5.372A2.25 2.25 0 011.5 3.25zM11 2.5h-1V4h1a1 1 0 011 1v5.628a2.251 2.251 0 101.5 0V5A2.5 2.5 0 0011 2.5zm1 10.25a.75.75 0 111.5 0 .75.75 0 01-1.5 0zM3.75 12a.75.75 0 100 1.5.75.75 0 000-1.5z" />
              </svg>
              View PR on GitHub
            </a>
          ) : isCreatingPR ? (
            <div className="btn btn-ghost" style={{ cursor: "default" }}>
              <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
              Creating PR...
            </div>
          ) : displayError ? (
            <>
              <span style={{ fontSize: "13px", color: "var(--red)" }}>{displayError}</span>
              <button type="button" onClick={handleRetryPR} className="btn btn-ghost">
                Retry PR
              </button>
            </>
          ) : (
            <button type="button" onClick={handleRetryPR} className="btn btn-primary">
              Create PR
            </button>
          )}
        </div>
      </div>

      {/* Stats grid */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-value">{totalTasks}</div>
          <div className="stat-label">Total Tasks</div>
        </div>
        <div className="stat-card success">
          <div className="stat-value text-green">{passedCount}</div>
          <div className="stat-label">Passed</div>
        </div>
        <div className="stat-card danger">
          <div className={`stat-value ${failedCount > 0 ? "text-red" : "text-dim"}`}>{failedCount}</div>
          <div className="stat-label">Failed</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">{totalFiles}</div>
          <div className="stat-label">Files Changed</div>
        </div>
        <div className="stat-card">
          <div className="stat-value">
            <span className="text-green">+{totalAdded}</span>
            <span style={{ color: "var(--text-dim)", margin: "0 4px" }}>/</span>
            <span className="text-red">-{totalRemoved}</span>
          </div>
          <div className="stat-label">Lines Changed</div>
        </div>
      </div>

      {/* Task Results */}
      <div className="results-section">
        <h3 className="results-title">Task Results</h3>
        <div className="results-list">
          {taskList.map((task, i) => (
            <div
              key={task.id}
              className={`result-row ${task.state === "error" ? "error" : ""}`}
            >
              <div className="result-left">
                <StatusDot state={task.state} />
                <span className="result-number">#{i + 1}</span>
                <span className="result-name">{task.title}</span>
              </div>
              <div className="result-right">
                {task.mergeResult?.success && (
                  <span className="result-diff">
                    <span className="stat-add">+{task.mergeResult.linesAdded ?? 0}</span>
                    <span className="stat-del">-{task.mergeResult.linesRemoved ?? 0}</span>
                  </span>
                )}
                <span className="result-files">
                  {task.files.length} file{task.files.length !== 1 ? "s" : ""}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* New Task link */}
      <div style={{ marginTop: "24px" }}>
        <Link href="/tasks/new" className="btn btn-primary btn-glow">
          New Task
        </Link>
      </div>
    </div>
  );
}

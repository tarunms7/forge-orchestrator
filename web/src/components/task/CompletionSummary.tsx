"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import { CopyButton } from "@/components/CopyButton";

function buildResultsSummary(tasks: TaskState[]): string {
  const header = `Task Results\n${"=".repeat(60)}`;
  const rows = tasks.map((task, i) => {
    const statusSymbol =
      task.state === "done" ? "✓" : task.state === "error" ? "✗" : "~";
    const added = task.mergeResult?.linesAdded ?? 0;
    const removed = task.mergeResult?.linesRemoved ?? 0;
    const diff = task.mergeResult?.success
      ? `  +${added} / -${removed} lines`
      : "";
    return `${statusSymbol} #${i + 1}  ${task.title} [${task.state}]${diff}`;
  });
  return [header, ...rows].join("\n");
}

function StatusDot({ state }: { state: TaskState["state"] }) {
  const colors: Record<TaskState["state"], string> = {
    pending: "blocked-status",
    working: "pass",
    in_review: "pass",
    done: "pass",
    error: "fail",
    retrying: "pass",
    cancelled: "blocked-status",
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
  const followUpStatus = useTaskStore((s) => s.followUpStatus);

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
  const totalCost = taskList.reduce(
    (sum, t) => sum + (t.costUsd ?? 0),
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

  const scrollToFollowUp = () => {
    const el = document.getElementById("follow-up-panel");
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // Focus the textarea after scrolling
      setTimeout(() => {
        const textarea = el.querySelector("textarea");
        if (textarea) textarea.focus();
      }, 400);
    }
  };

  const isCreatingPR = prLoading || manualPrLoading;
  const displayError = prError || manualPrError;

  // Follow-up status label
  const followUpLabel =
    followUpStatus === "submitting" ? "Submitting follow-up..." :
    followUpStatus === "executing" ? "Follow-up in progress..." :
    followUpStatus === "done" ? "Follow-up complete" :
    null;

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
              <CopyButton text={displayError} label="Copy" />
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
        {totalCost > 0 && (
          <div className="stat-card">
            <div className="stat-value">${totalCost.toFixed(2)}</div>
            <div className="stat-label">Total Cost</div>
          </div>
        )}
      </div>

      {/* Task Results */}
      <div className="results-section">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
          <h3 className="results-title" style={{ margin: 0 }}>Task Results</h3>
          <CopyButton text={buildResultsSummary(taskList)} label="Copy" />
        </div>
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

      {/* Follow-up CTA and status */}
      <div style={{ marginTop: 24, display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <Link href="/tasks/new" className="btn btn-primary btn-glow">
          New Task
        </Link>

        <button
          type="button"
          onClick={scrollToFollowUp}
          className="btn btn-ghost"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            padding: "8px 16px",
            fontSize: 13,
            fontWeight: 500,
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
            cursor: "pointer",
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M8.228 9c.549-1.165 2.03-2 3.772-2 2.21 0 4 1.343 4 3 0 1.4-1.278 2.575-3.006 2.907-.542.104-.994.54-.994 1.093m0 3h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Have follow-up questions?
        </button>

        {followUpLabel && (
          <span
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              fontSize: 12,
              fontWeight: 500,
              color: followUpStatus === "done" ? "var(--green)" : "var(--accent)",
              padding: "4px 10px",
              borderRadius: "var(--radius-sm)",
              background: followUpStatus === "done" ? "rgba(52,211,153,0.1)" : "var(--accent-glow)",
            }}
          >
            {followUpStatus !== "done" && (
              <svg className="h-3 w-3 animate-spin" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            )}
            {followUpLabel}
          </span>
        )}
      </div>
    </div>
  );
}

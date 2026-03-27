"use client";

import { useState } from "react";
import Link from "next/link";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import { CopyButton } from "@/components/CopyButton";
import CIFixPanel from "./CIFixPanel";

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
    awaiting_approval: "pass",
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
  const githubIssueUrl = useTaskStore((s) => s.githubIssueUrl);
  const githubIssueNumber = useTaskStore((s) => s.githubIssueNumber);
  const followUpStatus = useTaskStore((s) => s.followUpStatus);
  const ciFixStatus = useTaskStore((s) => s.ciFixStatus);

  // Local fallback for manual PR creation (if auto-PR fails)
  const [manualPrLoading, setManualPrLoading] = useState(false);
  const [manualPrError, setManualPrError] = useState<string | null>(null);
  const [cleanupLoading, setCleanupLoading] = useState(false);

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
  const totalAgentCost = taskList.reduce(
    (sum, t) => sum + (t.agentCostUsd ?? 0),
    0,
  );
  const totalReviewCost = taskList.reduce(
    (sum, t) => sum + (t.reviewCostUsd ?? 0),
    0,
  );
  // Planner cost = total minus agent and review costs
  const plannerCost = Math.max(0, totalCost - totalAgentCost - totalReviewCost);
  const budgetLimitUsd = useTaskStore((s) => s.budgetLimitUsd);
  const budgetPct = budgetLimitUsd > 0 ? Math.min((totalCost / budgetLimitUsd) * 100, 100) : 0;

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

  const handleCleanup = async () => {
    if (!token || !pipelineId) return;
    setCleanupLoading(true);
    try {
      await apiPost(`/tasks/${pipelineId}/cleanup`, {}, token);
    } catch (err) {
      console.warn("Cleanup failed:", err);
    } finally {
      setCleanupLoading(false);
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
          {prUrl && ciFixStatus === "idle" && (
            <button
              type="button"
              onClick={async () => {
                if (!token || !pipelineId) return;
                try {
                  await apiPost(`/tasks/${pipelineId}/ci-fix`, {}, token);
                  useTaskStore.setState({ ciFixStatus: "watching" });
                } catch (err) {
                  console.warn("Failed to start CI fix:", err);
                }
              }}
              className="btn btn-ghost"
              style={{ fontSize: 13 }}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
              </svg>
              Watch CI
            </button>
          )}
        </div>
        {githubIssueUrl && (
          <a
            href={githubIssueUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="btn btn-ghost"
            style={{ marginTop: "8px", fontSize: "13px" }}
          >
            <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 16 16">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
            Issue #{githubIssueNumber}
          </a>
        )}
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

      {/* Cost Breakdown */}
      {totalCost > 0 && (
        <div className="cost-breakdown-section">
          <h3 className="results-title">Cost Breakdown</h3>
          <div className="cost-breakdown-total">
            <span className="cost-breakdown-total-label">Total Cost</span>
            <span className="cost-breakdown-total-value">${totalCost.toFixed(2)}</span>
          </div>
          {budgetLimitUsd > 0 && (
            <div className="cost-breakdown-budget">
              Budget utilization: {budgetPct.toFixed(0)}% of ${budgetLimitUsd.toFixed(2)}
            </div>
          )}
          {/* CSS-only bar chart */}
          <div className="cost-bar-chart" role="img" aria-label={`Cost breakdown: Planner $${plannerCost.toFixed(2)}, Agents $${totalAgentCost.toFixed(2)}, Review $${totalReviewCost.toFixed(2)}`}>
            {plannerCost > 0 && (
              <div
                className="cost-bar-segment cost-bar-planner"
                style={{ flex: plannerCost }}
                title={`Planner: $${plannerCost.toFixed(2)}`}
              />
            )}
            {totalAgentCost > 0 && (
              <div
                className="cost-bar-segment cost-bar-agent"
                style={{ flex: totalAgentCost }}
                title={`Agents: $${totalAgentCost.toFixed(2)}`}
              />
            )}
            {totalReviewCost > 0 && (
              <div
                className="cost-bar-segment cost-bar-review"
                style={{ flex: totalReviewCost }}
                title={`Review: $${totalReviewCost.toFixed(2)}`}
              />
            )}
          </div>
          <div className="cost-bar-legend">
            {plannerCost > 0 && (
              <span className="cost-legend-item">
                <span className="cost-legend-dot" style={{ background: "var(--purple)" }} />
                Planner ${plannerCost.toFixed(2)}
              </span>
            )}
            {totalAgentCost > 0 && (
              <span className="cost-legend-item">
                <span className="cost-legend-dot" style={{ background: "var(--accent)" }} />
                Agents ${totalAgentCost.toFixed(2)}
              </span>
            )}
            {totalReviewCost > 0 && (
              <span className="cost-legend-item">
                <span className="cost-legend-dot" style={{ background: "var(--amber)" }} />
                Review ${totalReviewCost.toFixed(2)}
              </span>
            )}
          </div>
        </div>
      )}

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

        {/* Cleanup worktrees — always visible so user can trigger anytime */}
        <button
          type="button"
          onClick={handleCleanup}
          disabled={cleanupLoading}
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
            cursor: cleanupLoading ? "default" : "pointer",
            opacity: cleanupLoading ? 0.7 : 1,
          }}
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
          {cleanupLoading ? "Cleaning..." : "Clean Up Worktrees"}
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

      <CIFixPanel pipelineId={pipelineId} />
    </div>
  );
}

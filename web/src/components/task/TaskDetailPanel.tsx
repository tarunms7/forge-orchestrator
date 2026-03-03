"use client";

import { useState } from "react";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { CopyButton } from "@/components/CopyButton";
import { FormattedLine } from "./FormattedLine";

const STATE_CLASS: Record<string, { label: string; badgeClass: string }> = {
  pending: { label: "Pending", badgeClass: "state-badge pending" },
  working: { label: "Working", badgeClass: "state-badge working" },
  in_review: { label: "In Review", badgeClass: "state-badge review" },
  done: { label: "Done", badgeClass: "state-badge done" },
  error: { label: "Error", badgeClass: "state-badge error" },
  retrying: { label: "Retrying", badgeClass: "state-badge retrying" },
};

function activityDotColor(type: string): string {
  const colors: Record<string, string> = {
    "task:state_changed": "blue",
    "task:review_update": "amber",
    "task:merge_result": "green",
    "task:cost_update": "gray",
    "task:files_changed": "blue",
  };
  return colors[type] || "gray";
}

export default function TaskDetailPanel({
  task,
  onClose,
}: {
  task: TaskState;
  onClose: () => void;
}) {
  const badge = STATE_CLASS[task.state] ?? STATE_CLASS.pending;
  const timeline = useTaskStore((s) => s.timeline);
  const taskTimeline = timeline.filter(e => e.taskId === task.id);
  const [activeTab, setActiveTab] = useState<'output' | 'review' | 'files' | 'activity'>('output');

  // Derive review gates from timeline if task.reviewGates is empty
  // (handles race condition where REST hydration overwrites WS-accumulated gates)
  const reviewGates = task.reviewGates.length > 0
    ? task.reviewGates
    : taskTimeline
        .filter(e => e.type === "task:review_update")
        .map(e => ({
          gate: (e.payload.gate as string) || "",
          result: e.payload.passed ? "pass" : "fail",
          details: (e.payload.details as string) || undefined,
        }));

  // Derive merge result from timeline if not set on task
  const mergeResult = task.mergeResult ?? (() => {
    const mergeEvent = taskTimeline.find(e => e.type === "task:merge_result");
    if (!mergeEvent) return undefined;
    return {
      success: mergeEvent.payload.success as boolean,
      error: mergeEvent.payload.error as string | undefined,
      linesAdded: mergeEvent.payload.linesAdded as number | undefined,
      linesRemoved: mergeEvent.payload.linesRemoved as number | undefined,
    };
  })();

  return (
    <>
      {/* Overlay */}
      <div className="detail-overlay visible" onClick={onClose} />

      {/* Panel */}
      <div className="detail-panel open">
        {/* Header */}
        <div className="detail-header">
          <div className="detail-header-top">
            <div className="detail-status-row">
              <span className={badge.badgeClass}>{badge.label}</span>
              {task.costUsd != null && task.costUsd > 0 && (
                <span className="detail-cost">${task.costUsd.toFixed(4)}</span>
              )}
            </div>
            <button className="detail-close" onClick={onClose}>
              <svg width="14" height="14" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <h2 className="detail-title">{task.title}</h2>
          <div className="detail-branch">{task.id}</div>
        </div>

        {/* Tabs */}
        <div className="detail-tabs">
          <button
            className={`detail-tab ${activeTab === 'output' ? 'active' : ''}`}
            onClick={() => setActiveTab('output')}
          >
            Output
          </button>
          <button
            className={`detail-tab ${activeTab === 'review' ? 'active' : ''}`}
            onClick={() => setActiveTab('review')}
          >
            Review
          </button>
          <button
            className={`detail-tab ${activeTab === 'files' ? 'active' : ''}`}
            onClick={() => setActiveTab('files')}
          >
            Files
          </button>
          <button
            className={`detail-tab ${activeTab === 'activity' ? 'active' : ''}`}
            onClick={() => setActiveTab('activity')}
          >
            Activity
          </button>
        </div>

        {/* Body */}
        <div className="detail-body">
          {/* Output Tab */}
          <div className={`tab-content ${activeTab === 'output' ? 'active' : ''}`}>
            {task.description && (
              <div style={{ marginBottom: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", marginBottom: "8px" }}>
                  <span style={{ fontSize: "12px", fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em" }}>Description</span>
                  <CopyButton text={task.description} label="Copy" />
                </div>
                <div style={{ fontSize: "13px", color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>
                  {task.description}
                </div>
              </div>
            )}
            {task.output.length > 0 ? (
              <div className="detail-terminal">
                <div style={{ display: "flex", justifyContent: "flex-end", padding: "4px 8px 0" }}>
                  <CopyButton text={task.output.join('\n')} label="Copy" />
                </div>
                {task.output.map((line, i) => (
                  <div key={i} className="output-line">
                    <FormattedLine text={line} />
                  </div>
                ))}
              </div>
            ) : (
              <div style={{ color: "var(--text-dim)", fontSize: "13px" }}>No output yet.</div>
            )}
          </div>

          {/* Review Tab */}
          <div className={`tab-content ${activeTab === 'review' ? 'active' : ''}`}>
            {reviewGates.length > 0 ? (
              <div className="review-timeline">
                {reviewGates.map((gate, i) => (
                  <div key={i} className={`review-gate-card ${gate.result === "pass" ? "pass" : "fail"}`}>
                    <div className="review-gate-header">
                      <div className="review-gate-icon">
                        {gate.result === "pass" ? (
                          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="var(--green)" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                          </svg>
                        ) : (
                          <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="var(--red)" strokeWidth={2.5}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                          </svg>
                        )}
                      </div>
                      <h4>{gate.gate}</h4>
                      {gate.details && (
                        <CopyButton text={gate.details} label="Copy" />
                      )}
                    </div>
                    {gate.details && (
                      <div className="review-details">
                        <FormattedLine text={gate.details} />
                      </div>
                    )}
                  </div>
                ))}

                {/* Merge Result */}
                {mergeResult && (
                  <div className={`merge-result-card ${mergeResult.success ? "success" : ""}`}>
                    {mergeResult.success ? (
                      <>
                        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                        </svg>
                        <span>Merged successfully</span>
                        <span className="merge-diff">
                          <span className="stat-add">+{mergeResult.linesAdded ?? 0}</span>
                          {" "}
                          <span className="stat-del">-{mergeResult.linesRemoved ?? 0}</span>
                        </span>
                      </>
                    ) : (
                      <>
                        <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="var(--red)" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                        </svg>
                        <span style={{ color: "var(--red)" }}>Merge failed: {mergeResult.error}</span>
                        {mergeResult.error && (
                          <CopyButton text={mergeResult.error} label="Copy" />
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div style={{ color: "var(--text-dim)", fontSize: "13px" }}>No review gates yet.</div>
            )}
          </div>

          {/* Files Tab */}
          <div className={`tab-content ${activeTab === 'files' ? 'active' : ''}`}>
            {task.files.length > 0 && (
              <div className="files-section">
                <div className="files-heading">
                  Files Changed ({task.files.length})
                  <CopyButton text={task.files.join('\n')} label="Copy" />
                </div>
                {task.files.map((f) => (
                  <div key={f} className="file-row">
                    <span className="file-path">{f}</span>
                  </div>
                ))}
              </div>
            )}
            {task.targetFiles && task.targetFiles.length > 0 && (
              <div className="files-section">
                <div className="files-heading">
                  Target Files ({task.targetFiles.length})
                  <CopyButton text={task.targetFiles.join('\n')} label="Copy" />
                </div>
                {task.targetFiles.map((f) => (
                  <div key={f} className="file-row">
                    <span className="file-path">{f}</span>
                  </div>
                ))}
              </div>
            )}
            {task.files.length === 0 && (!task.targetFiles || task.targetFiles.length === 0) && (
              <div style={{ color: "var(--text-dim)", fontSize: "13px" }}>No files yet.</div>
            )}
          </div>

          {/* Activity Tab */}
          <div className={`tab-content ${activeTab === 'activity' ? 'active' : ''}`}>
            {taskTimeline.length > 0 ? (
              <div className="activity-timeline">
                {taskTimeline.map((ev, i) => {
                  const label = (() => {
                    const p = ev.payload;
                    switch (ev.type) {
                      case "task:state_changed": return `State \u2192 ${p.state}`;
                      case "task:review_update": return `${p.gate} ${p.passed ? "\u2713 passed" : "\u2717 failed"}`;
                      case "task:merge_result": return p.success ? "Merged successfully" : `Merge failed: ${p.error || "unknown"}`;
                      case "task:cost_update": return `Cost: $${(p.cost_usd as number)?.toFixed(4)}`;
                      case "task:files_changed": return `${(p.files as string[])?.length || 0} files changed`;
                      default: return ev.type.split(":")[1] || ev.type;
                    }
                  })();
                  const dotColor = activityDotColor(ev.type);
                  return (
                    <div key={i} className="activity-item">
                      <div className={`activity-dot ${dotColor}`} />
                      <div className="activity-content">
                        <span className="activity-text">{label}</span>
                        <span className="activity-time">
                          {new Date(ev.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
                        </span>
                      </div>
                    </div>
                  );
                })}
              </div>
            ) : (
              <div style={{ color: "var(--text-dim)", fontSize: "13px" }}>No activity yet.</div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}

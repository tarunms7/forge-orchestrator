"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore, useTask } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import { FormattedLine } from "./FormattedLine";
import { CopyButton } from "@/components/CopyButton";

const STATE_CLASS: Record<TaskState["state"], { label: string; cardClass: string; badgeClass: string }> = {
  pending: { label: "Pending", cardClass: "pending", badgeClass: "state-badge pending" },
  working: { label: "Working", cardClass: "working", badgeClass: "state-badge working" },
  in_review: { label: "In Review", cardClass: "in-review", badgeClass: "state-badge review" },
  awaiting_approval: { label: "Awaiting Approval", cardClass: "awaiting-approval", badgeClass: "state-badge awaiting-approval" },
  done: { label: "Done", cardClass: "done", badgeClass: "state-badge done" },
  error: { label: "Error", cardClass: "error-card", badgeClass: "state-badge error" },
  retrying: { label: "Retrying", cardClass: "working", badgeClass: "state-badge retrying" },
  cancelled: { label: "Cancelled", cardClass: "pending", badgeClass: "state-badge pending" },
};

const GATE_LABELS: Record<string, string> = {
  Gate0_Build: "Build",
  L1: "Lint",
  Gate1_5_Test: "Test",
  L2: "Review",
};

function ReviewGateIcon({ result }: { result: string }) {
  if (result === "pass") {
    return (
      <svg className="h-4 w-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (result === "fail") {
    return (
      <svg className="h-4 w-4 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  return (
    <svg className="h-4 w-4 animate-spin text-yellow-400" fill="none" viewBox="0 0 24 24">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
      <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

/* ── Log Modal ─────────────────────────────────────────────────────── */

function LogModal({
  task,
  onClose,
}: {
  task: TaskState;
  onClose: () => void;
}) {
  const bodyRef = useRef<HTMLDivElement>(null);

  // Lock body scroll while modal is open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  // Scroll to bottom on open
  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
    }
  }, []);

  return createPortal(
    <div className="log-modal-overlay" onClick={onClose}>
      <div className="log-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="log-modal-header">
          <div>
            <span className="log-modal-title">{task.title}</span>
            <span className="log-modal-subtitle">{task.output.length} lines</span>
          </div>
          <span onClick={(e) => e.stopPropagation()}>
            <CopyButton
              text={task.output.join("\n")}
              label="Copy"
            />
          </span>
          <button className="log-modal-close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div ref={bodyRef} className="log-modal-body">
          {task.output.map((line, i) => (
            <div key={i} className="log-modal-line">
              <FormattedLine text={line} />
            </div>
          ))}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Agent Card ────────────────────────────────────────────────────── */

/** Format token counts for compact display: 32000 → "32K", 1500000 → "1.5M" */
function formatTokenCount(count: number): string {
  if (count >= 1_000_000) {
    const m = count / 1_000_000;
    return m % 1 === 0 ? `${m}M` : `${m.toFixed(1)}M`;
  }
  if (count >= 1_000) {
    const k = count / 1_000;
    return k % 1 === 0 ? `${k}K` : `${k.toFixed(1)}K`;
  }
  return String(count);
}

const COLLAPSED_LINE_LIMIT = 6;

export default function AgentCard({ taskId, onClick }: { taskId: string; onClick?: () => void }) {
  const task = useTask(taskId);
  const outputRef = useRef<HTMLDivElement>(null);
  const [showModal, setShowModal] = useState(false);
  const pipelineId = useTaskStore((s) => s.pipelineId);
  const token = useAuthStore((s) => s.token);

  // Auto-scroll output to bottom
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [task?.output]);

  if (!task) return null;

  const stateInfo = STATE_CLASS[task.state];

  const showExpand = task.output.length > COLLAPSED_LINE_LIMIT;
  const visibleLines = task.output.slice(-COLLAPSED_LINE_LIMIT);
  const startIndex = Math.max(0, task.output.length - COLLAPSED_LINE_LIMIT);

  return (
    <>
      <div onClick={onClick} className={`task-card ${stateInfo.cardClass}`}>
        {/* Header */}
        <div className="task-card-header">
          <span className="task-card-id">{task.id}</span>
          <span className={stateInfo.badgeClass}>{stateInfo.label}</span>
        </div>

        {/* Title */}
        <div className="task-card-title">{task.title}</div>

        {/* Agent output — scrollable, always shows last N lines */}
        <div className="task-card-output-wrap">
          {task.output.length > 0 ? (
            <>
              <div ref={outputRef} className="task-card-output">
                {visibleLines.map((line, i) => (
                  <div key={`line-${startIndex + i}`} className="output-line">
                    <FormattedLine text={line} />
                  </div>
                ))}
              </div>
              {showExpand && (
                <button
                  className="output-toggle"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowModal(true);
                  }}
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                    <path strokeLinecap="round" strokeLinejoin="round" d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7" />
                  </svg>
                  Show all {task.output.length} lines
                </button>
              )}
            </>
          ) : (
            <div className="task-card-output" style={{ display: "flex", alignItems: "center", justifyContent: "center", color: task.state === "awaiting_approval" ? "var(--amber)" : "var(--text-dim)", fontSize: 11 }}>
              {task.state === "pending" ? "Waiting..." : task.state === "awaiting_approval" ? "⚠ Review and approve to merge" : "No output yet"}
            </div>
          )}
        </div>

        {/* Review gates */}
        {task.reviewGates.length > 0 && (
          <div className="review-gates-detail">
            {task.reviewGates.map((gate, i) => {
              const label = GATE_LABELS[gate.gate] || String(gate.gate);
              return (
                <div
                  key={`${gate.gate}-${i}`}
                  className={`gate-item ${gate.result === "pass" ? "pass" : "pending"}`}
                  title={gate.details}
                >
                  <ReviewGateIcon result={gate.result} />
                  <span>{label}</span>
                </div>
              );
            })}
          </div>
        )}

        {/* Footer — always pinned to bottom */}
        <div className="task-card-footer">
          <div className="review-gates-mini">
            {task.reviewGates.map((gate, i) => (
              <div
                key={`dot-${gate.gate}-${i}`}
                className={`gate-dot ${gate.result === "pass" ? "pass" : gate.result === "fail" ? "fail" : "pending-gate"}`}
                title={`${GATE_LABELS[gate.gate] || gate.gate}: ${gate.result}`}
              />
            ))}
          </div>

          {task.mergeResult && task.mergeResult.success && (
            <span className="merge-stats">
              <span className="stat-add">+{task.mergeResult.linesAdded ?? 0}</span>
              <span className="stat-del">-{task.mergeResult.linesRemoved ?? 0}</span>
            </span>
          )}

          {((task.inputTokens != null && task.inputTokens > 0) ||
            (task.outputTokens != null && task.outputTokens > 0)) && (
            <span className="token-label" title={`Input: ${task.inputTokens ?? 0} / Output: ${task.outputTokens ?? 0}`}>
              {formatTokenCount(task.inputTokens ?? 0)}/{formatTokenCount(task.outputTokens ?? 0)} tok
            </span>
          )}

          {task.costUsd != null && task.costUsd > 0 && (
            <span className="cost-label">${task.costUsd.toFixed(2)}</span>
          )}
        </div>

        {/* Retry for errored tasks */}
        {task.state === "error" && (
          <button
            className="btn btn-primary"
            style={{ width: "100%", marginTop: "8px" }}
            onClick={async (e) => {
              e.stopPropagation();
              if (!pipelineId || !token) return;
              try {
                await apiPost(`/tasks/${pipelineId}/${task.id}/retry`, {}, token);
              } catch (err) {
                console.warn("Retry failed:", err);
              }
            }}
          >
            Retry Task
          </button>
        )}
      </div>

      {/* Full Log Modal */}
      {showModal && (
        <LogModal task={task} onClose={() => setShowModal(false)} />
      )}
    </>
  );
}

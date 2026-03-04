"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiGet, apiPost } from "@/lib/api";
import DiffViewer from "@/components/diff/DiffViewer";

/* ── Full Diff Modal ──────────────────────────────────────────────── */

function FullDiffModal({
  diff,
  stats,
  onClose,
}: {
  diff: string;
  stats?: { files_changed: number; lines_added: number; lines_removed: number };
  onClose: () => void;
}) {
  // Lock body scroll
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return createPortal(
    <div className="log-modal-overlay" onClick={onClose}>
      <div
        className="log-modal"
        onClick={(e) => e.stopPropagation()}
        style={{ maxWidth: 900, width: "95%" }}
      >
        {/* Header */}
        <div className="log-modal-header">
          <div>
            <span className="log-modal-title">Full Diff</span>
            {stats && (
              <span className="log-modal-subtitle">
                {stats.files_changed} file{stats.files_changed !== 1 ? "s" : ""} changed
                {" \u2022 "}
                <span style={{ color: "var(--green)" }}>+{stats.lines_added}</span>
                {" "}
                <span style={{ color: "var(--red)" }}>-{stats.lines_removed}</span>
              </span>
            )}
          </div>
          <button className="log-modal-close" onClick={onClose}>
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M6 18L18 6M6 6l12 12"
              />
            </svg>
          </button>
        </div>

        {/* Body */}
        <div className="log-modal-body" style={{ padding: 16 }}>
          <DiffViewer diff={diff} />
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Approval Panel ───────────────────────────────────────────────── */

export default function ApprovalPanel({ task }: { task: TaskState }) {
  const pipelineId = useTaskStore((s) => s.pipelineId);
  const token = useAuthStore((s) => s.token);
  const handleEvent = useTaskStore((s) => s.handleEvent);

  const [rejectionReason, setRejectionReason] = useState("");
  const [approving, setApproving] = useState(false);
  const [rejecting, setRejecting] = useState(false);
  const [showFullDiff, setShowFullDiff] = useState(false);
  const [fullDiff, setFullDiff] = useState<string | null>(null);
  const [diffStats, setDiffStats] = useState<{
    files_changed: number;
    lines_added: number;
    lines_removed: number;
  } | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [diffError, setDiffError] = useState<string | null>(null);

  // Get the diff preview (first 200 lines)
  const diffPreview = (task as TaskState & { diffPreview?: string }).diffPreview || "";

  async function handleViewFullDiff() {
    if (!token || !pipelineId) return;
    setDiffLoading(true);
    setDiffError(null);
    try {
      const data = await apiGet(
        `/tasks/${pipelineId}/tasks/${task.id}/diff`,
        token,
      );
      setFullDiff(data.diff);
      setDiffStats(data.stats || null);
      setShowFullDiff(true);
    } catch (err) {
      setDiffError(
        err instanceof Error ? err.message : "Failed to load diff",
      );
    } finally {
      setDiffLoading(false);
    }
  }

  async function handleApprove() {
    if (!token || !pipelineId) return;
    setApproving(true);
    try {
      await apiPost(
        `/tasks/${pipelineId}/tasks/${task.id}/approve`,
        {},
        token,
      );
      // Optimistic update: set state to merging
      handleEvent({
        event: "task:state_changed",
        data: { task_id: task.id, state: "merging" },
      });
    } catch (err) {
      console.warn("Approve failed:", err);
    } finally {
      setApproving(false);
    }
  }

  async function handleReject() {
    if (!token || !pipelineId) return;
    setRejecting(true);
    try {
      await apiPost(
        `/tasks/${pipelineId}/tasks/${task.id}/reject`,
        { reason: rejectionReason || undefined },
        token,
      );
      // Optimistic update: set state to retrying (will become todo)
      handleEvent({
        event: "task:state_changed",
        data: { task_id: task.id, state: "todo" },
      });
    } catch (err) {
      console.warn("Reject failed:", err);
    } finally {
      setRejecting(false);
    }
  }

  return (
    <>
      <div
        style={{
          background: "var(--bg-surface-2)",
          border: "1px solid var(--amber)",
          borderRadius: "var(--radius-lg)",
          padding: 20,
        }}
      >
        {/* Header */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            marginBottom: 16,
          }}
        >
          <svg
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="var(--amber)"
            strokeWidth={2}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z"
            />
          </svg>
          <span
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: "var(--amber)",
            }}
          >
            Awaiting Approval
          </span>
        </div>

        {/* Diff preview */}
        {diffPreview && (
          <div style={{ marginBottom: 16 }}>
            <div
              style={{
                fontSize: 12,
                fontWeight: 500,
                color: "var(--text-tertiary)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: 8,
              }}
            >
              Diff Preview
            </div>
            <div
              style={{
                maxHeight: 300,
                overflow: "auto",
                background: "var(--bg-base)",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius-md)",
                padding: 12,
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                lineHeight: 1.6,
                whiteSpace: "pre",
                color: "var(--text-secondary)",
              }}
            >
              {diffPreview.split("\n").map((line, i) => {
                let lineColor = "var(--text-secondary)";
                if (line.startsWith("+")) lineColor = "var(--green)";
                else if (line.startsWith("-")) lineColor = "var(--red)";
                else if (line.startsWith("@@")) lineColor = "var(--accent)";
                else if (line.startsWith("diff ")) lineColor = "var(--text-primary)";
                return (
                  <div key={i} style={{ color: lineColor }}>
                    {line}
                  </div>
                );
              })}
            </div>
            <div style={{ marginTop: 8, textAlign: "right" }}>
              <button
                type="button"
                onClick={handleViewFullDiff}
                disabled={diffLoading}
                className="btn btn-ghost btn-sm"
                style={{
                  fontSize: 12,
                  padding: "4px 12px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  cursor: diffLoading ? "not-allowed" : "pointer",
                  opacity: diffLoading ? 0.5 : 1,
                }}
              >
                {diffLoading ? "Loading..." : "View Full Diff"}
              </button>
            </div>
            {diffError && (
              <div
                style={{
                  marginTop: 8,
                  fontSize: 12,
                  color: "var(--red)",
                  padding: "6px 10px",
                  borderRadius: "var(--radius-sm)",
                  background: "var(--red-dim)",
                  border: "1px solid rgba(239,68,68,0.3)",
                }}
              >
                {diffError}
              </div>
            )}
          </div>
        )}

        {/* No preview available */}
        {!diffPreview && (
          <div
            style={{
              marginBottom: 16,
              padding: 20,
              textAlign: "center",
              background: "var(--bg-surface-3)",
              borderRadius: "var(--radius-md)",
              color: "var(--text-dim)",
              fontSize: 13,
            }}
          >
            No diff preview available.
            <div style={{ marginTop: 8 }}>
              <button
                type="button"
                onClick={handleViewFullDiff}
                disabled={diffLoading}
                className="btn btn-ghost btn-sm"
                style={{
                  fontSize: 12,
                  padding: "4px 12px",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  cursor: diffLoading ? "not-allowed" : "pointer",
                }}
              >
                {diffLoading ? "Loading..." : "Load Full Diff"}
              </button>
            </div>
          </div>
        )}

        {/* Rejection reason */}
        <div style={{ marginBottom: 16 }}>
          <label
            htmlFor={`rejection-reason-${task.id}`}
            style={{
              display: "block",
              fontSize: 12,
              fontWeight: 500,
              color: "var(--text-tertiary)",
              marginBottom: 4,
            }}
          >
            Rejection reason (optional)
          </label>
          <textarea
            id={`rejection-reason-${task.id}`}
            rows={2}
            value={rejectionReason}
            onChange={(e) => setRejectionReason(e.target.value)}
            placeholder="Explain why this should be retried..."
            style={{
              width: "100%",
              padding: "8px 12px",
              fontSize: 13,
              background: "var(--bg-surface-3)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              color: "var(--text-primary)",
              outline: "none",
              resize: "vertical",
            }}
          />
        </div>

        {/* Action buttons */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            gap: 12,
          }}
        >
          <button
            type="button"
            onClick={handleReject}
            disabled={rejecting || approving}
            className="btn"
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: 600,
              background: "var(--red-dim)",
              color: "var(--red)",
              border: "1px solid rgba(239,68,68,0.3)",
              borderRadius: "var(--radius-md)",
              cursor: rejecting || approving ? "not-allowed" : "pointer",
              opacity: rejecting || approving ? 0.5 : 1,
            }}
          >
            {rejecting ? "Rejecting..." : "Reject & Retry"}
          </button>
          <button
            type="button"
            onClick={handleApprove}
            disabled={approving || rejecting}
            className="btn btn-primary btn-glow"
            style={{
              padding: "10px 24px",
              fontSize: 13,
              fontWeight: 600,
              cursor: approving || rejecting ? "not-allowed" : "pointer",
              opacity: approving || rejecting ? 0.5 : 1,
            }}
          >
            {approving ? "Approving..." : "Approve Merge"}
          </button>
        </div>
      </div>

      {/* Full Diff Modal */}
      {showFullDiff && fullDiff !== null && (
        <FullDiffModal
          diff={fullDiff}
          stats={diffStats || undefined}
          onClose={() => setShowFullDiff(false)}
        />
      )}
    </>
  );
}

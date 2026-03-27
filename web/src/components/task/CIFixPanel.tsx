"use client";

import { useState } from "react";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";

const STATUS_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  watching: { label: "Watching CI", color: "var(--amber)", bg: "rgba(245,158,11,0.1)" },
  fixing: { label: "Fixing", color: "var(--accent)", bg: "var(--accent-glow)" },
  passed: { label: "CI Passed", color: "var(--green)", bg: "rgba(52,211,153,0.1)" },
  exhausted: { label: "Retries Exhausted", color: "var(--red)", bg: "rgba(239,68,68,0.1)" },
  cancelled: { label: "Cancelled", color: "var(--text-dim)", bg: "rgba(128,128,128,0.1)" },
  error: { label: "Error", color: "var(--red)", bg: "rgba(239,68,68,0.1)" },
};

function CheckIcon({ conclusion }: { conclusion: string }) {
  if (conclusion === "success" || conclusion === "neutral" || conclusion === "skipped") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--green)" strokeWidth={2.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
      </svg>
    );
  }
  if (conclusion === "failure" || conclusion === "cancelled" || conclusion === "timed_out") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--red)" strokeWidth={2.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
      </svg>
    );
  }
  // Pending/running — spinner
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" className="animate-spin">
      <circle className="opacity-25" cx="12" cy="12" r="10" stroke="var(--amber)" strokeWidth="3" />
      <path className="opacity-75" fill="var(--amber)" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
    </svg>
  );
}

export default function CIFixPanel({ pipelineId }: { pipelineId: string }) {
  const token = useAuthStore((s) => s.token);
  const status = useTaskStore((s) => s.ciFixStatus);
  const attempt = useTaskStore((s) => s.ciFixAttempt);
  const maxRetries = useTaskStore((s) => s.ciFixMaxRetries);
  const costUsd = useTaskStore((s) => s.ciFixCostUsd);
  const checks = useTaskStore((s) => s.ciFixChecks);
  const history = useTaskStore((s) => s.ciFixHistory);

  const [cancelLoading, setCancelLoading] = useState(false);
  const [retryLoading, setRetryLoading] = useState(false);
  const [historyExpanded, setHistoryExpanded] = useState(false);

  if (status === "idle") return null;

  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.watching;
  const isActive = status === "watching" || status === "fixing";

  const handleCancel = async () => {
    if (!token) return;
    setCancelLoading(true);
    try {
      await apiPost(`/tasks/${pipelineId}/ci-fix/cancel`, {}, token);
    } catch (err) {
      console.warn("Cancel CI fix failed:", err);
    } finally {
      setCancelLoading(false);
    }
  };

  const handleRetry = async () => {
    if (!token) return;
    setRetryLoading(true);
    try {
      await apiPost(`/tasks/${pipelineId}/ci-fix`, {}, token);
      useTaskStore.setState({ ciFixStatus: "watching", ciFixAttempt: 0, ciFixChecks: [] });
    } catch (err) {
      console.warn("Retry CI fix failed:", err);
    } finally {
      setRetryLoading(false);
    }
  };

  return (
    <div
      style={{
        border: `1px solid ${cfg.color}33`,
        borderRadius: "var(--radius-md)",
        padding: "16px 20px",
        marginTop: 16,
        background: cfg.bg,
      }}
    >
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {isActive && (
            <span
              style={{
                width: 8,
                height: 8,
                borderRadius: "50%",
                background: cfg.color,
                display: "inline-block",
                animation: "pulse 2s infinite",
              }}
            />
          )}
          <h3 style={{ margin: 0, fontSize: 15, fontWeight: 600, color: "var(--text)" }}>
            CI Auto-Fix
          </h3>
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "2px 8px",
              borderRadius: "var(--radius-sm)",
              color: cfg.color,
              border: `1px solid ${cfg.color}44`,
              background: cfg.bg,
              textTransform: "uppercase",
              letterSpacing: "0.5px",
            }}
          >
            {cfg.label}
          </span>
          {isActive && attempt > 0 && (
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Attempt {attempt}/{maxRetries}
            </span>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {costUsd > 0 && (
            <span style={{ fontSize: 12, color: "var(--text-dim)" }}>
              ${costUsd.toFixed(2)}
            </span>
          )}
          {isActive && (
            <button
              type="button"
              onClick={handleCancel}
              disabled={cancelLoading}
              className="btn btn-ghost"
              style={{ fontSize: 12, padding: "4px 10px" }}
            >
              {cancelLoading ? "Cancelling..." : "Cancel"}
            </button>
          )}
          {(status === "exhausted" || status === "error" || status === "cancelled") && (
            <button
              type="button"
              onClick={handleRetry}
              disabled={retryLoading}
              className="btn btn-ghost"
              style={{ fontSize: 12, padding: "4px 10px", borderColor: "var(--accent)" }}
            >
              {retryLoading ? "Starting..." : "Retry CI Fix"}
            </button>
          )}
        </div>
      </div>

      {/* Live CI Checks */}
      {checks.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {checks.map((check) => (
              <div
                key={check.name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  padding: "4px 8px",
                  borderRadius: "var(--radius-sm)",
                  background: "rgba(0,0,0,0.15)",
                  fontSize: 13,
                }}
              >
                <CheckIcon conclusion={check.conclusion || check.status} />
                <span style={{ color: "var(--text)" }}>{check.name}</span>
                <span style={{ color: "var(--text-dim)", fontSize: 11, marginLeft: "auto" }}>
                  {check.conclusion || check.status}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Attempt History */}
      {history.length > 0 && (
        <div>
          <button
            type="button"
            onClick={() => setHistoryExpanded(!historyExpanded)}
            style={{
              background: "none",
              border: "none",
              color: "var(--text-dim)",
              fontSize: 12,
              cursor: "pointer",
              padding: 0,
              display: "flex",
              alignItems: "center",
              gap: 4,
            }}
          >
            <svg
              width="10"
              height="10"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              style={{ transform: historyExpanded ? "rotate(90deg)" : "none", transition: "transform 0.15s" }}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
            {history.length} attempt{history.length !== 1 ? "s" : ""}
          </button>
          {historyExpanded && (
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
              {history.map((h, i) => (
                <div
                  key={i}
                  style={{
                    padding: "6px 10px",
                    borderRadius: "var(--radius-sm)",
                    background: "rgba(0,0,0,0.1)",
                    fontSize: 12,
                    borderLeft: `2px solid ${h.status === "fix_pushed" ? "var(--green)" : "var(--red)"}`,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                    <span style={{ fontWeight: 500, color: "var(--text)" }}>
                      {h.status === "fix_pushed" ? `Fix pushed (attempt ${h.attempt})` : `CI failed (attempt ${h.attempt})`}
                    </span>
                    {h.costUsd > 0 && (
                      <span style={{ color: "var(--text-dim)" }}>${h.costUsd.toFixed(2)}</span>
                    )}
                  </div>
                  {h.failedChecks.length > 0 && (
                    <div style={{ color: "var(--text-dim)" }}>
                      Failed: {h.failedChecks.join(", ")}
                    </div>
                  )}
                  {h.fixSummary && (
                    <div style={{ color: "var(--text-dim)", marginTop: 2 }}>
                      {h.fixSummary.slice(0, 150)}{h.fixSummary.length > 150 ? "..." : ""}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

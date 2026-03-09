"use client";

import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useWebSocket } from "@/hooks/useWebSocket";
import type { WsStatus } from "@/hooks/useWebSocket";
import { useTaskStore } from "@/stores/taskStore";
import type { TaskState } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiGet, apiPost } from "@/lib/api";
import AgentCard from "@/components/task/AgentCard";
import PipelineProgress from "@/components/task/PipelineProgress";
import PlannerCard from "@/components/task/PlannerCard";
import CompletionSummary from "@/components/task/CompletionSummary";
import TaskDetailPanel from "@/components/task/TaskDetailPanel";
import FollowUpPanel from "@/components/task/FollowUpPanel";
import EditablePlanPanel from "@/components/task/EditablePlanPanel";
import ApprovalPanel from "@/components/task/ApprovalPanel";
import ContractsPanel from "@/components/task/ContractsPanel";
import { CopyButton } from "@/components/CopyButton";
import { pausePipeline, resumePipeline } from "@/lib/api";

/* ── Plan Panel ───────────────────────────────────────────────────── */

function PlanTaskCard({ task, allTasks }: { task: TaskState; allTasks: TaskState[] }) {
  const [open, setOpen] = useState(false);

  // Resolve dependency IDs to short labels (e.g. "task-1")
  const depIds = task.dependsOn ?? [];
  // Resolve dependency names for expanded view
  const depNames = depIds.map((depId) => {
    const dep = allTasks.find((t) => t.id === depId);
    return dep ? dep.title : depId;
  });

  // Extract short suffix for dependency pills (e.g. "9df8148-task-2" → "task-2")
  const depShortLabels = depIds.map((depId) => {
    const parts = depId.split("-");
    // Take last two segments (e.g. "task-2")
    return parts.length >= 2 ? parts.slice(-2).join("-") : depId;
  });

  return (
    <div className="plan-task-card" onClick={() => setOpen(!open)} style={{ cursor: "pointer" }}>
      <div className="plan-task-header">
        <div className="plan-task-left">
          <svg
            className={`transition-transform ${open ? "rotate-90" : ""}`}
            style={{ width: 14, height: 14, color: "var(--text-tertiary)", flexShrink: 0 }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <span className="task-number">{task.id}</span>
          <span className="plan-task-title">{task.title}</span>
        </div>
        <div className="plan-task-right">
          {/* Dependency pills — shown on collapsed row */}
          {depShortLabels.length > 0 && (
            <div className="dep-pills">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} style={{ color: "var(--text-dim)", flexShrink: 0 }}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101" />
                <path strokeLinecap="round" strokeLinejoin="round" d="M10.172 13.828a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.102 1.101" />
              </svg>
              {depShortLabels.map((label) => (
                <span key={label} className="dep-pill">{label}</span>
              ))}
            </div>
          )}
          <span className={`complexity-badge ${task.complexity ?? "medium"}`}>
            {task.complexity ?? "medium"}
          </span>
        </div>
      </div>

      {open && (
        <div style={{ paddingTop: 8 }}>
          {/* Description */}
          {task.description && (
            <div className="plan-task-desc">{task.description}</div>
          )}

          {/* Target files & Dependencies */}
          <div className="plan-task-meta">
            {task.targetFiles && task.targetFiles.length > 0 && (
              <span className="meta-item">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                </svg>
                {task.targetFiles.length} file{task.targetFiles.length !== 1 ? "s" : ""}
              </span>
            )}
            {depNames.length > 0 && (
              <span className="meta-item depends">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M13 10V3L4 14h7v7l9-11h-7z" />
                </svg>
                Depends: {depNames.join(", ")}
              </span>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function PlanPanel({
  taskList,
  phase,
  executing,
  onExecute,
}: {
  taskList: TaskState[];
  phase: string;
  executing: boolean;
  onExecute: () => void;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const isPlanned = phase === "planned";

  if (taskList.length === 0) return null;

  return (
    <div className="plan-review-container mb-8">
      <div className="plan-header">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-2 text-left"
          style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
        >
          <svg
            className={`transition-transform ${collapsed ? "" : "rotate-90"}`}
            style={{ width: 16, height: 16, color: "var(--text-tertiary)" }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <h2 className="section-title">
            Plan — {taskList.length} task{taskList.length !== 1 ? "s" : ""}
          </h2>
        </button>
        {isPlanned && (
          <button
            type="button"
            onClick={onExecute}
            disabled={executing}
            className="btn btn-primary btn-glow"
            style={executing ? { opacity: 0.4, cursor: "not-allowed" } : {}}
          >
            {executing ? "Starting..." : "Execute Plan"}
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="plan-tasks">
          {taskList.map((task) => (
            <PlanTaskCard key={task.id} task={task} allTasks={taskList} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Connection Status Banner ─────────────────────────────────────── */

function ConnectionBanner({ status, onRetry }: { status: WsStatus; onRetry?: () => void }) {
  if (status === "connected") return null;

  const messages: Record<string, { text: string; bg: string; border: string; color: string }> = {
    connecting: {
      text: "Connecting to server...",
      bg: "var(--accent-glow)",
      border: "rgba(59,130,246,0.3)",
      color: "var(--accent)",
    },
    reconnecting: {
      text: "Reconnecting...",
      bg: "var(--amber-dim)",
      border: "rgba(245,158,11,0.3)",
      color: "var(--amber)",
    },
    disconnected: {
      text: "Connection lost.",
      bg: "var(--red-dim)",
      border: "rgba(239,68,68,0.3)",
      color: "var(--red)",
    },
  };

  const msg = messages[status];
  return (
    <div
      style={{
        marginBottom: 16,
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        borderRadius: "var(--radius-md)",
        border: `1px solid ${msg.border}`,
        background: msg.bg,
        padding: "10px 16px",
        fontSize: 13,
        fontWeight: 500,
        color: msg.color,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        {status !== "disconnected" && (
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
        )}
        {msg.text}
      </div>
      {status === "disconnected" && onRetry && (
        <button
          onClick={onRetry}
          className="btn btn-sm"
          style={{
            background: "var(--red-dim)",
            border: "1px solid rgba(239,68,68,0.3)",
            color: "var(--red)",
            fontSize: 12,
            padding: "4px 12px",
            borderRadius: "var(--radius-sm)",
            cursor: "pointer",
          }}
        >
          Retry Connection
        </button>
      )}
    </div>
  );
}

/* ── Confirmation Dialog ──────────────────────────────────────────── */

function ConfirmDialog({
  title,
  message,
  confirmLabel,
  confirmColor,
  onConfirm,
  onCancel,
}: {
  title: string;
  message: string;
  confirmLabel: string;
  confirmColor: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  // Lock body scroll while dialog is open
  useEffect(() => {
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, []);

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onCancel]);

  return createPortal(
    <div
      className="log-modal-overlay"
      onClick={onCancel}
      style={{ zIndex: 9999 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-surface-2)",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border)",
          padding: 24,
          maxWidth: 420,
          width: "90%",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        <h3 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8 }}>
          {title}
        </h3>
        <p style={{ fontSize: 13, color: "var(--text-secondary)", lineHeight: 1.5, marginBottom: 20 }}>
          {message}
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            type="button"
            onClick={onCancel}
            className="btn btn-ghost"
            style={{
              padding: "8px 16px",
              fontSize: 13,
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
            }}
          >
            Go Back
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="btn"
            style={{
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              background: confirmColor,
              color: "white",
              borderRadius: "var(--radius-md)",
              border: "none",
              cursor: "pointer",
            }}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Cancelled Banner ─────────────────────────────────────────────── */

function CancelledBanner({ onRestart }: { onRestart: () => void }) {
  return (
    <div
      style={{
        marginBottom: 24,
        borderRadius: "var(--radius-lg)",
        border: "1px solid rgba(107,114,128,0.3)",
        background: "rgba(107,114,128,0.08)",
        padding: "20px 24px",
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        gap: 16,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <svg style={{ width: 24, height: 24, color: "var(--text-tertiary)", flexShrink: 0 }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M18.364 18.364A9 9 0 005.636 5.636m12.728 12.728A9 9 0 015.636 5.636m12.728 12.728L5.636 5.636" />
        </svg>
        <div>
          <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)" }}>
            Pipeline Cancelled
          </div>
          <div style={{ fontSize: 13, color: "var(--text-tertiary)", marginTop: 2 }}>
            This pipeline was cancelled. You can restart it to begin fresh planning.
          </div>
        </div>
      </div>
      <button
        type="button"
        onClick={onRestart}
        className="btn btn-primary"
        style={{ padding: "8px 20px", fontSize: 13, fontWeight: 600, flexShrink: 0 }}
      >
        Restart Pipeline
      </button>
    </div>
  );
}

/* ── Description Modal ────────────────────────────────────────────── */

function DescriptionModal({
  description,
  onClose,
}: {
  description: string;
  onClose: () => void;
}) {
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

  return createPortal(
    <div className="log-modal-overlay" onClick={onClose}>
      <div className="log-modal" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="log-modal-header">
          <div>
            <span className="log-modal-title">Pipeline Description</span>
          </div>
          <div className="log-modal-header-actions">
            <CopyButton text={description} variant="default" label="Copy" />
            <button className="log-modal-close" onClick={onClose}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="log-modal-body desc-modal-body">
          {description}
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Main Page ────────────────────────────────────────────────────── */

export default function TaskExecutionPage() {
  return (
    <Suspense fallback={
      <div className="phase-content" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "80vh", color: "var(--text-tertiary)" }}>
        Loading...
      </div>
    }>
      <TaskExecutionPageInner />
    </Suspense>
  );
}

function TaskExecutionPageInner() {
  const searchParams = useSearchParams();
  const pipelineId = searchParams.get("id") ?? "";

  const token = useAuthStore((s) => s.token);
  const phase = useTaskStore((s) => s.phase);
  const tasks = useTaskStore((s) => s.tasks);
  const storePipelineId = useTaskStore((s) => s.pipelineId);
  const setPipelineId = useTaskStore((s) => s.setPipelineId);
  const handleEvent = useTaskStore((s) => s.handleEvent);
  const hydrateFromRest = useTaskStore((s) => s.hydrateFromRest);
  const reset = useTaskStore((s) => s.reset);
  const hydrationError = useTaskStore((s) => s.hydrationError);
  const setHydrationError = useTaskStore((s) => s.setHydrationError);
  const storeCancelPipeline = useTaskStore((s) => s.cancelPipeline);
  const storeRestartPipeline = useTaskStore((s) => s.restartPipeline);
  const editedTasks = useTaskStore((s) => s.editedTasks);
  const planValidation = useTaskStore((s) => s.planValidation);
  const applyEditedTasks = useTaskStore((s) => s.applyEditedTasks);

  const [executing, setExecuting] = useState(false);
  const [pauseLoading, setPauseLoading] = useState(false);
  const [resumeLoading, setResumeLoading] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pipelineDesc, setPipelineDesc] = useState<string | null>(null);
  const [showDescModal, setShowDescModal] = useState(false);
  const [showCancelDialog, setShowCancelDialog] = useState(false);
  const [showRestartDialog, setShowRestartDialog] = useState(false);
  const [cancelLoading, setCancelLoading] = useState(false);
  const [restartLoading, setRestartLoading] = useState(false);
  const hydrated = useRef(false);

  // Request browser notification permission on mount
  useEffect(() => {
    if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }, []);

  // Reset store when navigating to a different pipeline
  useEffect(() => {
    if (!pipelineId) return;
    if (storePipelineId && storePipelineId !== pipelineId) {
      // Navigated to a different pipeline — clear stale data
      reset();
      hydrated.current = false;
      setSelectedTaskId(null);
    }
    setPipelineId(pipelineId);
  }, [pipelineId, storePipelineId, setPipelineId, reset]);

  // Hydrate initial state from REST (handles page refresh, late WS connect)
  useEffect(() => {
    if (!token || !pipelineId || hydrated.current) return;
    hydrated.current = true;
    setHydrationError(null);
    apiGet(`/tasks/${pipelineId}`, token)
      .then((data) => {
        hydrateFromRest(data);
      })
      .catch((err) => {
        setHydrationError(err.message || "Failed to load pipeline");
      });

    // Fetch pipeline description from history endpoint
    apiGet(`/history/${pipelineId}`, token)
      .then((data) => {
        if (data.description) setPipelineDesc(data.description);
      })
      .catch(() => {}); // non-critical
  }, [token, pipelineId, hydrateFromRest, setHydrationError]);

  // Close detail panel on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setSelectedTaskId(null);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  const onMessage = useCallback(
    (raw: unknown) => {
      const msg = raw as Record<string, unknown>;
      const { type, ...data } = msg;
      handleEvent({ event: type as string, data });
    },
    [handleEvent],
  );

  const { status: wsStatus, retry: wsRetry } = useWebSocket(pipelineId, token, onMessage);

  const taskList = Object.values(tasks);

  async function handleExecute() {
    if (!token || !pipelineId) return;
    // Block execution if plan validation failed
    if (editedTasks && !planValidation.valid) return;
    setExecuting(true);
    try {
      // Sync edited values (complexity, title, files) into the main tasks
      // store so they display correctly during execution.
      if (editedTasks) applyEditedTasks();
      const body = editedTasks ? { tasks: editedTasks } : {};
      await apiPost(`/tasks/${pipelineId}/execute`, body, token);
    } catch {
      // errors will surface via WS events
    } finally {
      setExecuting(false);
    }
  }

  async function handlePause() {
    if (!token || !pipelineId) return;
    setPauseLoading(true);
    try {
      await pausePipeline(pipelineId, token);
    } catch (e) {
      console.warn("Pause failed:", e);
    } finally {
      setPauseLoading(false);
    }
  }

  async function handleResume() {
    if (!token || !pipelineId) return;
    setResumeLoading(true);
    try {
      await resumePipeline(pipelineId, token);
    } catch (e) {
      console.warn("Resume failed:", e);
    } finally {
      setResumeLoading(false);
    }
  }

  async function handleCancel() {
    if (!token || !pipelineId) return;
    setCancelLoading(true);
    setShowCancelDialog(false);
    try {
      await storeCancelPipeline(token);
    } catch (e) {
      console.warn("Cancel failed:", e);
    } finally {
      setCancelLoading(false);
    }
  }

  async function handleRestart() {
    if (!token || !pipelineId) return;
    setRestartLoading(true);
    setShowRestartDialog(false);
    try {
      await storeRestartPipeline(token);
    } catch (e) {
      console.warn("Restart failed:", e);
    } finally {
      setRestartLoading(false);
    }
  }

  if (!pipelineId) {
    return (
      <div className="phase-content" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "80vh", color: "var(--text-tertiary)" }}>
        No pipeline ID provided.
      </div>
    );
  }

  // Determine if we should show the plan panel (always, once tasks exist)
  const hasTasks = taskList.length > 0;
  // Show agent cards during execution/review/complete/cancelled/paused phases
  const showAgentCards = phase !== "idle" && phase !== "planning" && phase !== "planned" && phase !== "contracts" && hasTasks;
  const isCancelled = phase === "cancelled";
  const isPaused = phase === "paused";
  // Phases where cancel button is shown
  const showCancelButton = phase === "idle" || phase === "planning" || phase === "planned" || phase === "contracts" || phase === "executing" || phase === "reviewing" || phase === "paused";
  // Phases where restart button is shown
  const showRestartButton = isCancelled || (phase === "complete" && taskList.some(t => t.state === "error"));
  // Tasks awaiting approval
  const awaitingApprovalTasks = taskList.filter(t => t.state === "awaiting_approval");

  return (
    <div className="phase-content">
      {/* Pipeline Header */}
      <div className="pipeline-header" style={{ margin: "-28px -32px 0", paddingBottom: 0 }}>
        <div className="pipeline-header-top">
          <Link href="/" className="back-btn">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
              <path d="M10 12l-4-4 4-4" stroke="currentColor" strokeWidth="1.5" fill="none" />
            </svg>
            Back
          </Link>
          <div className="pipeline-meta-wrap">
            <div className="pipeline-meta">
              <h1 className="pipeline-title">Pipeline</h1>
              <span className="id-badge">{pipelineId.slice(0, 8)}</span>
            </div>
            {pipelineDesc && (
              <div className="pipeline-desc-row">
                <p className="pipeline-desc">{pipelineDesc}</p>
                <button
                  className="pipeline-desc-more"
                  onClick={(e) => {
                    e.preventDefault();
                    setShowDescModal(true);
                  }}
                >
                  view more
                </button>
              </div>
            )}
          </div>
        </div>
        {/* Progress track inside header */}
        <PipelineProgress phase={phase} />
      </div>

      {/* Connection Status */}
      <ConnectionBanner status={wsStatus} onRetry={wsRetry} />

      {/* Hydration Error — pipeline not found or failed to load */}
      {hydrationError && (
        <div style={{
          marginBottom: 24,
          borderRadius: "var(--radius-lg)",
          border: "1px solid rgba(239,68,68,0.3)",
          background: "var(--red-dim)",
          padding: 24,
          textAlign: "center",
        }}>
          <svg style={{ margin: "0 auto 12px", width: 32, height: 32, color: "var(--red)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z" />
          </svg>
          <p style={{ fontSize: 13, fontWeight: 500, color: "var(--red)" }}>{hydrationError}</p>
          <Link
            href="/"
            className="btn btn-sm"
            style={{
              marginTop: 12,
              display: "inline-block",
              background: "var(--red-dim)",
              border: "1px solid rgba(239,68,68,0.3)",
              color: "var(--red)",
              fontSize: 12,
              padding: "6px 16px",
              borderRadius: "var(--radius-sm)",
            }}
          >
            Back to Dashboard
          </Link>
        </div>
      )}

      {/* Cancelled Banner */}
      {isCancelled && (
        <CancelledBanner onRestart={() => setShowRestartDialog(true)} />
      )}

      {/* Planner Card — shown during planning phase */}
      <PlannerCard />

      {/* Plan Panel — editable during planned phase, read-only otherwise */}
      {hasTasks && phase === "planned" ? (
        <EditablePlanPanel />
      ) : hasTasks ? (
        <PlanPanel
          taskList={taskList}
          phase={phase}
          executing={executing}
          onExecute={handleExecute}
        />
      ) : null}

      {/* Contracts Building Indicator */}
      {phase === "contracts" && (
        <div className="planner-card mb-8 active" style={{ marginTop: 16 }}>
          <div className="planner-header">
            <div className="planner-status-icon active">
              <svg className="animate-spin" width="14" height="14" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            </div>
            <span className="planner-title">Building Contracts</span>
          </div>
          <div className="planner-body" style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span className="terminal-line active">
              Generating cross-task API and type contracts from integration hints...
            </span>
          </div>
        </div>
      )}

      {/* Pipeline Status Banner */}
      {showAgentCards && (
        <div className="exec-header">
          <div style={{ display: "flex", alignItems: "center" }}>
            {!isCancelled && !isPaused && <span className="live-dot"></span>}
            {isPaused && (
              <svg className="h-4 w-4" fill="currentColor" viewBox="0 0 24 24" style={{ color: "var(--amber)", marginRight: 8 }}>
                <rect x="6" y="4" width="4" height="16" rx="1" />
                <rect x="14" y="4" width="4" height="16" rx="1" />
              </svg>
            )}
            <span className="section-title" style={{ fontSize: 16, color: isPaused ? "var(--amber)" : undefined }}>
              {isCancelled
                ? "Cancelled"
                : isPaused
                  ? "Paused"
                  : phase === "executing"
                    ? "Executing"
                    : phase === "reviewing"
                      ? "Reviewing"
                      : "Complete"}
            </span>
            <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>
              {taskList.filter(t => t.state === "done").length}/{taskList.length} tasks
            </span>
          </div>
        </div>
      )}

      {/* Agent Cards Grid — shown during execution */}
      {showAgentCards ? (
        <div className={`task-grid ${isCancelled ? "opacity-50" : ""}`} style={isCancelled ? { pointerEvents: "none" } : {}}>
          {taskList.map((task) => (
            <AgentCard key={task.id} task={task} onClick={isCancelled ? undefined : () => setSelectedTaskId(task.id)} />
          ))}
        </div>
      ) : (
        !hasTasks &&
        (phase === "idle" || phase === "planning") && (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: 256,
          }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ marginBottom: 8, fontSize: 18, color: "var(--text-tertiary)" }}>
                {phase === "planning" ? "Planning in progress..." : "Starting pipeline..."}
              </div>
              <div style={{
                height: 6,
                width: 192,
                overflow: "hidden",
                borderRadius: 9999,
                background: "var(--bg-surface-3)",
              }}>
                <div className="animate-pulse" style={{
                  height: "100%",
                  width: "33%",
                  borderRadius: 9999,
                  background: "var(--accent)",
                }} />
              </div>
            </div>
          </div>
        )
      )}

      {/* Approval Panels — shown for tasks awaiting approval */}
      {awaitingApprovalTasks.length > 0 && (
        <div style={{ marginTop: 24 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="var(--amber)" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 9v3.75m-9.303 3.376c-.866 1.5.217 3.374 1.948 3.374h14.71c1.73 0 2.813-1.874 1.948-3.374L13.949 3.378c-.866-1.5-3.032-1.5-3.898 0L2.697 16.126zM12 15.75h.007v.008H12v-.008z" />
            </svg>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--amber)" }}>
              {awaitingApprovalTasks.length} Task{awaitingApprovalTasks.length !== 1 ? "s" : ""} Awaiting Approval
            </span>
          </div>
          {awaitingApprovalTasks.map((task) => (
            <div key={task.id} style={{ marginBottom: 16 }}>
              <div style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)", marginBottom: 8 }}>
                <span style={{ color: "var(--text-tertiary)", marginRight: 6 }}>{task.id}</span>
                {task.title}
              </div>
              <ApprovalPanel task={task} />
            </div>
          ))}
        </div>
      )}

      {/* Contracts Panel — shown during phases where contracts may exist */}
      {(phase === "executing" || phase === "reviewing" || phase === "complete" || phase === "paused" || phase === "error") && token && (
        <ContractsPanel pipelineId={pipelineId} token={token} />
      )}

      {/* Pause / Resume / Cancel / Restart Buttons */}
      <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 12 }}>
        {/* Pause button — during executing phase */}
        {phase === "executing" && (
          <button
            onClick={handlePause}
            disabled={pauseLoading}
            className="btn"
            style={{
              background: "var(--amber-dim, rgba(245,158,11,0.1))",
              color: "var(--amber)",
              border: "1px solid rgba(245,158,11,0.3)",
              padding: "10px 24px",
              fontWeight: 600,
              opacity: pauseLoading ? 0.5 : 1,
              cursor: pauseLoading ? "not-allowed" : "pointer",
            }}
          >
            {pauseLoading ? "Pausing..." : "Pause Pipeline"}
          </button>
        )}

        {/* Resume button — during paused phase */}
        {isPaused && (
          <button
            onClick={handleResume}
            disabled={resumeLoading}
            className="btn btn-primary btn-glow"
            style={{
              padding: "10px 24px",
              fontWeight: 600,
              opacity: resumeLoading ? 0.5 : 1,
              cursor: resumeLoading ? "not-allowed" : "pointer",
            }}
          >
            {resumeLoading ? "Resuming..." : "Resume Pipeline"}
          </button>
        )}

        {showCancelButton && (
          <button
            onClick={() => setShowCancelDialog(true)}
            disabled={cancelLoading}
            className="btn"
            style={{
              background: "var(--red)",
              color: "white",
              padding: "10px 24px",
              fontWeight: 600,
              opacity: cancelLoading ? 0.5 : 1,
              cursor: cancelLoading ? "not-allowed" : "pointer",
            }}
          >
            {cancelLoading ? "Cancelling..." : "Cancel Pipeline"}
          </button>
        )}

        {showRestartButton && (
          <button
            onClick={() => setShowRestartDialog(true)}
            disabled={restartLoading}
            className="btn btn-primary btn-glow"
            style={{
              padding: "10px 24px",
              fontWeight: 600,
              opacity: restartLoading ? 0.5 : 1,
              cursor: restartLoading ? "not-allowed" : "pointer",
            }}
          >
            {restartLoading ? "Restarting..." : (isCancelled ? "Restart Pipeline" : "Start Over (Re-plan)")}
          </button>
        )}
      </div>

      {/* Retry Failed Tasks — shown only when pipeline is complete with errored tasks */}
      {phase === "complete" && taskList.some(t => t.state === "error") && (
        <div style={{ marginTop: 8, display: "flex", justifyContent: "center", gap: 12 }}>
          <button
            onClick={async () => {
              if (!token || !pipelineId) return;
              try {
                await apiPost(`/tasks/${pipelineId}/resume`, {}, token);
                // Re-fetch state — WebSocket will also deliver updates
                const data = await apiGet(`/tasks/${pipelineId}`, token);
                hydrateFromRest(data);
              } catch (e) {
                console.warn("Resume failed:", e);
              }
            }}
            className="btn btn-primary btn-glow"
            style={{ padding: "10px 24px", fontWeight: 600 }}
          >
            Retry {taskList.filter(t => t.state === "error").length} Failed Task{taskList.filter(t => t.state === "error").length !== 1 ? "s" : ""}
          </button>
        </div>
      )}

      {/* Completion Summary */}
      {phase === "complete" && (
        <div style={{ marginTop: 32 }}>
          <CompletionSummary tasks={tasks} pipelineId={pipelineId} />
        </div>
      )}

      {/* Follow-Up Panel — shown after completion */}
      {phase === "complete" && (
        <FollowUpPanel pipelineId={pipelineId} />
      )}

      {/* Task Detail Slide-out Panel */}
      {selectedTaskId && tasks[selectedTaskId] && (
        <TaskDetailPanel
          task={tasks[selectedTaskId]}
          onClose={() => setSelectedTaskId(null)}
        />
      )}

      {/* Description Modal */}
      {showDescModal && pipelineDesc && (
        <DescriptionModal
          description={pipelineDesc}
          onClose={() => setShowDescModal(false)}
        />
      )}

      {/* Cancel Confirmation Dialog */}
      {showCancelDialog && (
        <ConfirmDialog
          title="Cancel Pipeline"
          message="Are you sure you want to cancel this pipeline? All running tasks will be stopped. You can restart the pipeline later to begin fresh planning."
          confirmLabel="Cancel Pipeline"
          confirmColor="var(--red)"
          onConfirm={handleCancel}
          onCancel={() => setShowCancelDialog(false)}
        />
      )}

      {/* Restart Confirmation Dialog */}
      {showRestartDialog && (
        <ConfirmDialog
          title="Restart Pipeline"
          message="This will start fresh planning. All previous work will be discarded. Are you sure you want to restart?"
          confirmLabel="Restart Pipeline"
          confirmColor="var(--accent)"
          onConfirm={handleRestart}
          onCancel={() => setShowRestartDialog(false)}
        />
      )}
    </div>
  );
}

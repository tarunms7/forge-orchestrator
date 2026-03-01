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

/* ── Plan Panel ───────────────────────────────────────────────────── */

function PlanTaskCard({ task, allTasks }: { task: TaskState; allTasks: TaskState[] }) {
  const [open, setOpen] = useState(false);

  // Resolve dependency names
  const depNames = (task.dependsOn ?? []).map((depId) => {
    const dep = allTasks.find((t) => t.id === depId);
    return dep ? dep.title : depId;
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
        <span className={`complexity-badge ${task.complexity ?? "medium"}`}>
          {task.complexity ?? "medium"}
        </span>
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
          <button className="log-modal-close" onClick={onClose}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
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

  const [executing, setExecuting] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [pipelineDesc, setPipelineDesc] = useState<string | null>(null);
  const [showDescModal, setShowDescModal] = useState(false);
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
        if (data.tasks?.length > 0 || data.phase !== "planning") {
          hydrateFromRest(data);
        }
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
    setExecuting(true);
    try {
      await apiPost(`/tasks/${pipelineId}/execute`, {}, token);
    } catch {
      // errors will surface via WS events
    } finally {
      setExecuting(false);
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
  // Show agent cards during execution/review/complete phases
  const showAgentCards = phase !== "idle" && phase !== "planning" && phase !== "planned" && hasTasks;

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

      {/* Planner Card — shown during planning phase */}
      <PlannerCard />

      {/* Plan Panel — shown whenever tasks exist (planned, executing, complete) */}
      {hasTasks && (
        <PlanPanel
          taskList={taskList}
          phase={phase}
          executing={executing}
          onExecute={handleExecute}
        />
      )}

      {/* Pipeline Status Banner */}
      {showAgentCards && (
        <div className="exec-header">
          <div style={{ display: "flex", alignItems: "center" }}>
            <span className="live-dot"></span>
            <span className="section-title" style={{ fontSize: 16 }}>
              {phase === "executing" ? "Executing" : phase === "reviewing" ? "Reviewing" : "Complete"}
            </span>
            <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>
              {taskList.filter(t => t.state === "done").length}/{taskList.length} tasks
            </span>
          </div>
        </div>
      )}

      {/* Agent Cards Grid — shown during execution */}
      {showAgentCards ? (
        <div className="task-grid">
          {taskList.map((task) => (
            <AgentCard key={task.id} task={task} onClick={() => setSelectedTaskId(task.id)} />
          ))}
        </div>
      ) : (
        !hasTasks &&
        phase === "idle" && (
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            height: 256,
            borderRadius: "var(--radius-lg)",
            border: "1px solid var(--border)",
            background: "var(--bg-surface-1)",
          }}>
            <div style={{ textAlign: "center" }}>
              <div style={{ marginBottom: 8, fontSize: 18, color: "var(--text-tertiary)" }}>
                Waiting for pipeline to start...
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

      {/* Cancel Button — shown only during active execution */}
      {phase === "executing" && (
        <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 12 }}>
          <button
            onClick={async () => {
              if (!token || !pipelineId) return;
              if (!confirm("Cancel all running tasks?")) return;
              try {
                await apiPost(`/tasks/${pipelineId}/cancel`, {}, token);
                // Re-fetch state — WebSocket will also deliver updates
                const data = await apiGet(`/tasks/${pipelineId}`, token);
                hydrateFromRest(data);
              } catch (e) {
                console.warn("Cancel failed:", e);
              }
            }}
            className="btn"
            style={{
              background: "var(--red)",
              color: "white",
              padding: "10px 24px",
              fontWeight: 600,
            }}
          >
            Cancel Pipeline
          </button>
        </div>
      )}

      {/* Resume Button — shown only when pipeline is complete with errored tasks */}
      {phase === "complete" && taskList.some(t => t.state === "error") && (
        <div style={{ marginTop: 16, display: "flex", justifyContent: "center", gap: 12 }}>
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
            Resume Pipeline
          </button>
        </div>
      )}

      {/* Completion Summary */}
      {phase === "complete" && (
        <div style={{ marginTop: 32 }}>
          <CompletionSummary tasks={tasks} pipelineId={pipelineId} />
        </div>
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
    </div>
  );
}

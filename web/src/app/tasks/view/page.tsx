"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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

const COMPLEXITY_COLORS: Record<string, string> = {
  low: "bg-green-900/50 text-green-300 border-green-800",
  medium: "bg-yellow-900/50 text-yellow-300 border-yellow-800",
  high: "bg-red-900/50 text-red-300 border-red-800",
};

function PlanTaskCard({ task, allTasks }: { task: TaskState; allTasks: TaskState[] }) {
  const [open, setOpen] = useState(false);
  const complexityStyle = COMPLEXITY_COLORS[task.complexity ?? "medium"] ?? COMPLEXITY_COLORS.medium;

  // Resolve dependency names
  const depNames = (task.dependsOn ?? []).map((depId) => {
    const dep = allTasks.find((t) => t.id === depId);
    return dep ? dep.title : depId;
  });

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-zinc-800/50 transition-colors"
      >
        <svg
          className={`h-3.5 w-3.5 text-zinc-500 transition-transform ${open ? "rotate-90" : ""}`}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        <span className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-zinc-400 shrink-0">
          {task.id}
        </span>
        <span className="text-sm font-medium text-white flex-1 truncate">
          {task.title}
        </span>
        <span className={`rounded-full border px-2 py-0.5 text-xs font-medium shrink-0 ${complexityStyle}`}>
          {task.complexity ?? "medium"}
        </span>
      </button>

      {open && (
        <div className="border-t border-zinc-800 px-4 py-3 space-y-3">
          {/* Description */}
          {task.description && (
            <div>
              <p className="text-xs font-medium text-zinc-500 mb-1">Description</p>
              <p className="text-sm text-zinc-300 whitespace-pre-wrap">{task.description}</p>
            </div>
          )}

          {/* Target files */}
          {task.targetFiles && task.targetFiles.length > 0 && (
            <div>
              <p className="text-xs font-medium text-zinc-500 mb-1">
                Target Files ({task.targetFiles.length})
              </p>
              <ul className="space-y-0.5">
                {task.targetFiles.map((f) => (
                  <li key={f} className="truncate font-mono text-xs text-zinc-400">
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* Dependencies */}
          {depNames.length > 0 && (
            <div>
              <p className="text-xs font-medium text-zinc-500 mb-1">
                Depends On
              </p>
              <div className="flex flex-wrap gap-1.5">
                {depNames.map((name) => (
                  <span key={name} className="rounded bg-zinc-800 px-2 py-0.5 text-xs text-zinc-400">
                    {name}
                  </span>
                ))}
              </div>
            </div>
          )}
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
    <div className="mb-8">
      <div className="mb-3 flex items-center justify-between">
        <button
          type="button"
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center gap-2 text-left"
        >
          <svg
            className={`h-4 w-4 text-zinc-400 transition-transform ${collapsed ? "" : "rotate-90"}`}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <h2 className="text-lg font-semibold text-white">
            Plan — {taskList.length} task{taskList.length !== 1 ? "s" : ""}
          </h2>
        </button>
        {isPlanned && (
          <button
            type="button"
            onClick={onExecute}
            disabled={executing}
            className="rounded-lg bg-green-600 px-6 py-2 text-sm font-semibold text-white transition hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {executing ? "Starting..." : "Execute Plan"}
          </button>
        )}
      </div>
      {!collapsed && (
        <div className="space-y-2">
          {taskList.map((task) => (
            <PlanTaskCard key={task.id} task={task} allTasks={taskList} />
          ))}
        </div>
      )}
    </div>
  );
}

/* ── Connection Status Banner ─────────────────────────────────────── */

function ConnectionBanner({ status }: { status: WsStatus }) {
  if (status === "connected") return null;

  const messages: Record<string, { text: string; color: string }> = {
    connecting: { text: "Connecting to server...", color: "bg-blue-900/50 border-blue-800 text-blue-300" },
    reconnecting: { text: "Reconnecting...", color: "bg-yellow-900/50 border-yellow-800 text-yellow-300" },
    disconnected: { text: "Disconnected. Please refresh the page.", color: "bg-red-900/50 border-red-800 text-red-300" },
  };

  const msg = messages[status];
  return (
    <div className={`mb-4 flex items-center gap-2 rounded-lg border px-4 py-2.5 text-sm font-medium ${msg.color}`}>
      {status !== "disconnected" && (
        <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {msg.text}
    </div>
  );
}

/* ── Main Page ────────────────────────────────────────────────────── */

export default function TaskExecutionPage() {
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

  const [executing, setExecuting] = useState(false);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
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
    }
    setPipelineId(pipelineId);
  }, [pipelineId, storePipelineId, setPipelineId, reset]);

  // Hydrate initial state from REST (handles page refresh, late WS connect)
  useEffect(() => {
    if (!token || !pipelineId || hydrated.current) return;
    hydrated.current = true;
    apiGet(`/tasks/${pipelineId}`, token)
      .then((data) => {
        if (data.tasks?.length > 0 || data.phase !== "planning") {
          hydrateFromRest(data);
        }
      })
      .catch(() => {});
  }, [token, pipelineId, hydrateFromRest]);

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

  const { status: wsStatus } = useWebSocket(pipelineId, token, onMessage);

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
      <div className="flex h-screen items-center justify-center bg-black text-zinc-400">
        No pipeline ID provided.
      </div>
    );
  }

  // Determine if we should show the plan panel (always, once tasks exist)
  const hasTasks = taskList.length > 0;
  // Show agent cards during execution/review/complete phases
  const showAgentCards = phase !== "idle" && phase !== "planning" && phase !== "planned" && hasTasks;

  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {/* Breadcrumb / Back */}
        <div className="mb-6 flex items-center gap-3">
          <Link
            href="/"
            className="rounded-lg border border-zinc-800 px-3 py-1.5 text-sm text-zinc-400 transition-colors hover:border-zinc-600 hover:text-zinc-200"
          >
            &larr; Back
          </Link>
          <span className="text-sm text-zinc-500">
            Pipeline{" "}
            <span className="font-mono text-zinc-400">{pipelineId}</span>
          </span>
        </div>

        {/* Connection Status */}
        <ConnectionBanner status={wsStatus} />

        {/* Pipeline Progress */}
        <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-4">
          <PipelineProgress phase={phase} />
        </div>

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
          <div className="mb-4 flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-2.5">
            <div className="flex items-center gap-2 text-sm text-zinc-400">
              <span>
                {phase === "executing" ? "Executing" : phase === "reviewing" ? "Reviewing" : "Complete"}
              </span>
              <span className="text-zinc-600">|</span>
              <span>
                {taskList.filter(t => t.state === "done").length}/{taskList.length} tasks complete
              </span>
            </div>
          </div>
        )}

        {/* Agent Cards Grid — shown during execution */}
        {showAgentCards ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {taskList.map((task) => (
              <AgentCard key={task.id} task={task} onClick={() => setSelectedTaskId(task.id)} />
            ))}
          </div>
        ) : (
          !hasTasks &&
          phase === "idle" && (
            <div className="flex h-64 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900">
              <div className="text-center">
                <div className="mb-2 text-lg text-zinc-400">
                  Waiting for pipeline to start...
                </div>
                <div className="h-1.5 w-48 overflow-hidden rounded-full bg-zinc-800">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-600" />
                </div>
              </div>
            </div>
          )
        )}

        {/* Cancel Button — shown only during active execution */}
        {phase === "executing" && (
          <div className="mt-4 flex justify-center gap-3">
            <button
              onClick={async () => {
                if (!token || !pipelineId) return;
                if (!confirm("Cancel all running tasks?")) return;
                try {
                  await apiPost(`/tasks/${pipelineId}/cancel`, {}, token);
                  window.location.reload();
                } catch (e) {
                  // Error surfaces via events
                }
              }}
              className="rounded-lg bg-red-600 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-red-700"
            >
              Cancel Pipeline
            </button>
          </div>
        )}

        {/* Resume Button — shown only when pipeline is complete with errored tasks */}
        {phase === "complete" && taskList.some(t => t.state === "error") && (
          <div className="mt-4 flex justify-center gap-3">
            <button
              onClick={async () => {
                if (!token || !pipelineId) return;
                try {
                  await apiPost(`/tasks/${pipelineId}/resume`, {}, token);
                  window.location.reload();
                } catch (e) {
                  // Error will surface via events
                }
              }}
              className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white transition hover:bg-blue-700"
            >
              Resume Pipeline
            </button>
          </div>
        )}

        {/* Completion Summary */}
        {phase === "complete" && (
          <div className="mt-8">
            <CompletionSummary tasks={tasks} pipelineId={pipelineId} />
          </div>
        )}
      </div>

      {/* Task Detail Slide-out Panel */}
      {selectedTaskId && tasks[selectedTaskId] && (
        <TaskDetailPanel
          task={tasks[selectedTaskId]}
          onClose={() => setSelectedTaskId(null)}
        />
      )}
    </div>
  );
}

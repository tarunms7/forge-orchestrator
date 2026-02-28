"use client";

import { useCallback, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import AgentCard from "@/components/task/AgentCard";
import PipelineProgress from "@/components/task/PipelineProgress";
import CompletionSummary from "@/components/task/CompletionSummary";

export default function TaskExecutionPage() {
  const searchParams = useSearchParams();
  const pipelineId = searchParams.get("id") ?? "";

  const token = useAuthStore((s) => s.token);
  const phase = useTaskStore((s) => s.phase);
  const tasks = useTaskStore((s) => s.tasks);
  const handleEvent = useTaskStore((s) => s.handleEvent);

  const [executing, setExecuting] = useState(false);

  const onMessage = useCallback(
    (raw: unknown) => {
      const msg = raw as Record<string, unknown>;
      const { type, ...data } = msg;
      handleEvent({ event: type as string, data });
    },
    [handleEvent],
  );

  useWebSocket(pipelineId, token, onMessage);

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

        {/* Pipeline Progress */}
        <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 p-4">
          <PipelineProgress phase={phase} />
        </div>

        {/* Plan Review — shown when plan is ready for approval */}
        {phase === "planned" && taskList.length > 0 && (
          <div className="mb-8">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-white">
                Plan Ready — {taskList.length} task{taskList.length !== 1 ? "s" : ""}
              </h2>
              <button
                type="button"
                onClick={handleExecute}
                disabled={executing}
                className="rounded-lg bg-green-600 px-6 py-2 text-sm font-semibold text-white transition hover:bg-green-700 disabled:cursor-not-allowed disabled:opacity-40"
              >
                {executing ? "Starting..." : "Execute Plan"}
              </button>
            </div>
            <div className="space-y-3">
              {taskList.map((task) => (
                <div
                  key={task.id}
                  className="rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-3"
                >
                  <div className="flex items-center gap-3">
                    <span className="rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-zinc-400">
                      {task.id}
                    </span>
                    <span className="text-sm font-medium text-white">
                      {task.title}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Agent Cards Grid — shown during execution */}
        {phase !== "planned" && taskList.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {taskList.map((task) => (
              <AgentCard key={task.id} task={task} />
            ))}
          </div>
        ) : (
          phase !== "planned" &&
          taskList.length === 0 && (
            <div className="flex h-64 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900">
              <div className="text-center">
                <div className="mb-2 text-lg text-zinc-400">
                  {phase === "idle"
                    ? "Waiting for connection..."
                    : "Planning tasks..."}
                </div>
                <div className="h-1.5 w-48 overflow-hidden rounded-full bg-zinc-800">
                  <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-600" />
                </div>
              </div>
            </div>
          )
        )}

        {/* Completion Summary */}
        {phase === "complete" && (
          <div className="mt-8">
            <CompletionSummary tasks={tasks} pipelineId={pipelineId} />
          </div>
        )}
      </div>
    </div>
  );
}

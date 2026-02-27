"use client";

import { useCallback } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { useWebSocket } from "@/hooks/useWebSocket";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
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

  const onMessage = useCallback(
    (event: unknown) => {
      handleEvent(event as { event: string; data: Record<string, unknown> });
    },
    [handleEvent],
  );

  useWebSocket(pipelineId, token, onMessage);

  const taskList = Object.values(tasks);

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

        {/* Agent Cards Grid */}
        {taskList.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {taskList.map((task) => (
              <AgentCard key={task.id} task={task} />
            ))}
          </div>
        ) : (
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
        )}

        {/* Completion Summary */}
        {phase === "complete" && (
          <div className="mt-8">
            <CompletionSummary tasks={tasks} />
          </div>
        )}
      </div>
    </div>
  );
}

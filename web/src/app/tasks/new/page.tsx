"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { apiPost } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import ProjectSelector, { ProjectConfig } from "@/components/task/ProjectSelector";
import TaskForm, { TaskFormData } from "@/components/task/TaskForm";
import ExecutionTargetSelector, { ExecutionConfig } from "@/components/task/ExecutionTargetSelector";
import TemplatePicker from "@/components/task/TemplatePicker";

const STEPS = ["Project", "Task", "Execute"];

function StepIndicator({ current }: { current: number }) {
  return (
    <div className="flex items-center justify-center gap-2">
      {STEPS.map((label, i) => {
        const stepNum = i + 1;
        const isActive = stepNum === current;
        const isComplete = stepNum < current;

        return (
          <div key={label} className="flex items-center gap-2">
            {i > 0 && (
              <div
                className={`h-px w-8 ${
                  isComplete ? "bg-blue-600" : "bg-zinc-700"
                }`}
              />
            )}
            <div className="flex items-center gap-2">
              <div
                className={`flex h-8 w-8 items-center justify-center rounded-full text-sm font-medium transition ${
                  isActive
                    ? "bg-blue-600 text-white"
                    : isComplete
                      ? "bg-blue-600/20 text-blue-400"
                      : "bg-zinc-800 text-zinc-500"
                }`}
              >
                {isComplete ? (
                  <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                ) : (
                  stepNum
                )}
              </div>
              <span
                className={`text-sm font-medium ${
                  isActive ? "text-white" : isComplete ? "text-blue-400" : "text-zinc-500"
                }`}
              >
                {label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function ReviewSummary({
  project,
  task,
  execution,
}: {
  project: ProjectConfig;
  task: TaskFormData;
  execution: ExecutionConfig;
}) {
  const sourceLabels = { existing: "Existing repo", clone: "GitHub clone", create: "New project" };
  const projectDetail =
    project.source === "existing"
      ? project.path
      : project.source === "clone"
        ? project.githubUrl
        : project.projectName;

  return (
    <div className="space-y-4 rounded-lg border border-zinc-700 bg-zinc-900 p-5">
      <h3 className="text-sm font-semibold uppercase tracking-wider text-zinc-400">
        Review Summary
      </h3>

      <div className="space-y-3 text-sm">
        <div className="flex justify-between border-b border-zinc-800 pb-2">
          <span className="text-zinc-400">Project</span>
          <span className="text-white">
            {sourceLabels[project.source]}: {projectDetail || "—"}
          </span>
        </div>
        <div className="flex justify-between border-b border-zinc-800 pb-2">
          <span className="text-zinc-400">Priority</span>
          <span className="capitalize text-white">{task.priority}</span>
        </div>
        <div className="flex justify-between border-b border-zinc-800 pb-2">
          <span className="text-zinc-400">Target</span>
          <span className="capitalize text-white">
            {execution.target === "remote"
              ? `Remote (${execution.sshUser}@${execution.sshHost}:${execution.sshPort})`
              : "Local"}
          </span>
        </div>
        <div>
          <span className="text-zinc-400">Description</span>
          <p className="mt-1 whitespace-pre-wrap text-white">{task.description || "—"}</p>
        </div>
      </div>
    </div>
  );
}

export default function NewTaskPage() {
  const router = useRouter();
  const token = useAuthStore((s) => s.token);

  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state
  const [project, setProject] = useState<ProjectConfig>({ source: "existing" });
  const [task, setTask] = useState<TaskFormData>({
    description: "",
    priority: "medium",
    additionalContext: "",
  });
  const [execution, setExecution] = useState<ExecutionConfig>({ target: "local" });

  function canAdvance(): boolean {
    if (step === 1) {
      if (project.source === "existing" && !project.path?.trim()) return false;
      if (project.source === "clone" && !project.githubUrl?.trim()) return false;
      if (project.source === "create" && !project.projectName?.trim()) return false;
      return true;
    }
    if (step === 2) {
      return task.description.trim().length > 0;
    }
    return true;
  }

  function resolveProjectPath(): string {
    if (project.source === "existing") return project.path || "";
    if (project.source === "clone") return project.githubUrl || "";
    return project.projectName || "";
  }

  async function handleSubmit() {
    if (!token) {
      setError("You must be logged in to create a task.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const body: Record<string, unknown> = {
        description: task.description,
        project_path: resolveProjectPath(),
        extra_dirs: [],
      };

      if (task.additionalContext.trim()) {
        body.description = `${task.description}\n\n---\nContext: ${task.additionalContext}`;
      }

      const data = await apiPost("/tasks", body, token);
      router.push(`/tasks/${data.pipeline_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-zinc-950">
      <div className="mx-auto max-w-2xl px-4 py-10">
        {/* Header */}
        <div className="mb-8 text-center">
          <h1 className="text-2xl font-bold text-white">Create a New Task</h1>
          <p className="mt-2 text-sm text-zinc-400">
            Set up and launch a Forge pipeline in three steps.
          </p>
        </div>

        {/* Step indicator */}
        <div className="mb-8">
          <StepIndicator current={step} />
        </div>

        {/* Error */}
        {error && (
          <div className="mb-6 rounded-md border border-red-800 bg-red-950 px-4 py-3 text-sm text-red-400">
            {error}
          </div>
        )}

        {/* Step content */}
        <div className="rounded-xl border border-zinc-800 bg-zinc-900 p-6">
          {step === 1 && <ProjectSelector value={project} onChange={setProject} />}
          {step === 2 && (
            <div className="space-y-6">
              <TemplatePicker
                onSelect={(desc) => setTask((prev) => ({ ...prev, description: desc }))}
              />
              <div className="border-t border-zinc-800" />
              <TaskForm value={task} onChange={setTask} />
            </div>
          )}
          {step === 3 && (
            <div className="space-y-8">
              <ExecutionTargetSelector value={execution} onChange={setExecution} />
              <ReviewSummary project={project} task={task} execution={execution} />
            </div>
          )}
        </div>

        {/* Navigation buttons */}
        <div className="mt-6 flex items-center justify-between">
          <button
            type="button"
            onClick={() => setStep((s) => Math.max(1, s - 1))}
            disabled={step === 1}
            className="rounded-lg bg-zinc-800 px-5 py-2 text-sm font-medium text-zinc-300 transition hover:bg-zinc-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            Previous
          </button>

          {step < 3 ? (
            <button
              type="button"
              onClick={() => setStep((s) => s + 1)}
              disabled={!canAdvance()}
              className="rounded-lg bg-blue-600 px-5 py-2 text-sm font-medium text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              onClick={handleSubmit}
              disabled={loading || !canAdvance()}
              className="rounded-lg bg-blue-600 px-6 py-2 text-sm font-semibold text-white transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {loading ? "Creating..." : "Run Task"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

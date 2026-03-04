"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { apiPost } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import ProjectSelector, { ProjectConfig } from "@/components/task/ProjectSelector";
import TaskForm, { TaskFormData, validateBranchName } from "@/components/task/TaskForm";
import ExecutionTargetSelector, { ExecutionConfig } from "@/components/task/ExecutionTargetSelector";
import TemplatePicker from "@/components/task/TemplatePicker";

const STEPS = ["Project", "Task", "Execute"];

function StepIndicator({ current }: { current: number }) {
  return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: "8px" }}>
      {STEPS.map((label, i) => {
        const stepNum = i + 1;
        const isActive = stepNum === current;
        const isComplete = stepNum < current;

        return (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            {i > 0 && (
              <div style={{ height: "1px", width: "32px", background: isComplete ? "var(--accent)" : "var(--border)" }} />
            )}
            <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
              <div style={{
                display: "flex", height: "32px", width: "32px", alignItems: "center", justifyContent: "center",
                borderRadius: "50%", fontSize: "13px", fontWeight: 500,
                transition: "var(--transition)",
                background: isActive ? "var(--accent)" : isComplete ? "var(--accent-glow)" : "var(--bg-surface-3)",
                color: isActive ? "white" : isComplete ? "var(--accent)" : "var(--text-dim)",
              }}>
                {isComplete ? (
                  <svg style={{ width: "16px", height: "16px" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                ) : stepNum}
              </div>
              <span style={{
                fontSize: "13px", fontWeight: 500,
                color: isActive ? "var(--text-primary)" : isComplete ? "var(--accent)" : "var(--text-dim)",
              }}>
                {label}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function fileToBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error(`Failed to read file: ${file.name}`));
    reader.readAsDataURL(file);
  });
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
    <div style={{ borderRadius: "var(--radius-md)", border: "1px solid var(--border)", background: "var(--bg-surface-1)", padding: "20px" }}>
      <h3 style={{ fontSize: "11px", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--text-tertiary)", marginBottom: "16px" }}>
        Review Summary
      </h3>
      <div style={{ display: "flex", flexDirection: "column", gap: "12px", fontSize: "13px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
          <span style={{ color: "var(--text-tertiary)" }}>Project</span>
          <span style={{ color: "var(--text-primary)" }}>
            {sourceLabels[project.source]}: {projectDetail || "\u2014"}
          </span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
          <span style={{ color: "var(--text-tertiary)" }}>Priority</span>
          <span style={{ color: "var(--text-primary)", textTransform: "capitalize" }}>{task.priority}</span>
        </div>
        <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
          <span style={{ color: "var(--text-tertiary)" }}>Target</span>
          <span style={{ color: "var(--text-primary)", textTransform: "capitalize" }}>
            {execution.target === "remote"
              ? `Remote (${execution.sshUser}@${execution.sshHost}:${execution.sshPort})`
              : "Local"}
          </span>
        </div>
        {task.branchName.trim() && (
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
            <span style={{ color: "var(--text-tertiary)" }}>Branch</span>
            <span style={{ color: "var(--text-primary)", fontFamily: "monospace", fontSize: "12px" }}>
              {task.branchName.trim()}
            </span>
          </div>
        )}
        {task.buildCmd.trim() && (
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
            <span style={{ color: "var(--text-tertiary)" }}>Build command</span>
            <span style={{ color: "var(--text-primary)", fontFamily: "monospace", fontSize: "12px" }}>
              {task.buildCmd.trim()}
            </span>
          </div>
        )}
        {task.testCmd.trim() && (
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
            <span style={{ color: "var(--text-tertiary)" }}>Test command</span>
            <span style={{ color: "var(--text-primary)", fontFamily: "monospace", fontSize: "12px" }}>
              {task.testCmd.trim()}
            </span>
          </div>
        )}
        {task.images.length > 0 && (
          <div style={{ display: "flex", justifyContent: "space-between", borderBottom: "1px solid var(--border-subtle)", paddingBottom: "8px" }}>
            <span style={{ color: "var(--text-tertiary)" }}>Images</span>
            <span style={{ color: "var(--text-primary)" }}>
              {task.images.length} image{task.images.length === 1 ? "" : "s"} attached
            </span>
          </div>
        )}
        <div>
          <span style={{ color: "var(--text-tertiary)" }}>Description</span>
          <p style={{ marginTop: "4px", whiteSpace: "pre-wrap", color: "var(--text-primary)" }}>{task.description || "\u2014"}</p>
        </div>
      </div>
    </div>
  );
}

export default function NewTaskPage() {
  return (
    <Suspense fallback={
      <div className="page-content" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "400px", color: "var(--text-tertiary)" }}>
        Loading...
      </div>
    }>
      <NewTaskPageInner />
    </Suspense>
  );
}

function NewTaskPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = useAuthStore((s) => s.token);

  const [step, setStep] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Form state — pre-fill description from ?desc= query param (dashboard shortcut)
  const [project, setProject] = useState<ProjectConfig>({ source: "existing" });
  const [task, setTask] = useState<TaskFormData>({
    description: searchParams.get("desc") || "",
    priority: "medium",
    additionalContext: "",
    images: [],
    branchName: "",
    buildCmd: "",
    testCmd: "",
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
      if (!task.description.trim()) return false;
      if (task.branchName.trim() && validateBranchName(task.branchName.trim())) return false;
      return true;
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
      // Convert images to base64 data URIs
      const imageDataUris: string[] = [];
      for (const img of task.images) {
        const dataUri = await fileToBase64(img.file);
        imageDataUris.push(dataUri);
      }

      const body: Record<string, unknown> = {
        description: task.description,
        project_path: resolveProjectPath(),
        extra_dirs: [],
        model_strategy: "auto",
      };

      if (task.additionalContext.trim()) {
        body.description = `${task.description}\n\n---\nContext: ${task.additionalContext}`;
      }

      if (imageDataUris.length > 0) {
        body.images = imageDataUris;
      }

      if (task.branchName.trim()) {
        body.branch_name = task.branchName.trim();
      }

      body.build_cmd = task.buildCmd || null;
      body.test_cmd = task.testCmd || null;

      const data = await apiPost("/tasks", body, token);
      router.push(`/tasks/view?id=${data.pipeline_id}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create task");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="page-content">
      {/* Header */}
      <div style={{ marginBottom: "32px", textAlign: "center" }}>
        <h1 className="page-title">Create a New Task</h1>
        <p className="page-subtitle">Set up and launch a Forge pipeline in three steps.</p>
      </div>

      {/* Step indicator */}
      <div style={{ marginBottom: "32px" }}>
        <StepIndicator current={step} />
      </div>

      {/* Error */}
      {error && (
        <div style={{
          marginBottom: "24px", borderRadius: "var(--radius-md)",
          border: "1px solid rgba(239,68,68,0.3)", background: "var(--red-dim)",
          padding: "12px 16px", fontSize: "13px", color: "#fca5a5",
        }}>
          {error}
        </div>
      )}

      {/* Step content */}
      <div style={{
        borderRadius: "var(--radius-lg)", border: "1px solid var(--border)",
        background: "var(--bg-surface-1)", padding: "24px",
      }}>
        {step === 1 && <ProjectSelector value={project} onChange={setProject} />}
        {step === 2 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "24px" }}>
            <TemplatePicker
              onSelect={(desc) => setTask((prev) => ({ ...prev, description: desc }))}
            />
            <div style={{ borderTop: "1px solid var(--border)", margin: "0" }} />
            <TaskForm value={task} onChange={setTask} />
          </div>
        )}
        {step === 3 && (
          <div style={{ display: "flex", flexDirection: "column", gap: "32px" }}>
            <ExecutionTargetSelector value={execution} onChange={setExecution} />
            <ReviewSummary project={project} task={task} execution={execution} />
          </div>
        )}
      </div>

      {/* Navigation buttons */}
      <div style={{ marginTop: "24px", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <button
          type="button"
          onClick={() => setStep((s) => Math.max(1, s - 1))}
          disabled={step === 1}
          style={{
            borderRadius: "var(--radius-md)", background: "var(--bg-surface-3)",
            padding: "10px 20px", fontSize: "13px", fontWeight: 500,
            color: "var(--text-secondary)", border: "none", cursor: step === 1 ? "not-allowed" : "pointer",
            opacity: step === 1 ? 0.4 : 1, transition: "var(--transition)",
          }}
        >
          Previous
        </button>

        {step < 3 ? (
          <button
            type="button"
            onClick={() => setStep((s) => s + 1)}
            disabled={!canAdvance()}
            className="btn btn-primary"
            style={{ opacity: canAdvance() ? 1 : 0.4, cursor: canAdvance() ? "pointer" : "not-allowed" }}
          >
            Next
          </button>
        ) : (
          <button
            type="button"
            onClick={handleSubmit}
            disabled={loading || !canAdvance()}
            className="btn btn-primary btn-glow"
            style={{ opacity: (loading || !canAdvance()) ? 0.4 : 1, cursor: (loading || !canAdvance()) ? "not-allowed" : "pointer" }}
          >
            {loading ? "Creating..." : "Run Task"}
          </button>
        )}
      </div>
    </div>
  );
}

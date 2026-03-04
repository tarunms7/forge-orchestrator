import { create } from "zustand";
import {
  submitFollowUp as apiSubmitFollowUp,
  cancelPipeline as apiCancelPipeline,
  restartPipeline as apiRestartPipeline,
  apiGet,
} from "@/lib/api";
import type { EditableTask, ValidationResult } from "@/lib/validateTaskGraph";
import { validateTaskGraph } from "@/lib/validateTaskGraph";

/** Maximum timeline entries to keep in memory (prevents unbounded growth). */
const MAX_TIMELINE_ENTRIES = 500;
/** Maximum agent output lines per task (prevents browser memory issues on verbose tasks). */
const MAX_OUTPUT_LINES = 1000;

function sendNotification(title: string, body: string) {
  if (typeof window !== "undefined" && "Notification" in window && Notification.permission === "granted") {
    new Notification(title, { body });
  }
}

export interface TaskState {
  id: string;
  title: string;
  state: "pending" | "working" | "in_review" | "awaiting_approval" | "done" | "error" | "retrying" | "cancelled";
  branch: string;
  /** Description from the plan (what this task does). */
  description?: string;
  /** Target files from the plan (files the agent should create/modify). */
  targetFiles?: string[];
  /** Dependency IDs from the plan. */
  dependsOn?: string[];
  /** Complexity tier from the plan. */
  complexity?: string;
  /** Diff preview for approval UI (first 200 lines). */
  diffPreview?: string;
  /** Files actually changed during execution. */
  files: string[];
  output: string[];
  reviewGates: { gate: string; result: string; details?: string }[];
  mergeResult?: {
    success: boolean;
    error?: string;
    linesAdded?: number;
    linesRemoved?: number;
  };
  costUsd?: number;
  agentCostUsd?: number;
  reviewCostUsd?: number;
  inputTokens?: number;
  outputTokens?: number;
  diffPreview?: string;
}

export interface TimelineEntry {
  type: string;
  taskId?: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

export interface FollowUpResult {
  taskId: string;
  title: string;
  status: "working" | "done" | "error";
  output: string[];
  filesChanged?: string[];
}

export interface PipelineState {
  pipelineId: string | null;
  phase: "idle" | "planning" | "planned" | "executing" | "reviewing" | "paused" | "complete" | "cancelled" | "error";
  tasks: Record<string, TaskState>;
  plannerOutput: string[];
  timeline: TimelineEntry[];
  prUrl: string | null;
  prLoading: boolean;
  prError: string | null;
  hydrationError: string | null;
  pipelineCost: number;
  estimatedCostUsd: number;
  budgetLimitUsd: number;

  /* Plan editing state */
  editedTasks: EditableTask[] | null;
  planValidation: ValidationResult;

  /* Follow-up state */
  followUpQuestions: string[];
  followUpStatus: "idle" | "submitting" | "executing" | "done";
  followUpResults: Record<string, FollowUpResult>;

  setPipelineId: (id: string) => void;
  setHydrationError: (err: string | null) => void;
  hydrateFromRest: (data: {
    phase: string;
    tasks: Array<Record<string, unknown>>;
    timeline?: Array<Record<string, unknown>>;
    pr_url?: string | null;
    planner_output?: string[];
    total_cost_usd?: number;
    estimated_cost_usd?: number;
    budget_limit_usd?: number;
  }) => void;
  handleEvent: (event: {
    event: string;
    data: Record<string, unknown>;
  }) => void;
  reset: () => void;

  /* Plan editing actions */
  setEditedTasks: (tasks: EditableTask[]) => void;
  updateEditedTask: (id: string, patch: Partial<EditableTask>) => void;
  deleteEditedTask: (id: string) => void;
  addEditedTask: (task: EditableTask) => void;
  reorderEditedTasks: (fromIndex: number, toIndex: number) => void;
  resetEdits: () => void;

  /* Follow-up actions */
  setFollowUpStatus: (status: PipelineState["followUpStatus"]) => void;
  addFollowUpQuestion: (question: string) => void;
  resetFollowUp: () => void;

  /* Pipeline control actions */
  submitFollowUp: (questions: string, token: string) => Promise<void>;
  cancelPipeline: (token: string) => Promise<void>;
  restartPipeline: (token: string) => Promise<void>;
}

// Backend daemon uses different state names than the frontend UI.
// Map them so AgentCard's STATE_BADGE never gets undefined.
const BACKEND_STATE_MAP: Record<string, TaskState["state"]> = {
  todo: "pending",
  in_progress: "working",
  in_review: "in_review",
  awaiting_approval: "awaiting_approval",
  merging: "working",
  done: "done",
  error: "error",
  cancelled: "cancelled",
  // Frontend-native states pass through
  pending: "pending",
  working: "working",
  retrying: "retrying",
};

function mapState(raw: string): TaskState["state"] {
  return BACKEND_STATE_MAP[raw] ?? "pending";
}

const INITIAL_FOLLOWUP = {
  followUpQuestions: [] as string[],
  followUpStatus: "idle" as PipelineState["followUpStatus"],
  followUpResults: {} as Record<string, FollowUpResult>,
};

export const useTaskStore = create<PipelineState>((set, get) => ({
  pipelineId: null,
  phase: "idle",
  tasks: {},
  plannerOutput: [],
  timeline: [],
  prUrl: null,
  prLoading: false,
  prError: null,
  hydrationError: null,
  pipelineCost: 0,
  estimatedCostUsd: 0,
  budgetLimitUsd: 0,
  editedTasks: null,
  planValidation: { valid: true, errors: [] },
  ...INITIAL_FOLLOWUP,
  setPipelineId: (id) => set({ pipelineId: id }),
  setHydrationError: (err) => set({ hydrationError: err }),

  /* Plan editing actions */
  setEditedTasks: (tasks) =>
    set({ editedTasks: tasks, planValidation: validateTaskGraph(tasks) }),

  updateEditedTask: (id, patch) =>
    set((state) => {
      if (!state.editedTasks) return {};
      const updated = state.editedTasks.map((t) =>
        t.id === id ? { ...t, ...patch } : t
      );
      return { editedTasks: updated, planValidation: validateTaskGraph(updated) };
    }),

  deleteEditedTask: (id) =>
    set((state) => {
      if (!state.editedTasks) return {};
      // Remove the task and clean up dangling dependency references
      const updated = state.editedTasks
        .filter((t) => t.id !== id)
        .map((t) => ({
          ...t,
          depends_on: t.depends_on.filter((dep) => dep !== id),
        }));
      return { editedTasks: updated, planValidation: validateTaskGraph(updated) };
    }),

  addEditedTask: (task) =>
    set((state) => {
      const updated = [...(state.editedTasks || []), task];
      return { editedTasks: updated, planValidation: validateTaskGraph(updated) };
    }),

  reorderEditedTasks: (fromIndex, toIndex) =>
    set((state) => {
      if (!state.editedTasks) return {};
      const updated = [...state.editedTasks];
      const [moved] = updated.splice(fromIndex, 1);
      updated.splice(toIndex, 0, moved);
      return { editedTasks: updated, planValidation: validateTaskGraph(updated) };
    }),

  resetEdits: () =>
    set({ editedTasks: null, planValidation: { valid: true, errors: [] } }),

  /* Follow-up actions */
  setFollowUpStatus: (status) => set({ followUpStatus: status }),
  addFollowUpQuestion: (question) =>
    set((state) => ({
      followUpQuestions: [...state.followUpQuestions, question],
    })),
  resetFollowUp: () => set(INITIAL_FOLLOWUP),

  /* Pipeline control actions */
  submitFollowUp: async (questions, token) => {
    const pid = get().pipelineId;
    if (!pid) return;
    set({ followUpStatus: "submitting" });
    set((state) => ({ followUpQuestions: [...state.followUpQuestions, questions] }));
    try {
      await apiSubmitFollowUp(pid, questions, token);
      // Status will transition to "executing" via WebSocket followup:started event
    } catch (err) {
      set({ followUpStatus: "idle" });
      throw err; // let caller handle error display
    }
  },

  cancelPipeline: async (token) => {
    const pid = get().pipelineId;
    if (!pid) return;
    await apiCancelPipeline(pid, token);
    // Re-fetch state — WebSocket will also deliver updates
    const data = await apiGet(`/tasks/${pid}`, token);
    get().hydrateFromRest(data);
  },

  restartPipeline: async (token) => {
    const pid = get().pipelineId;
    if (!pid) return;
    await apiRestartPipeline(pid, token);
    // Re-fetch state — WebSocket will also deliver updates
    const data = await apiGet(`/tasks/${pid}`, token);
    get().hydrateFromRest(data);
  },

  hydrateFromRest: (data) => {
    const newTasks: Record<string, TaskState> = {};
    for (const t of data.tasks) {
      newTasks[t.id as string] = {
        id: t.id as string,
        title: t.title as string,
        description: t.description as string | undefined,
        targetFiles: t.files as string[] | undefined,
        dependsOn: t.depends_on as string[] | undefined,
        complexity: t.complexity as string | undefined,
        state: mapState((t.state as string) || "pending"),
        branch: `forge/${t.id}`,
        files: (t.files_changed as string[]) || [],
        output: (t.output as string[]) || [],
        reviewGates: (t.reviewGates as TaskState["reviewGates"]) || [],
        mergeResult: (t.mergeResult as TaskState["mergeResult"]) || undefined,
        costUsd: (t.cost_usd as number) || undefined,
        agentCostUsd: (t.agent_cost_usd as number) || undefined,
        reviewCostUsd: (t.review_cost_usd as number) || undefined,
        inputTokens: (t.input_tokens as number) || undefined,
        outputTokens: (t.output_tokens as number) || undefined,
      };
    }
    const phase = (data.phase || "idle") as PipelineState["phase"];
    const timeline = ((data.timeline as Array<Record<string, unknown>>) || []).map((entry) => ({
      type: (entry.type as string) || "",
      taskId: (entry.taskId as string) || (entry.task_id as string) || undefined,
      payload: entry.payload as Record<string, unknown> || entry,
      timestamp: (entry.timestamp as string) || new Date().toISOString(),
    }));
    set({
      tasks: newTasks,
      phase,
      timeline,
      prUrl: data.pr_url ?? null,
      plannerOutput: data.planner_output ?? [],
      hydrationError: null,
      pipelineCost: (data.total_cost_usd as number) || 0,
      estimatedCostUsd: (data.estimated_cost_usd as number) || 0,
      budgetLimitUsd: (data.budget_limit_usd as number) || 0,
    });
  },
  reset: () =>
    set({
      pipelineId: null,
      phase: "idle",
      tasks: {},
      plannerOutput: [],
      timeline: [],
      prUrl: null,
      prLoading: false,
      prError: null,
      hydrationError: null,
      pipelineCost: 0,
      estimatedCostUsd: 0,
      budgetLimitUsd: 0,
      editedTasks: null,
      planValidation: { valid: true, errors: [] },
      ...INITIAL_FOLLOWUP,
    }),
  handleEvent: (event) =>
    set((state) => {
      const { event: eventName, data } = event;

      // Append to timeline for all events except agent_output (too noisy)
      let newTimeline = state.timeline;
      if (eventName !== "task:agent_output") {
        const entry = {
          type: eventName,
          taskId: (data.task_id as string) || undefined,
          payload: data,
          timestamp: new Date().toISOString(),
        };
        newTimeline = [...state.timeline, entry];
        // Cap timeline to prevent unbounded memory growth
        if (newTimeline.length > MAX_TIMELINE_ENTRIES) {
          newTimeline = newTimeline.slice(-MAX_TIMELINE_ENTRIES);
        }
      }

      switch (eventName) {
        case "pipeline:phase_changed": {
          if (data.phase === "complete") sendNotification("Pipeline complete", "All tasks finished");
          return { phase: data.phase as PipelineState["phase"], timeline: newTimeline };
        }

        case "pipeline:plan_ready": {
          const newTasks: Record<string, TaskState> = {};
          const incomingTasks = data.tasks as Array<{
            id: string;
            title: string;
            description?: string;
            files?: string[];
            depends_on?: string[];
            complexity?: string;
          }>;
          for (const t of incomingTasks) {
            newTasks[t.id] = {
              id: t.id,
              title: t.title,
              description: t.description,
              targetFiles: t.files,
              dependsOn: t.depends_on,
              complexity: t.complexity,
              state: "pending",
              branch: `forge/${t.id}`,
              files: [],
              output: [],
              reviewGates: [],
            };
          }
          // Deep copy incoming tasks for plan editing
          const editable: EditableTask[] = incomingTasks.map((t) => ({
            id: t.id,
            title: t.title,
            description: t.description || "",
            files: [...(t.files || [])],
            depends_on: [...(t.depends_on || [])],
            complexity: (t.complexity as EditableTask["complexity"]) || "medium",
          }));
          return {
            tasks: newTasks,
            phase: "planned",
            timeline: newTimeline,
            editedTasks: editable,
            planValidation: validateTaskGraph(editable),
          };
        }

        case "pipeline:cancelled": {
          // Mark all non-done tasks as cancelled and set phase
          const cancelledTasks = { ...state.tasks };
          for (const taskId of Object.keys(cancelledTasks)) {
            const task = cancelledTasks[taskId];
            if (task.state !== "done") {
              cancelledTasks[taskId] = { ...task, state: "cancelled" };
            }
          }
          sendNotification("Pipeline cancelled", "The pipeline has been cancelled");
          return {
            phase: "cancelled" as PipelineState["phase"],
            tasks: cancelledTasks,
            timeline: newTimeline,
          };
        }

        case "pipeline:restarted": {
          // Reset all state and set phase to planning
          sendNotification("Pipeline restarted", "The pipeline is being re-planned");
          return {
            phase: "planning" as PipelineState["phase"],
            tasks: {},
            plannerOutput: [],
            prUrl: null,
            prLoading: false,
            prError: null,
            timeline: newTimeline,
            ...INITIAL_FOLLOWUP,
          };
        }

        case "followup:started": {
          return {
            followUpStatus: "executing" as PipelineState["followUpStatus"],
            timeline: newTimeline,
          };
        }

        case "followup:progress": {
          const taskId = data.task_id as string;
          const existing = state.followUpResults[taskId];
          const output = existing
            ? [...existing.output, data.line as string]
            : [data.line as string];
          return {
            followUpResults: {
              ...state.followUpResults,
              [taskId]: {
                taskId,
                title: (data.title as string) || existing?.title || taskId,
                status: "working",
                output,
                filesChanged: (data.files_changed as string[]) || existing?.filesChanged,
              },
            },
            timeline: newTimeline,
          };
        }

        case "followup:completed": {
          // Mark all follow-up results as done
          const doneResults = { ...state.followUpResults };
          for (const key of Object.keys(doneResults)) {
            doneResults[key] = { ...doneResults[key], status: "done" };
          }
          // If the event includes task-level results, merge them
          if (data.results && Array.isArray(data.results)) {
            for (const r of data.results as Array<Record<string, unknown>>) {
              const tid = r.task_id as string;
              doneResults[tid] = {
                taskId: tid,
                title: (r.task_title as string) || (r.title as string) || tid,
                status: "done",
                output: doneResults[tid]?.output || [],
                filesChanged: (r.files_changed as string[]) || undefined,
              };
            }
          }
          sendNotification("Follow-up complete", "Follow-up questions have been addressed");
          return {
            followUpStatus: "done" as PipelineState["followUpStatus"],
            followUpResults: doneResults,
            timeline: newTimeline,
          };
        }

        case "task:state_changed": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          const newState = mapState(data.state as string);
          if (newState === "done") sendNotification("Task completed", existing.title);
          if (newState === "error") sendNotification("Task failed", existing.title);
          // Clear review gates when a retry starts (maps to "working")
          // so we only show the current attempt's gates, not all historical ones.
          const resetOnRetry = newState === "working"
            ? { reviewGates: [] as typeof existing.reviewGates, mergeResult: undefined }
            : {};
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                ...resetOnRetry,
                state: newState,
              },
            },
            timeline: newTimeline,
          };
        }

        case "task:agent_output": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          let newOutput = [...existing.output, data.line as string];
          // Cap output to prevent unbounded memory growth on verbose tasks
          if (newOutput.length > MAX_OUTPUT_LINES) {
            newOutput = newOutput.slice(-MAX_OUTPUT_LINES);
          }
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                output: newOutput,
              },
            },
          };
        }

        case "task:files_changed": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          return {
            tasks: {
              ...state.tasks,
              [taskId]: { ...existing, files: data.files as string[] },
            },
            timeline: newTimeline,
          };
        }

        case "task:review_update": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                reviewGates: [
                  ...existing.reviewGates,
                  {
                    gate: data.gate as string,
                    result: data.passed ? "pass" : "fail",
                    details: data.details as string,
                  },
                ],
              },
            },
            timeline: newTimeline,
          };
        }

        case "task:merge_result": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                mergeResult: data as TaskState["mergeResult"],
              },
            },
            timeline: newTimeline,
          };
        }

        case "task:cost_update": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                costUsd: (existing.costUsd || 0) + ((data.agent_cost_usd as number) || (data.review_cost_usd as number) || 0),
                agentCostUsd: data.agent_cost_usd != null
                  ? (data.agent_cost_usd as number)
                  : existing.agentCostUsd,
                reviewCostUsd: data.review_cost_usd != null
                  ? (data.review_cost_usd as number)
                  : existing.reviewCostUsd,
                inputTokens: data.input_tokens != null
                  ? (data.input_tokens as number)
                  : existing.inputTokens,
                outputTokens: data.output_tokens != null
                  ? (data.output_tokens as number)
                  : existing.outputTokens,
              },
            },
            timeline: newTimeline,
          };
        }

        case "pipeline:cost_update": {
          return {
            pipelineCost: data.total_cost_usd as number,
            timeline: newTimeline,
          };
        }

        case "pipeline:cost_estimate": {
          return {
            estimatedCostUsd: data.estimated_cost_usd as number,
            budgetLimitUsd: data.budget_limit_usd != null
              ? (data.budget_limit_usd as number)
              : state.budgetLimitUsd,
            timeline: newTimeline,
          };
        }

        case "pipeline:budget_exceeded": {
          sendNotification("Budget exceeded", `Pipeline cost exceeded budget limit of $${data.limit}`);
          return {
            phase: "error" as PipelineState["phase"],
            timeline: newTimeline,
          };
        }

        case "pipeline:pr_creating":
          return { prLoading: true, prError: null, timeline: newTimeline };

        case "pipeline:pr_created":
          sendNotification("PR created", (data.pr_url as string) || "");
          return { prUrl: data.pr_url as string, prLoading: false, prError: null, timeline: newTimeline };

        case "pipeline:pr_failed":
          return { prLoading: false, prError: data.error as string, timeline: newTimeline };

        case "planner:output":
          return {
            plannerOutput: [...state.plannerOutput, data.line as string],
            timeline: newTimeline,
          };

        case "task:awaiting_approval": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return { timeline: newTimeline };
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                state: "awaiting_approval",
                diffPreview: data.diff_preview as string,
              },
            },
            timeline: newTimeline,
          };
        }

        case "pipeline:paused":
          return { phase: "paused" as PipelineState["phase"], timeline: newTimeline };

        case "pipeline:resumed":
          return { phase: "executing" as PipelineState["phase"], timeline: newTimeline };

        case "pipeline:preflight_failed":
          return { phase: "idle" as PipelineState["phase"], timeline: newTimeline };

        default:
          return { timeline: newTimeline };
      }
    }),
}));

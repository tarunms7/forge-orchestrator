import { create } from "zustand";

export interface TaskState {
  id: string;
  title: string;
  state: "pending" | "working" | "in_review" | "done" | "error" | "retrying";
  branch: string;
  /** Description from the plan (what this task does). */
  description?: string;
  /** Target files from the plan (files the agent should create/modify). */
  targetFiles?: string[];
  /** Dependency IDs from the plan. */
  dependsOn?: string[];
  /** Complexity tier from the plan. */
  complexity?: string;
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
}

export interface PipelineState {
  pipelineId: string | null;
  phase: "idle" | "planning" | "planned" | "executing" | "reviewing" | "complete";
  tasks: Record<string, TaskState>;
  plannerOutput: string[];
  prUrl: string | null;
  prLoading: boolean;
  prError: string | null;
  setPipelineId: (id: string) => void;
  hydrateFromRest: (data: {
    phase: string;
    tasks: Array<Record<string, unknown>>;
    timeline?: Array<Record<string, unknown>>;
  }) => void;
  handleEvent: (event: {
    event: string;
    data: Record<string, unknown>;
  }) => void;
  reset: () => void;
}

// Backend daemon uses different state names than the frontend UI.
// Map them so AgentCard's STATE_BADGE never gets undefined.
const BACKEND_STATE_MAP: Record<string, TaskState["state"]> = {
  todo: "pending",
  in_progress: "working",
  in_review: "in_review",
  merging: "working",
  done: "done",
  error: "error",
  // Frontend-native states pass through
  pending: "pending",
  working: "working",
  retrying: "retrying",
};

function mapState(raw: string): TaskState["state"] {
  return BACKEND_STATE_MAP[raw] ?? "pending";
}

export const useTaskStore = create<PipelineState>((set) => ({
  pipelineId: null,
  phase: "idle",
  tasks: {},
  plannerOutput: [],
  prUrl: null,
  prLoading: false,
  prError: null,
  setPipelineId: (id) => set({ pipelineId: id }),
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
      };
    }
    const phase = (data.phase || "idle") as PipelineState["phase"];
    set({ tasks: newTasks, phase });
  },
  reset: () =>
    set({ pipelineId: null, phase: "idle", tasks: {}, plannerOutput: [], prUrl: null, prLoading: false, prError: null }),
  handleEvent: (event) =>
    set((state) => {
      const { event: eventName, data } = event;
      switch (eventName) {
        case "pipeline:phase_changed":
          return { phase: data.phase as PipelineState["phase"] };

        case "pipeline:plan_ready": {
          const newTasks: Record<string, TaskState> = {};
          for (const t of data.tasks as Array<{
            id: string;
            title: string;
            description?: string;
            files?: string[];
            depends_on?: string[];
            complexity?: string;
          }>) {
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
          return { tasks: newTasks, phase: "planned" };
        }

        case "task:state_changed": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                state: mapState(data.state as string),
              },
            },
          };
        }

        case "task:agent_output": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                output: [...existing.output, data.line as string],
              },
            },
          };
        }

        case "task:files_changed": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          return {
            tasks: {
              ...state.tasks,
              [taskId]: { ...existing, files: data.files as string[] },
            },
          };
        }

        case "task:review_update": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
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
          };
        }

        case "task:merge_result": {
          const taskId = data.task_id as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                mergeResult: data as TaskState["mergeResult"],
              },
            },
          };
        }

        case "pipeline:pr_creating":
          return { prLoading: true, prError: null };

        case "pipeline:pr_created":
          return { prUrl: data.pr_url as string, prLoading: false, prError: null };

        case "pipeline:pr_failed":
          return { prLoading: false, prError: data.error as string };

        case "planner:output":
          return {
            plannerOutput: [...state.plannerOutput, data.line as string],
          };

        default:
          return state;
      }
    }),
}));

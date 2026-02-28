import { create } from "zustand";

export interface TaskState {
  id: string;
  title: string;
  state: "pending" | "working" | "in_review" | "done" | "error" | "retrying";
  branch: string;
  files: string[];
  output: string[];
  reviewGates: { gate: number; result: string; details?: string }[];
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
  setPipelineId: (id: string) => void;
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
  setPipelineId: (id) => set({ pipelineId: id }),
  reset: () =>
    set({ pipelineId: null, phase: "idle", tasks: {}, plannerOutput: [] }),
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
            complexity: string;
          }>) {
            newTasks[t.id] = {
              id: t.id,
              title: t.title,
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
                    gate: data.gate as number,
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

        case "planner:output":
          return {
            plannerOutput: [...state.plannerOutput, data.line as string],
          };

        default:
          return state;
      }
    }),
}));

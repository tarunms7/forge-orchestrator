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
  phase: "idle" | "planning" | "executing" | "reviewing" | "complete";
  tasks: Record<string, TaskState>;
  plannerOutput: string[];
  setPipelineId: (id: string) => void;
  handleEvent: (event: {
    event: string;
    data: Record<string, unknown>;
  }) => void;
  reset: () => void;
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
            branch: string;
          }>) {
            newTasks[t.id] = {
              id: t.id,
              title: t.title,
              state: "pending",
              branch: t.branch,
              files: [],
              output: [],
              reviewGates: [],
            };
          }
          return { tasks: newTasks, phase: "executing" };
        }

        case "task:state_changed": {
          const taskId = data.taskId as string;
          const existing = state.tasks[taskId];
          if (!existing) return state;
          return {
            tasks: {
              ...state.tasks,
              [taskId]: {
                ...existing,
                state: data.newState as TaskState["state"],
              },
            },
          };
        }

        case "task:agent_output": {
          const taskId = data.taskId as string;
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
          const taskId = data.taskId as string;
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
          const taskId = data.taskId as string;
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
                    result: data.result as string,
                    details: data.details as string,
                  },
                ],
              },
            },
          };
        }

        case "task:merge_result": {
          const taskId = data.taskId as string;
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

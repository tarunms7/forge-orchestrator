"use client";

import { useCallback, useMemo, useState } from "react";
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  verticalListSortingStrategy,
  arrayMove,
} from "@dnd-kit/sortable";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import EditableTaskCard from "./EditableTaskCard";
import AddTaskForm from "./AddTaskForm";
import type { EditableTask } from "./AddTaskForm";
import PlanValidationBanner from "./PlanValidationBanner";
import type { ValidationResult } from "./PlanValidationBanner";

/* ── Client-side Validation ───────────────────────────────────────── */

function hasTransitiveDep(
  tasks: EditableTask[],
  fromId: string,
  toId: string,
): boolean {
  const visited = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    if (node === toId) return true;
    visited.add(node);
    for (const dep of adj[node] || []) {
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    return false;
  }
  return dfs(fromId);
}

function validateTaskGraph(tasks: EditableTask[]): ValidationResult {
  const errors: string[] = [];
  const ids = new Set(tasks.map((t) => t.id));

  // 1. No empty tasks
  if (tasks.length === 0) {
    errors.push("Plan must have at least one task.");
  }

  // 2. No duplicate IDs
  const seenIds = new Set<string>();
  for (const t of tasks) {
    if (seenIds.has(t.id)) errors.push(`Duplicate task ID: "${t.id}".`);
    seenIds.add(t.id);
  }

  // 3. All dependencies reference valid IDs
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!ids.has(dep)) {
        errors.push(`Task "${t.id}" depends on unknown task "${dep}".`);
      }
    }
  }

  // 4. No self-dependencies
  for (const t of tasks) {
    if (t.depends_on.includes(t.id)) {
      errors.push(`Task "${t.id}" depends on itself.`);
    }
  }

  // 5. Cycle detection (DFS)
  const visited = new Set<string>();
  const inStack = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    visited.add(node);
    inStack.add(node);
    for (const dep of adj[node] || []) {
      if (inStack.has(dep)) {
        errors.push(`Cycle detected involving tasks: ${node} \u2192 ${dep}.`);
        return true;
      }
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    inStack.delete(node);
    return false;
  }
  for (const t of tasks) {
    if (!visited.has(t.id)) dfs(t.id);
  }

  // 6. Every task must have at least one file
  for (const t of tasks) {
    if (t.files.length === 0) {
      errors.push(`Task "${t.id}" must declare at least one target file.`);
    }
  }

  // 7. No file conflicts between independent tasks
  const fileOwners: Record<string, string> = {};
  for (const t of tasks) {
    for (const f of t.files) {
      if (
        f in fileOwners &&
        !hasTransitiveDep(tasks, t.id, fileOwners[f]) &&
        !hasTransitiveDep(tasks, fileOwners[f], t.id)
      ) {
        errors.push(
          `File "${f}" is claimed by both "${fileOwners[f]}" and "${t.id}" ` +
            `with no dependency between them.`,
        );
      }
      if (!(f in fileOwners)) fileOwners[f] = t.id;
    }
  }

  // 8. Non-empty title
  for (const t of tasks) {
    if (!t.title.trim()) errors.push(`Task "${t.id}" has an empty title.`);
  }

  return { valid: errors.length === 0, errors };
}

/* ── Editable Plan Panel ──────────────────────────────────────────── */

export default function EditablePlanPanel() {
  const tasks = useTaskStore((s) => s.tasks);
  const pipelineId = useTaskStore((s) => s.pipelineId);
  const token = useAuthStore((s) => s.token);

  // Convert store tasks to EditableTask format
  const initialTasks: EditableTask[] = useMemo(
    () =>
      Object.values(tasks).map((t) => ({
        id: t.id,
        title: t.title,
        description: t.description || "",
        files: t.targetFiles || [],
        depends_on: t.dependsOn || [],
        complexity: (t.complexity as EditableTask["complexity"]) || "medium",
      })),
    [tasks],
  );

  const [editedTasks, setEditedTasks] = useState<EditableTask[]>(initialTasks);
  const [showAddForm, setShowAddForm] = useState(false);
  const [executing, setExecuting] = useState(false);

  // Validate on every change
  const validation = useMemo(() => validateTaskGraph(editedTasks), [editedTasks]);

  // dnd-kit sensors
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      const { active, over } = event;
      if (!over || active.id === over.id) return;

      const oldIndex = editedTasks.findIndex((t) => t.id === active.id);
      const newIndex = editedTasks.findIndex((t) => t.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      setEditedTasks(arrayMove(editedTasks, oldIndex, newIndex));
    },
    [editedTasks],
  );

  const handleUpdateTask = useCallback(
    (id: string, patch: Partial<EditableTask>) => {
      setEditedTasks((prev) =>
        prev.map((t) => (t.id === id ? { ...t, ...patch } : t)),
      );
    },
    [],
  );

  const handleDeleteTask = useCallback((id: string) => {
    setEditedTasks((prev) => {
      // Remove the task and remove its ID from all depends_on arrays
      return prev
        .filter((t) => t.id !== id)
        .map((t) => ({
          ...t,
          depends_on: t.depends_on.filter((dep) => dep !== id),
        }));
    });
  }, []);

  const handleAddTask = useCallback((task: EditableTask) => {
    setEditedTasks((prev) => [...prev, task]);
    setShowAddForm(false);
  }, []);

  async function handleExecute() {
    if (!token || !pipelineId || !validation.valid) return;
    setExecuting(true);
    try {
      // Check if the user made edits by comparing with initial tasks
      const hasEdits =
        JSON.stringify(editedTasks) !== JSON.stringify(initialTasks);
      const payload = hasEdits ? { tasks: editedTasks } : {};
      await apiPost(`/tasks/${pipelineId}/execute`, payload, token);
    } catch {
      // Errors will surface via WS events
    } finally {
      setExecuting(false);
    }
  }

  const pipelinePrefix = pipelineId ? pipelineId.slice(0, 8) : "task";

  return (
    <div className="plan-review-container mb-8">
      {/* Header */}
      <div className="plan-header">
        <h2 className="section-title">
          Plan &mdash; {editedTasks.length} task
          {editedTasks.length !== 1 ? "s" : ""}
        </h2>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          <button
            type="button"
            onClick={() => setShowAddForm(true)}
            disabled={showAddForm}
            className="btn btn-ghost"
            style={{
              padding: "8px 14px",
              fontSize: 13,
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              cursor: showAddForm ? "not-allowed" : "pointer",
              opacity: showAddForm ? 0.5 : 1,
            }}
          >
            + Add Task
          </button>
          <button
            type="button"
            onClick={handleExecute}
            disabled={!validation.valid || executing}
            className="btn btn-primary btn-glow"
            style={{
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              opacity: !validation.valid || executing ? 0.4 : 1,
              cursor:
                !validation.valid || executing ? "not-allowed" : "pointer",
            }}
          >
            {executing ? "Starting..." : "Execute Plan"}
          </button>
        </div>
      </div>

      {/* Task list with drag-and-drop */}
      <div className="plan-tasks">
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={editedTasks.map((t) => t.id)}
            strategy={verticalListSortingStrategy}
          >
            {editedTasks.map((task) => (
              <EditableTaskCard
                key={task.id}
                task={task}
                allTasks={editedTasks}
                onUpdate={handleUpdateTask}
                onDelete={handleDeleteTask}
              />
            ))}
          </SortableContext>
        </DndContext>
      </div>

      {/* Add task form */}
      {showAddForm && (
        <div style={{ marginTop: 12 }}>
          <AddTaskForm
            existingTaskIds={editedTasks.map((t) => t.id)}
            pipelinePrefix={pipelinePrefix}
            onAdd={handleAddTask}
            onCancel={() => setShowAddForm(false)}
          />
        </div>
      )}

      {/* Validation banner */}
      <div style={{ marginTop: 16 }}>
        <PlanValidationBanner validation={validation} />
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
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
} from "@dnd-kit/sortable";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";
import type { EditableTask } from "@/lib/validateTaskGraph";
import EditableTaskCard from "./EditableTaskCard";
import AddTaskForm from "./AddTaskForm";
import PlanValidationBanner from "./PlanValidationBanner";

/* ── Editable Plan Panel ──────────────────────────────────────────── */

export default function EditablePlanPanel() {
  const tasks = useTaskStore((s) => s.tasks);
  const pipelineId = useTaskStore((s) => s.pipelineId);
  const editedTasks = useTaskStore((s) => s.editedTasks);
  const planValidation = useTaskStore((s) => s.planValidation);
  const setEditedTasks = useTaskStore((s) => s.setEditedTasks);
  const reorderEditedTasks = useTaskStore((s) => s.reorderEditedTasks);
  const token = useAuthStore((s) => s.token);

  const [showAddForm, setShowAddForm] = useState(false);
  const [executing, setExecuting] = useState(false);

  // Initialize editedTasks from store tasks if not yet set (e.g. REST hydration)
  useEffect(() => {
    if (editedTasks === null && Object.keys(tasks).length > 0) {
      const initial: EditableTask[] = Object.values(tasks).map((t) => ({
        id: t.id,
        title: t.title,
        description: t.description || "",
        files: t.targetFiles || [],
        depends_on: t.dependsOn || [],
        complexity: (t.complexity as EditableTask["complexity"]) || "medium",
      }));
      setEditedTasks(initial);
    }
  }, [editedTasks, tasks, setEditedTasks]);

  const taskList = editedTasks || [];

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

      const oldIndex = taskList.findIndex((t) => t.id === active.id);
      const newIndex = taskList.findIndex((t) => t.id === over.id);
      if (oldIndex === -1 || newIndex === -1) return;

      reorderEditedTasks(oldIndex, newIndex);
    },
    [taskList, reorderEditedTasks],
  );

  // Memoize initial tasks for edit detection
  const initialTasksJson = useMemo(
    () =>
      JSON.stringify(
        Object.values(tasks).map((t) => ({
          id: t.id,
          title: t.title,
          description: t.description || "",
          files: t.targetFiles || [],
          depends_on: t.dependsOn || [],
          complexity: t.complexity || "medium",
        })),
      ),
    [tasks],
  );

  async function handleExecute() {
    if (!token || !pipelineId || !planValidation.valid) return;
    setExecuting(true);
    try {
      const hasEdits = JSON.stringify(taskList) !== initialTasksJson;
      const payload = hasEdits ? { tasks: taskList } : {};
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
          Plan &mdash; {taskList.length} task
          {taskList.length !== 1 ? "s" : ""}
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
            disabled={!planValidation.valid || executing}
            className="btn btn-primary btn-glow"
            style={{
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              opacity: !planValidation.valid || executing ? 0.4 : 1,
              cursor:
                !planValidation.valid || executing ? "not-allowed" : "pointer",
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
            items={taskList.map((t) => t.id)}
            strategy={verticalListSortingStrategy}
          >
            {taskList.map((task) => (
              <EditableTaskCard key={task.id} taskId={task.id} />
            ))}
          </SortableContext>
        </DndContext>
      </div>

      {/* Add task form */}
      {showAddForm && (
        <div style={{ marginTop: 12 }}>
          <AddTaskForm
            pipelinePrefix={pipelinePrefix}
            onDone={() => setShowAddForm(false)}
            onCancel={() => setShowAddForm(false)}
          />
        </div>
      )}

      {/* Validation banner */}
      <div style={{ marginTop: 16 }}>
        <PlanValidationBanner />
      </div>
    </div>
  );
}

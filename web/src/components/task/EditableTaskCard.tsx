"use client";

import { useState } from "react";
import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { createPortal } from "react-dom";
import { useTaskStore } from "@/stores/taskStore";
import type { EditableTask } from "@/lib/validateTaskGraph";

const COMPLEXITY_OPTIONS: EditableTask["complexity"][] = ["low", "medium", "high"];

/* ── Delete Confirmation Dialog ───────────────────────────────────── */

function DeleteConfirmDialog({
  taskId,
  dependentTasks,
  onConfirm,
  onCancel,
}: {
  taskId: string;
  dependentTasks: { id: string; title: string }[];
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return createPortal(
    <div
      className="log-modal-overlay"
      onClick={onCancel}
      style={{ zIndex: 9999 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg-surface-2)",
          borderRadius: "var(--radius-lg)",
          border: "1px solid var(--border)",
          padding: 24,
          maxWidth: 420,
          width: "90%",
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        <h3
          style={{
            fontSize: 16,
            fontWeight: 600,
            color: "var(--text-primary)",
            marginBottom: 12,
          }}
        >
          Delete Task &ldquo;{taskId}&rdquo;?
        </h3>
        <p
          style={{
            fontSize: 13,
            color: "var(--text-secondary)",
            lineHeight: 1.5,
            marginBottom: 12,
          }}
        >
          The following tasks depend on this task:
        </p>
        <ul
          style={{
            margin: "0 0 12px 0",
            paddingLeft: 20,
            fontSize: 13,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}
        >
          {dependentTasks.map((dt) => (
            <li key={dt.id}>
              <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                {dt.id}
              </span>
              : {dt.title}
            </li>
          ))}
        </ul>
        <p
          style={{
            fontSize: 13,
            color: "var(--text-tertiary)",
            lineHeight: 1.5,
            marginBottom: 20,
          }}
        >
          Their dependency on &ldquo;{taskId}&rdquo; will be removed. You may need to
          adjust them.
        </p>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button
            type="button"
            onClick={onCancel}
            className="btn btn-ghost"
            style={{
              padding: "8px 16px",
              fontSize: 13,
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              cursor: "pointer",
            }}
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            className="btn"
            style={{
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              background: "var(--red)",
              color: "white",
              borderRadius: "var(--radius-md)",
              border: "none",
              cursor: "pointer",
            }}
          >
            Delete Anyway
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Editable Task Card ───────────────────────────────────────────── */

export default function EditableTaskCard({ taskId }: { taskId: string }) {
  const editedTasks = useTaskStore((s) => s.editedTasks) || [];
  const updateEditedTask = useTaskStore((s) => s.updateEditedTask);
  const deleteEditedTask = useTaskStore((s) => s.deleteEditedTask);

  const task = editedTasks.find((t) => t.id === taskId);

  const [expanded, setExpanded] = useState(false);
  const [showDeleteDialog, setShowDeleteDialog] = useState(false);
  const [fileInput, setFileInput] = useState("");

  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: taskId });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  if (!task) return null;

  // Find tasks that depend on this one
  const dependentTasks = editedTasks.filter((t) =>
    t.depends_on.includes(task.id),
  );

  function handleDeleteClick() {
    if (dependentTasks.length > 0) {
      setShowDeleteDialog(true);
    } else {
      deleteEditedTask(task!.id);
    }
  }

  function handleConfirmDelete() {
    setShowDeleteDialog(false);
    deleteEditedTask(task!.id);
  }

  function handleAddFile() {
    const trimmed = fileInput.trim();
    if (trimmed && !task!.files.includes(trimmed)) {
      updateEditedTask(task!.id, { files: [...task!.files, trimmed] });
      setFileInput("");
    }
  }

  function handleFileKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddFile();
    }
  }

  function handleRemoveFile(file: string) {
    updateEditedTask(task!.id, { files: task!.files.filter((f) => f !== file) });
  }

  function toggleDependency(depId: string) {
    if (task!.depends_on.includes(depId)) {
      updateEditedTask(task!.id, {
        depends_on: task!.depends_on.filter((d) => d !== depId),
      });
    } else {
      updateEditedTask(task!.id, { depends_on: [...task!.depends_on, depId] });
    }
  }

  // Available dependencies: all other tasks except self
  const availableDeps = editedTasks.filter((t) => t.id !== task.id);

  return (
    <>
      <div
        ref={setNodeRef}
        style={{
          ...style,
          background: "var(--bg-surface-2)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-lg)",
          padding: "12px 16px",
        }}
      >
        {/* Top row: drag handle, ID badge, title input, complexity, delete */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {/* Drag handle */}
          <button
            {...attributes}
            {...listeners}
            type="button"
            style={{
              cursor: "grab",
              background: "none",
              border: "none",
              color: "var(--text-dim)",
              padding: "2px 4px",
              fontSize: 16,
              lineHeight: 1,
              flexShrink: 0,
              touchAction: "none",
            }}
            title="Drag to reorder"
          >
            &#9776;
          </button>

          {/* Task ID badge */}
          <span
            className="task-number"
            style={{
              flexShrink: 0,
              fontSize: 11,
              fontFamily: "var(--font-mono)",
            }}
          >
            {task.id}
          </span>

          {/* Title input */}
          <input
            type="text"
            value={task.title}
            onChange={(e) => updateEditedTask(task.id, { title: e.target.value })}
            placeholder="Task title..."
            style={{
              flex: 1,
              padding: "6px 10px",
              fontSize: 13,
              background: "var(--bg-surface-3)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-primary)",
              outline: "none",
            }}
          />

          {/* Complexity dropdown */}
          <select
            value={task.complexity}
            onChange={(e) =>
              updateEditedTask(task.id, {
                complexity: e.target.value as EditableTask["complexity"],
              })
            }
            style={{
              padding: "6px 8px",
              fontSize: 12,
              background: "var(--bg-surface-3)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--text-secondary)",
              outline: "none",
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            {COMPLEXITY_OPTIONS.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>

          {/* Delete button */}
          <button
            type="button"
            onClick={handleDeleteClick}
            title="Delete task"
            style={{
              background: "none",
              border: "none",
              color: "var(--text-dim)",
              cursor: "pointer",
              padding: "4px",
              fontSize: 14,
              flexShrink: 0,
            }}
          >
            <svg
              width="16"
              height="16"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
              />
            </svg>
          </button>
        </div>

        {/* Expand toggle */}
        <button
          type="button"
          onClick={() => setExpanded(!expanded)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            marginTop: 8,
            background: "none",
            border: "none",
            color: "var(--text-dim)",
            cursor: "pointer",
            padding: "2px 0",
            fontSize: 12,
          }}
        >
          <svg
            className={`transition-transform ${expanded ? "rotate-90" : ""}`}
            style={{ width: 12, height: 12 }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          {expanded ? "Collapse" : "Expand details"}
          {task.depends_on.length > 0 && !expanded && (
            <span style={{ color: "var(--text-dim)", marginLeft: 8 }}>
              Depends: {task.depends_on.join(", ")}
            </span>
          )}
        </button>

        {/* Expanded section */}
        {expanded && (
          <div
            style={{
              marginTop: 12,
              padding: "12px 16px",
              background: "var(--bg-surface-3)",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--border-subtle)",
            }}
          >
            {/* Description */}
            <div style={{ marginBottom: 12 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                  marginBottom: 4,
                }}
              >
                Description
              </label>
              <textarea
                rows={3}
                value={task.description}
                onChange={(e) =>
                  updateEditedTask(task.id, { description: e.target.value })
                }
                placeholder="Detailed description..."
                style={{
                  width: "100%",
                  padding: "8px 12px",
                  fontSize: 13,
                  background: "var(--bg-surface-2)",
                  border: "1px solid var(--border)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--text-primary)",
                  outline: "none",
                  resize: "vertical",
                }}
              />
            </div>

            {/* Target files */}
            <div style={{ marginBottom: 12 }}>
              <label
                style={{
                  display: "block",
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-tertiary)",
                  marginBottom: 4,
                }}
              >
                Target Files
              </label>
              <div
                style={{
                  display: "flex",
                  flexWrap: "wrap",
                  gap: 6,
                  marginBottom: 6,
                }}
              >
                {task.files.map((file) => (
                  <span
                    key={file}
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 4,
                      padding: "3px 8px",
                      fontSize: 12,
                      fontFamily: "var(--font-mono)",
                      background: "var(--bg-surface-4)",
                      border: "1px solid var(--border)",
                      borderRadius: "var(--radius-sm)",
                      color: "var(--text-secondary)",
                    }}
                  >
                    {file}
                    <button
                      type="button"
                      onClick={() => handleRemoveFile(file)}
                      style={{
                        background: "none",
                        border: "none",
                        color: "var(--text-dim)",
                        cursor: "pointer",
                        padding: 0,
                        fontSize: 14,
                        lineHeight: 1,
                      }}
                    >
                      &times;
                    </button>
                  </span>
                ))}
              </div>
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  type="text"
                  value={fileInput}
                  onChange={(e) => setFileInput(e.target.value)}
                  onKeyDown={handleFileKeyDown}
                  placeholder="path/to/file.ts"
                  style={{
                    flex: 1,
                    padding: "6px 10px",
                    fontSize: 12,
                    fontFamily: "var(--font-mono)",
                    background: "var(--bg-surface-2)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-primary)",
                    outline: "none",
                  }}
                />
                <button
                  type="button"
                  onClick={handleAddFile}
                  disabled={!fileInput.trim()}
                  style={{
                    padding: "6px 12px",
                    fontSize: 12,
                    background: "var(--bg-surface-4)",
                    border: "1px solid var(--border)",
                    borderRadius: "var(--radius-sm)",
                    color: "var(--text-secondary)",
                    cursor: fileInput.trim() ? "pointer" : "not-allowed",
                    opacity: fileInput.trim() ? 1 : 0.5,
                  }}
                >
                  + Add
                </button>
              </div>
            </div>

            {/* Dependencies */}
            {availableDeps.length > 0 && (
              <div>
                <label
                  style={{
                    display: "block",
                    fontSize: 12,
                    fontWeight: 500,
                    color: "var(--text-tertiary)",
                    marginBottom: 4,
                  }}
                >
                  Dependencies
                </label>
                <div
                  style={{ display: "flex", flexWrap: "wrap", gap: 6 }}
                >
                  {availableDeps.map((dep) => {
                    const selected = task.depends_on.includes(dep.id);
                    return (
                      <button
                        key={dep.id}
                        type="button"
                        onClick={() => toggleDependency(dep.id)}
                        style={{
                          padding: "4px 10px",
                          fontSize: 12,
                          fontFamily: "var(--font-mono)",
                          background: selected
                            ? "var(--accent-glow)"
                            : "var(--bg-surface-2)",
                          border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                          borderRadius: "var(--radius-sm)",
                          color: selected
                            ? "var(--accent)"
                            : "var(--text-dim)",
                          cursor: "pointer",
                        }}
                      >
                        {dep.id}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Delete confirmation dialog */}
      {showDeleteDialog && (
        <DeleteConfirmDialog
          taskId={task.id}
          dependentTasks={dependentTasks.map((t) => ({
            id: t.id,
            title: t.title,
          }))}
          onConfirm={handleConfirmDelete}
          onCancel={() => setShowDeleteDialog(false)}
        />
      )}
    </>
  );
}

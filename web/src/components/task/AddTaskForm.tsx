"use client";

import { useState } from "react";

export interface EditableTask {
  id: string;
  title: string;
  description: string;
  files: string[];
  depends_on: string[];
  complexity: "low" | "medium" | "high";
}

const COMPLEXITY_OPTIONS: { value: EditableTask["complexity"]; label: string }[] = [
  { value: "low", label: "Low" },
  { value: "medium", label: "Medium" },
  { value: "high", label: "High" },
];

/**
 * Generate a unique task ID using the pipeline prefix.
 * Pattern: `{prefix}-task-{N}` where prefix is first 8 chars of pipeline ID.
 */
export function generateTaskId(existingIds: Set<string>, prefix: string): string {
  let counter = existingIds.size + 1;
  while (existingIds.has(`${prefix}-task-${counter}`)) counter++;
  return `${prefix}-task-${counter}`;
}

export default function AddTaskForm({
  existingTaskIds,
  pipelinePrefix,
  onAdd,
  onCancel,
}: {
  existingTaskIds: string[];
  pipelinePrefix: string;
  onAdd: (task: EditableTask) => void;
  onCancel: () => void;
}) {
  const idSet = new Set(existingTaskIds);
  const generatedId = generateTaskId(idSet, pipelinePrefix);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [files, setFiles] = useState<string[]>([]);
  const [fileInput, setFileInput] = useState("");
  const [dependsOn, setDependsOn] = useState<string[]>([]);
  const [complexity, setComplexity] = useState<EditableTask["complexity"]>("medium");

  const canSubmit = title.trim().length > 0;

  function handleAddFile() {
    const trimmed = fileInput.trim();
    if (trimmed && !files.includes(trimmed)) {
      setFiles([...files, trimmed]);
      setFileInput("");
    }
  }

  function handleRemoveFile(file: string) {
    setFiles(files.filter((f) => f !== file));
  }

  function handleFileKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      handleAddFile();
    }
  }

  function toggleDependency(depId: string) {
    if (dependsOn.includes(depId)) {
      setDependsOn(dependsOn.filter((d) => d !== depId));
    } else {
      setDependsOn([...dependsOn, depId]);
    }
  }

  function handleSubmit() {
    if (!canSubmit) return;
    onAdd({
      id: generatedId,
      title: title.trim(),
      description: description.trim(),
      files,
      depends_on: dependsOn,
      complexity,
    });
  }

  return (
    <div
      style={{
        background: "var(--bg-surface-2)",
        border: "1px solid var(--accent)",
        borderRadius: "var(--radius-lg)",
        padding: 20,
      }}
    >
      {/* Header */}
      <div
        style={{
          fontSize: 14,
          fontWeight: 600,
          color: "var(--accent)",
          marginBottom: 16,
        }}
      >
        New Task
      </div>

      {/* Auto-generated ID */}
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
          Task ID
        </label>
        <div
          style={{
            fontSize: 13,
            fontFamily: "var(--font-mono)",
            color: "var(--text-dim)",
            padding: "6px 10px",
            background: "var(--bg-surface-3)",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--border)",
          }}
        >
          {generatedId}
        </div>
      </div>

      {/* Title */}
      <div style={{ marginBottom: 12 }}>
        <label
          htmlFor="new-task-title"
          style={{
            display: "block",
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            marginBottom: 4,
          }}
        >
          Title <span style={{ color: "var(--red)" }}>*</span>
        </label>
        <input
          id="new-task-title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Describe the task..."
          style={{
            width: "100%",
            padding: "8px 12px",
            fontSize: 13,
            background: "var(--bg-surface-3)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
            color: "var(--text-primary)",
            outline: "none",
          }}
        />
      </div>

      {/* Description */}
      <div style={{ marginBottom: 12 }}>
        <label
          htmlFor="new-task-desc"
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
          id="new-task-desc"
          rows={3}
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="Detailed description (optional)..."
          style={{
            width: "100%",
            padding: "8px 12px",
            fontSize: 13,
            background: "var(--bg-surface-3)",
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-md)",
            color: "var(--text-primary)",
            outline: "none",
            resize: "vertical",
          }}
        />
      </div>

      {/* Files */}
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
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
          {files.map((file) => (
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
              background: "var(--bg-surface-3)",
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
      {existingTaskIds.length > 0 && (
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
            Dependencies
          </label>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {existingTaskIds.map((taskId) => {
              const selected = dependsOn.includes(taskId);
              return (
                <button
                  key={taskId}
                  type="button"
                  onClick={() => toggleDependency(taskId)}
                  style={{
                    padding: "4px 10px",
                    fontSize: 12,
                    fontFamily: "var(--font-mono)",
                    background: selected ? "var(--accent-glow)" : "var(--bg-surface-3)",
                    border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
                    borderRadius: "var(--radius-sm)",
                    color: selected ? "var(--accent)" : "var(--text-dim)",
                    cursor: "pointer",
                  }}
                >
                  {taskId}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {/* Complexity */}
      <div style={{ marginBottom: 16 }}>
        <label
          style={{
            display: "block",
            fontSize: 12,
            fontWeight: 500,
            color: "var(--text-tertiary)",
            marginBottom: 4,
          }}
        >
          Complexity
        </label>
        <div style={{ display: "flex", gap: 8 }}>
          {COMPLEXITY_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => setComplexity(opt.value)}
              className={`complexity-badge ${opt.value}`}
              style={{
                cursor: "pointer",
                opacity: complexity === opt.value ? 1 : 0.5,
                border:
                  complexity === opt.value
                    ? "1px solid currentColor"
                    : "1px solid var(--border)",
              }}
            >
              {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Actions */}
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
          onClick={handleSubmit}
          disabled={!canSubmit}
          className="btn btn-primary"
          style={{
            padding: "8px 20px",
            fontSize: 13,
            fontWeight: 600,
            opacity: canSubmit ? 1 : 0.4,
            cursor: canSubmit ? "pointer" : "not-allowed",
          }}
        >
          Add to Plan
        </button>
      </div>
    </div>
  );
}

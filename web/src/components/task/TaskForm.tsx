"use client";

export type Priority = "low" | "medium" | "high";

export interface TaskFormData {
  description: string;
  priority: Priority;
  additionalContext: string;
}

interface TaskFormProps {
  value: TaskFormData;
  onChange: (data: TaskFormData) => void;
}

const PRIORITY_OPTIONS: { value: Priority; label: string; color: string }[] = [
  { value: "low", label: "Low", color: "text-green-400 border-green-700 bg-green-950" },
  { value: "medium", label: "Medium", color: "text-yellow-400 border-yellow-700 bg-yellow-950" },
  { value: "high", label: "High", color: "text-red-400 border-red-700 bg-red-950" },
];

const MAX_DESCRIPTION_LENGTH = 4000;
const MAX_CONTEXT_LENGTH = 2000;

export default function TaskForm({ value, onChange }: TaskFormProps) {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-text-primary">Describe Your Task</h2>
        <p className="mt-1 text-sm text-text-tertiary">
          Tell Forge what you want to build, fix, or change.
        </p>
      </div>

      {/* Description textarea */}
      <div>
        <label htmlFor="task-description" className="block text-sm font-medium text-text-secondary">
          Task description
        </label>
        <textarea
          id="task-description"
          rows={6}
          value={value.description}
          onChange={(e) => onChange({ ...value, description: e.target.value })}
          maxLength={MAX_DESCRIPTION_LENGTH}
          placeholder="Build a REST API with user authentication, CRUD endpoints for posts, and unit tests..."
          className="mt-1 block w-full resize-y rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-1 flex items-center justify-between text-xs text-text-dim">
          <span>Supports markdown formatting</span>
          <span>
            {value.description.length}/{MAX_DESCRIPTION_LENGTH}
          </span>
        </div>
      </div>

      {/* Priority selector */}
      <div>
        <label className="block text-sm font-medium text-text-secondary">
          Priority <span className="text-text-dim">(optional)</span>
        </label>
        <div className="mt-2 flex gap-3">
          {PRIORITY_OPTIONS.map((option) => (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange({ ...value, priority: option.value })}
              className={`rounded-lg border px-4 py-1.5 text-sm font-medium transition ${
                value.priority === option.value
                  ? option.color
                  : "border-border-color bg-surface-3 text-text-tertiary hover:border-border-color/80"
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {/* Additional context textarea */}
      <div>
        <label htmlFor="task-context" className="block text-sm font-medium text-text-secondary">
          Additional context <span className="text-text-dim">(optional)</span>
        </label>
        <textarea
          id="task-context"
          rows={3}
          value={value.additionalContext}
          onChange={(e) => onChange({ ...value, additionalContext: e.target.value })}
          maxLength={MAX_CONTEXT_LENGTH}
          placeholder="Preferred libraries, coding style, or constraints..."
          className="mt-1 block w-full resize-y rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent"
        />
        <div className="mt-1 text-right text-xs text-text-dim">
          {value.additionalContext.length}/{MAX_CONTEXT_LENGTH}
        </div>
      </div>
    </div>
  );
}

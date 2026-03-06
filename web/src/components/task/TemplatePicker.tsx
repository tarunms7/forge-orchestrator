"use client";

import { useEffect, useState } from "react";
import { useAuthStore } from "@/stores/authStore";
import { apiGet } from "@/lib/api";

/* ── Types ─────────────────────────────────────────────────────────── */

export interface ReviewConfig {
  skip_l2: boolean;
  extra_review_pass: boolean;
  custom_review_focus: string;
}

export interface PipelineTemplate {
  id: string;
  name: string;
  description: string;
  icon: string;
  model_strategy: "auto" | "fast" | "quality";
  review_config: ReviewConfig;
  is_builtin: boolean;
  build_cmd?: string;
  test_cmd?: string;
  category?: string;
}

/* ── Built-in Templates ────────────────────────────────────────────── */

export const BUILTIN_TEMPLATES: PipelineTemplate[] = [
  {
    id: "feature",
    name: "New Feature",
    description:
      "Build a complete feature with implementation, tests, and documentation.",
    icon: "🚀",
    model_strategy: "auto",
    review_config: {
      skip_l2: false,
      extra_review_pass: false,
      custom_review_focus: "",
    },
    is_builtin: true,
    category: "development",
  },
  {
    id: "api",
    name: "REST API",
    description:
      "Build a RESTful API with CRUD endpoints, input validation, and error handling.",
    icon: "🔌",
    model_strategy: "auto",
    review_config: {
      skip_l2: false,
      extra_review_pass: false,
      custom_review_focus: "",
    },
    is_builtin: true,
    build_cmd: "npm run build",
    test_cmd: "npm test",
    category: "backend",
  },
  {
    id: "bugfix",
    name: "Bug Fix",
    description:
      "Diagnose and fix a bug. Investigate root cause, apply fix, add regression test.",
    icon: "🐛",
    model_strategy: "quality",
    review_config: {
      skip_l2: false,
      extra_review_pass: true,
      custom_review_focus: "regression risk",
    },
    is_builtin: true,
    category: "maintenance",
  },
  {
    id: "refactor",
    name: "Refactor",
    description:
      "Refactor code for readability, performance, or maintainability while preserving behavior.",
    icon: "🔧",
    model_strategy: "auto",
    review_config: {
      skip_l2: false,
      extra_review_pass: false,
      custom_review_focus: "",
    },
    is_builtin: true,
    category: "maintenance",
  },
  {
    id: "tests",
    name: "Add Tests",
    description:
      "Write comprehensive unit and integration tests with good coverage of edge cases.",
    icon: "🧪",
    model_strategy: "fast",
    review_config: {
      skip_l2: true,
      extra_review_pass: false,
      custom_review_focus: "",
    },
    is_builtin: true,
    test_cmd: "pytest",
    category: "testing",
  },
];

/* ── Component ─────────────────────────────────────────────────────── */

interface TemplatePickerProps {
  onSelect: (template: PipelineTemplate) => void;
  selectedId?: string;
}

export default function TemplatePicker({
  onSelect,
  selectedId,
}: TemplatePickerProps) {
  const token = useAuthStore((s) => s.token);
  const [userTemplates, setUserTemplates] = useState<PipelineTemplate[]>([]);
  const [showUserTemplates, setShowUserTemplates] = useState(false);

  // Fetch user templates on mount
  useEffect(() => {
    if (!token) return;
    let cancelled = false;

    async function fetchUserTemplates() {
      try {
        const data = await apiGet("/templates", token!);
        if (cancelled) return;
        // Backend returns { builtin: [...], user: [...] }
        const response = data as {
          builtin?: PipelineTemplate[];
          user?: PipelineTemplate[];
        };
        const mapped: PipelineTemplate[] = (response.user ?? []).map((t) => ({
          id: t.id,
          name: t.name,
          description: t.description,
          icon: t.icon || "📄",
          model_strategy: t.model_strategy || ("auto" as const),
          review_config: t.review_config || {
            skip_l2: false,
            extra_review_pass: false,
            custom_review_focus: "",
          },
          is_builtin: false,
          build_cmd: t.build_cmd,
          test_cmd: t.test_cmd,
        }));
        setUserTemplates(mapped);
      } catch {
        // silently ignore — user templates are optional
      }
    }

    fetchUserTemplates();
    return () => {
      cancelled = true;
    };
  }, [token]);

  const selected = selectedId ?? "feature";

  return (
    <div className="space-y-4">
      <div>
        <h3 className="text-sm font-medium text-text-secondary">
          Pipeline Template
        </h3>
        <p className="mt-0.5 text-xs text-text-dim">
          Choose a template to pre-configure the pipeline for your task type.
        </p>
      </div>

      {/* Built-in templates — 3-column card grid */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {BUILTIN_TEMPLATES.map((template) => {
          const isSelected = selected === template.id;

          return (
            <button
              key={template.id}
              type="button"
              onClick={() => onSelect(template)}
              className={`group relative flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition ${
                isSelected
                  ? "border-accent bg-surface-3/70"
                  : "border-border-color bg-surface-1 hover:border-border-color/80 hover:bg-surface-3/50"
              }`}
            >
              {/* Checkmark indicator */}
              {isSelected && (
                <div
                  style={{
                    position: "absolute",
                    top: "8px",
                    right: "8px",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    width: "20px",
                    height: "20px",
                    borderRadius: "50%",
                    background: "var(--accent)",
                  }}
                >
                  <svg
                    style={{ width: "12px", height: "12px", color: "white" }}
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                    strokeWidth={3}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M5 13l4 4L19 7"
                    />
                  </svg>
                </div>
              )}

              {/* Icon */}
              <span style={{ fontSize: "28px", lineHeight: 1 }}>
                {template.icon}
              </span>

              {/* Name */}
              <span className="text-sm font-semibold text-text-primary">
                {template.name}
              </span>

              {/* Description (2-line clamp) */}
              <p className="line-clamp-2 text-xs text-text-tertiary">
                {template.description}
              </p>
            </button>
          );
        })}
      </div>

      {/* User templates section */}
      {userTemplates.length > 0 && (
        <>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: "12px",
              margin: "4px 0",
            }}
          >
            <div
              style={{
                flex: 1,
                height: "1px",
                background: "var(--border)",
              }}
            />
            <button
              type="button"
              onClick={() => setShowUserTemplates((v) => !v)}
              className="text-xs font-medium text-text-tertiary hover:text-text-secondary"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "4px",
                background: "none",
                border: "none",
                cursor: "pointer",
                padding: "4px 8px",
                borderRadius: "var(--radius-md)",
              }}
            >
              My Templates ({userTemplates.length})
              <svg
                style={{
                  width: "12px",
                  height: "12px",
                  transform: showUserTemplates
                    ? "rotate(180deg)"
                    : "rotate(0deg)",
                  transition: "transform 0.2s",
                }}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M19 9l-7 7-7-7"
                />
              </svg>
            </button>
            <div
              style={{
                flex: 1,
                height: "1px",
                background: "var(--border)",
              }}
            />
          </div>

          {showUserTemplates && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {userTemplates.map((template) => {
                const isSelected = selected === template.id;

                return (
                  <button
                    key={template.id}
                    type="button"
                    onClick={() => onSelect(template)}
                    className={`group relative flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition ${
                      isSelected
                        ? "border-accent bg-surface-3/70"
                        : "border-border-color bg-surface-1 hover:border-border-color/80 hover:bg-surface-3/50"
                    }`}
                  >
                    {isSelected && (
                      <div
                        style={{
                          position: "absolute",
                          top: "8px",
                          right: "8px",
                          display: "flex",
                          alignItems: "center",
                          justifyContent: "center",
                          width: "20px",
                          height: "20px",
                          borderRadius: "50%",
                          background: "var(--accent)",
                        }}
                      >
                        <svg
                          style={{
                            width: "12px",
                            height: "12px",
                            color: "white",
                          }}
                          fill="none"
                          viewBox="0 0 24 24"
                          stroke="currentColor"
                          strokeWidth={3}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M5 13l4 4L19 7"
                          />
                        </svg>
                      </div>
                    )}

                    <span style={{ fontSize: "28px", lineHeight: 1 }}>
                      {template.icon}
                    </span>
                    <span className="text-sm font-semibold text-text-primary">
                      {template.name}
                    </span>
                    <p className="line-clamp-2 text-xs text-text-tertiary">
                      {template.description}
                    </p>
                  </button>
                );
              })}
            </div>
          )}
        </>
      )}
    </div>
  );
}

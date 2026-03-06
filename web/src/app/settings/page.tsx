"use client";

import { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useNotifications } from "@/hooks/useNotifications";

interface Settings {
  max_agents: number;
  timeout: number;
  max_retries: number;
  model_strategy: string;
  planner_model: string;
  agent_model_low: string;
  agent_model_medium: string;
  agent_model_high: string;
  reviewer_model: string;
}

interface PipelineTemplate {
  name: string;
  description: string;
  category: string;
  icon?: string;
  model_strategy?: string;
  build_command?: string;
  test_command?: string;
  planner_instructions?: string;
  agent_instructions?: string;
  review_focus?: string;
  skip_llm_review?: boolean;
  extra_review_pass?: boolean;
}

const EMPTY_TEMPLATE: PipelineTemplate = {
  name: "",
  description: "",
  category: "custom",
  icon: "",
  model_strategy: "auto",
  build_command: "",
  test_command: "",
  planner_instructions: "",
  agent_instructions: "",
  review_focus: "",
  skip_llm_review: false,
  extra_review_pass: false,
};

const DEFAULT_SETTINGS: Settings = {
  max_agents: 4,
  timeout: 600,
  max_retries: 3,
  model_strategy: "auto",
  planner_model: "opus",
  agent_model_low: "sonnet",
  agent_model_medium: "opus",
  agent_model_high: "opus",
  reviewer_model: "sonnet",
};

const MODEL_OPTIONS = ["opus", "sonnet", "haiku"] as const;
const STRATEGY_OPTIONS = ["auto", "fast", "quality"] as const;

function ModelSelect({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="setting-row">
      <div className="setting-label-group">
        <div className="setting-label">{label}</div>
      </div>
      <div className="setting-control">
        <select
          className="settings-select"
          value={value}
          onChange={(e) => onChange(e.target.value)}
        >
          {MODEL_OPTIONS.map((m) => (
            <option key={m} value={m}>
              {m.charAt(0).toUpperCase() + m.slice(1)}
            </option>
          ))}
        </select>
      </div>
    </div>
  );
}

/* ── Template Form (create / edit) ────────────────────────────────── */

function TemplateForm({
  initial,
  onSave,
  onCancel,
  saving,
}: {
  initial: PipelineTemplate;
  onSave: (t: PipelineTemplate) => void;
  onCancel: () => void;
  saving: boolean;
}) {
  const [form, setForm] = useState<PipelineTemplate>({ ...initial });

  const set = (field: keyof PipelineTemplate, value: string | boolean) =>
    setForm((f) => ({ ...f, [field]: value }));

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius-md)",
        background: "var(--bg-surface-2)",
        padding: "20px",
        marginTop: "8px",
      }}
    >
      {/* Name + Icon row */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "14px" }}>
        <div style={{ flex: 1 }}>
          <label
            style={{
              display: "block",
              fontSize: "12px",
              color: "var(--text-secondary)",
              marginBottom: "4px",
            }}
          >
            Name
          </label>
          <input
            className="text-input"
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="My Template"
            style={{ width: "100%" }}
          />
        </div>
        <div style={{ width: "80px" }}>
          <label
            style={{
              display: "block",
              fontSize: "12px",
              color: "var(--text-secondary)",
              marginBottom: "4px",
            }}
          >
            Icon
          </label>
          <input
            className="text-input"
            value={form.icon || ""}
            onChange={(e) => set("icon", e.target.value)}
            placeholder="📋"
            style={{ width: "100%", textAlign: "center" }}
          />
        </div>
      </div>

      {/* Description */}
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            fontSize: "12px",
            color: "var(--text-secondary)",
            marginBottom: "4px",
          }}
        >
          Description
        </label>
        <textarea
          className="text-input"
          value={form.description}
          onChange={(e) => set("description", e.target.value)}
          placeholder="Describe what this template does..."
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
        />
      </div>

      {/* Model Strategy */}
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            fontSize: "12px",
            color: "var(--text-secondary)",
            marginBottom: "4px",
          }}
        >
          Model Strategy
        </label>
        <select
          className="settings-select"
          value={form.model_strategy || "auto"}
          onChange={(e) => set("model_strategy", e.target.value)}
        >
          {STRATEGY_OPTIONS.map((s) => (
            <option key={s} value={s}>
              {s.charAt(0).toUpperCase() + s.slice(1)}
            </option>
          ))}
        </select>
      </div>

      {/* Build + Test commands */}
      <div style={{ display: "flex", gap: "12px", marginBottom: "14px" }}>
        <div style={{ flex: 1 }}>
          <label
            style={{
              display: "block",
              fontSize: "12px",
              color: "var(--text-secondary)",
              marginBottom: "4px",
            }}
          >
            Build Command
          </label>
          <input
            className="text-input mono"
            value={form.build_command || ""}
            onChange={(e) => set("build_command", e.target.value)}
            placeholder="npm run build"
            style={{ width: "100%" }}
          />
        </div>
        <div style={{ flex: 1 }}>
          <label
            style={{
              display: "block",
              fontSize: "12px",
              color: "var(--text-secondary)",
              marginBottom: "4px",
            }}
          >
            Test Command
          </label>
          <input
            className="text-input mono"
            value={form.test_command || ""}
            onChange={(e) => set("test_command", e.target.value)}
            placeholder="pytest"
            style={{ width: "100%" }}
          />
        </div>
      </div>

      {/* Planner Instructions */}
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            fontSize: "12px",
            color: "var(--text-secondary)",
            marginBottom: "4px",
          }}
        >
          Planner Instructions{" "}
          <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>
            Appended to planner prompt
          </span>
        </label>
        <textarea
          className="text-input mono"
          value={form.planner_instructions || ""}
          onChange={(e) => set("planner_instructions", e.target.value)}
          placeholder="Additional instructions for the planner..."
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
        />
      </div>

      {/* Agent Instructions */}
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            fontSize: "12px",
            color: "var(--text-secondary)",
            marginBottom: "4px",
          }}
        >
          Agent Instructions{" "}
          <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>
            Appended to each agent prompt
          </span>
        </label>
        <textarea
          className="text-input mono"
          value={form.agent_instructions || ""}
          onChange={(e) => set("agent_instructions", e.target.value)}
          placeholder="Additional instructions for agents..."
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
        />
      </div>

      {/* Review Focus */}
      <div style={{ marginBottom: "14px" }}>
        <label
          style={{
            display: "block",
            fontSize: "12px",
            color: "var(--text-secondary)",
            marginBottom: "4px",
          }}
        >
          Review Focus{" "}
          <span style={{ color: "var(--text-dim)", fontWeight: 400 }}>
            Appended to reviewer prompt
          </span>
        </label>
        <textarea
          className="text-input mono"
          value={form.review_focus || ""}
          onChange={(e) => set("review_focus", e.target.value)}
          placeholder="Focus areas for code review..."
          rows={2}
          style={{ width: "100%", resize: "vertical" }}
        />
      </div>

      {/* Checkboxes */}
      <div
        style={{
          display: "flex",
          gap: "24px",
          marginBottom: "18px",
        }}
      >
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            fontSize: "13px",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={form.skip_llm_review || false}
            onChange={(e) => set("skip_llm_review", e.target.checked)}
          />
          Skip LLM Review
        </label>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "8px",
            fontSize: "13px",
            color: "var(--text-secondary)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={form.extra_review_pass || false}
            onChange={(e) => set("extra_review_pass", e.target.checked)}
          />
          Extra Review Pass
        </label>
      </div>

      {/* Actions */}
      <div style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}>
        <button
          className="btn-sm-outline"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button
          className="btn btn-primary"
          onClick={() => onSave(form)}
          disabled={saving || !form.name.trim()}
          style={{ padding: "8px 20px", fontSize: "13px" }}
        >
          {saving ? "Saving..." : "Save Template"}
        </button>
      </div>
    </div>
  );
}

/* ── Delete Confirmation Dialog ───────────────────────────────────── */

function DeleteConfirmDialog({
  templateName,
  onConfirm,
  onCancel,
  deleting,
}: {
  templateName: string;
  onConfirm: () => void;
  onCancel: () => void;
  deleting: boolean;
}) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0,0,0,0.5)",
      }}
      onClick={onCancel}
    >
      <div
        style={{
          background: "var(--bg-surface-1)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-md)",
          padding: "24px",
          maxWidth: "400px",
          width: "90%",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <h3
          style={{
            fontSize: "15px",
            fontWeight: 600,
            color: "var(--text-primary)",
            marginBottom: "8px",
          }}
        >
          Delete Template
        </h3>
        <p
          style={{
            fontSize: "13px",
            color: "var(--text-secondary)",
            marginBottom: "20px",
          }}
        >
          Are you sure you want to delete{" "}
          <strong>&ldquo;{templateName}&rdquo;</strong>? This action cannot be
          undone.
        </p>
        <div
          style={{ display: "flex", gap: "10px", justifyContent: "flex-end" }}
        >
          <button
            className="btn-sm-outline"
            onClick={onCancel}
            disabled={deleting}
          >
            Cancel
          </button>
          <button
            className="btn-danger"
            onClick={onConfirm}
            disabled={deleting}
          >
            {deleting ? "Deleting..." : "Delete"}
          </button>
        </div>
      </div>
    </div>
  );
}

/* ── Main Settings Page ───────────────────────────────────────────── */

export default function SettingsPage() {
  const token = useAuthStore((s) => s.token);
  const { requestPermission } = useNotifications();
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [cliStatus, setCliStatus] = useState<string>("checking");

  // Template state
  const [templates, setTemplates] = useState<PipelineTemplate[]>([]);
  const [editingTemplate, setEditingTemplate] =
    useState<PipelineTemplate | null>(null);
  const [showCreateForm, setShowCreateForm] = useState(false);
  const [templateSaving, setTemplateSaving] = useState(false);
  const [deletingTemplate, setDeletingTemplate] = useState<string | null>(null);
  const [templateError, setTemplateError] = useState<string | null>(null);

  const fetchTemplates = useCallback(async () => {
    if (!token) return;
    try {
      const data = await apiGet("/templates", token);
      setTemplates(Array.isArray(data) ? data : []);
    } catch {
      // Templates endpoint might not be available — fail silently
    }
  }, [token]);

  useEffect(() => {
    if (!token) return;

    apiGet("/settings", token)
      .then((data) => setSettings(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));

    fetchTemplates();

    // Auto-check CLI status on load (health endpoint is at /health, not under /api)
    fetch("/health")
      .then((r) => (r.ok ? setCliStatus("connected") : setCliStatus("error")))
      .catch(() => setCliStatus("error"));
  }, [token, fetchTemplates]);

  const handleSave = async () => {
    if (!token) return;
    setSaving(true);
    setSaved(false);
    setError(null);

    try {
      const updated = await apiPut(
        "/settings",
        settings as unknown as Record<string, unknown>,
        token,
      );
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Failed to save settings",
      );
    } finally {
      setSaving(false);
    }
  };

  const checkCliStatus = () => {
    setCliStatus("checking");
    fetch("/health")
      .then((r) => (r.ok ? setCliStatus("connected") : setCliStatus("error")))
      .catch(() => setCliStatus("error"));
  };

  /* ── Template CRUD ────────────────────────────────────────────── */

  const handleCreateTemplate = async (template: PipelineTemplate) => {
    if (!token) return;
    setTemplateSaving(true);
    setTemplateError(null);
    try {
      await apiPost(
        "/templates",
        template as unknown as Record<string, unknown>,
        token,
      );
      setShowCreateForm(false);
      await fetchTemplates();
    } catch (err) {
      setTemplateError(
        err instanceof Error ? err.message : "Failed to create template",
      );
    } finally {
      setTemplateSaving(false);
    }
  };

  const handleUpdateTemplate = async (template: PipelineTemplate) => {
    if (!token) return;
    setTemplateSaving(true);
    setTemplateError(null);
    try {
      await apiPut(
        `/templates/${encodeURIComponent(template.name)}`,
        template as unknown as Record<string, unknown>,
        token,
      );
      setEditingTemplate(null);
      await fetchTemplates();
    } catch (err) {
      setTemplateError(
        err instanceof Error ? err.message : "Failed to update template",
      );
    } finally {
      setTemplateSaving(false);
    }
  };

  const handleDeleteTemplate = async (name: string) => {
    if (!token) return;
    setTemplateSaving(true);
    setTemplateError(null);
    try {
      await apiDelete(`/templates/${encodeURIComponent(name)}`, token);
      setDeletingTemplate(null);
      await fetchTemplates();
    } catch (err) {
      setTemplateError(
        err instanceof Error ? err.message : "Failed to delete template",
      );
    } finally {
      setTemplateSaving(false);
    }
  };

  const buildConfigSummary = (t: PipelineTemplate): string => {
    const parts: string[] = [];
    if (t.model_strategy) parts.push(`Strategy: ${t.model_strategy}`);
    if (t.build_command) parts.push(`Build: ${t.build_command}`);
    if (t.test_command) parts.push(`Test: ${t.test_command}`);
    return parts.length > 0 ? parts.join(" | ") : "No config set";
  };

  if (loading) {
    return (
      <div
        className="page-content"
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          minHeight: "400px",
        }}
      >
        <div
          style={{
            width: "192px",
            height: "6px",
            borderRadius: "999px",
            background: "var(--bg-surface-3)",
            overflow: "hidden",
          }}
        >
          <div
            style={{
              width: "33%",
              height: "100%",
              borderRadius: "999px",
              background: "var(--accent)",
              animation: "pulse 2s ease-in-out infinite",
            }}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="page-content">
      <div className="page-header">
        <h1 className="page-title">Settings</h1>
        <p className="page-subtitle">
          Configure your Forge pipeline preferences
        </p>
      </div>

      {error && (
        <div
          style={{
            marginBottom: 24,
            borderRadius: "var(--radius-md)",
            border: "1px solid rgba(239,68,68,0.3)",
            background: "var(--red-dim)",
            padding: "14px 16px",
            fontSize: "13px",
            color: "#fca5a5",
          }}
        >
          {error}
        </div>
      )}

      <div className="settings-container">
        {/* Pipeline Templates */}
        <div className="settings-group">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <rect
                x="2"
                y="2"
                width="5"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.3"
                fill="none"
                opacity="0.6"
              />
              <rect
                x="9"
                y="2"
                width="5"
                height="4"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.3"
                fill="none"
                opacity="0.6"
              />
              <rect
                x="2"
                y="10"
                width="5"
                height="4"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.3"
                fill="none"
                opacity="0.6"
              />
              <rect
                x="9"
                y="8"
                width="5"
                height="6"
                rx="1"
                stroke="currentColor"
                strokeWidth="1.3"
                fill="none"
                opacity="0.6"
              />
            </svg>
            <span className="settings-group-title">Pipeline Templates</span>
            <span className="settings-group-desc">
              Your saved pipeline templates
            </span>
          </div>

          {templateError && (
            <div
              style={{
                margin: "0 0 8px 0",
                borderRadius: "var(--radius-md)",
                border: "1px solid rgba(239,68,68,0.3)",
                background: "var(--red-dim)",
                padding: "10px 14px",
                fontSize: "12px",
                color: "#fca5a5",
              }}
            >
              {templateError}
            </div>
          )}

          {/* Template list */}
          {templates.length === 0 && !showCreateForm && (
            <div
              style={{
                padding: "20px 16px",
                textAlign: "center",
                fontSize: "13px",
                color: "var(--text-dim)",
              }}
            >
              No custom templates yet. Create one to get started.
            </div>
          )}

          {templates.map((t) => (
            <div key={t.name}>
              {editingTemplate?.name === t.name ? (
                <TemplateForm
                  initial={editingTemplate}
                  onSave={handleUpdateTemplate}
                  onCancel={() => setEditingTemplate(null)}
                  saving={templateSaving}
                />
              ) : (
                <div className="setting-row" style={{ alignItems: "flex-start" }}>
                  <div
                    className="setting-label-group"
                    style={{ flex: 1, minWidth: 0 }}
                  >
                    <div
                      className="setting-label"
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: "6px",
                      }}
                    >
                      <span>{t.icon || "📋"}</span>
                      <span style={{ fontWeight: 600 }}>{t.name}</span>
                    </div>
                    {t.description && (
                      <div
                        className="setting-hint"
                        style={{
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                          maxWidth: "400px",
                        }}
                      >
                        {t.description}
                      </div>
                    )}
                    <div
                      style={{
                        fontSize: "11px",
                        color: "var(--text-dim)",
                        fontFamily: "var(--font-mono, monospace)",
                        marginTop: "2px",
                      }}
                    >
                      {buildConfigSummary(t)}
                    </div>
                  </div>
                  <div
                    className="setting-control"
                    style={{
                      display: "flex",
                      gap: "6px",
                      flexShrink: 0,
                    }}
                  >
                    <button
                      className="btn-sm-outline"
                      onClick={() => setEditingTemplate({ ...t })}
                    >
                      Edit
                    </button>
                    <button
                      className="btn-sm-outline"
                      style={{
                        color: "var(--red, #ef4444)",
                        borderColor: "rgba(239,68,68,0.3)",
                      }}
                      onClick={() => setDeletingTemplate(t.name)}
                    >
                      Delete
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}

          {/* Create form */}
          {showCreateForm ? (
            <TemplateForm
              initial={EMPTY_TEMPLATE}
              onSave={handleCreateTemplate}
              onCancel={() => setShowCreateForm(false)}
              saving={templateSaving}
            />
          ) : (
            <div style={{ padding: "12px 0 4px" }}>
              <button
                className="btn-sm-outline"
                onClick={() => {
                  setShowCreateForm(true);
                  setEditingTemplate(null);
                  setTemplateError(null);
                }}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                <svg
                  width="14"
                  height="14"
                  viewBox="0 0 14 14"
                  fill="none"
                  style={{ opacity: 0.7 }}
                >
                  <path
                    d="M7 2v10M2 7h10"
                    stroke="currentColor"
                    strokeWidth="1.5"
                    strokeLinecap="round"
                  />
                </svg>
                Create New Template
              </button>
            </div>
          )}
        </div>

        {/* Pipeline Defaults */}
        <div className="settings-group">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M8 1a7 7 0 100 14A7 7 0 008 1zm0 2.5l1.7 3.4 3.8.6-2.75 2.7.65 3.8L8 12.4l-3.4 1.6.65-3.8L2.5 7.5l3.8-.6L8 3.5z"
                fill="currentColor"
                opacity="0.6"
              />
            </svg>
            <span className="settings-group-title">Pipeline Defaults</span>
            <span className="settings-group-desc">
              Applied to all new pipelines
            </span>
          </div>

          {/* Strategy row */}
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Model Strategy</div>
              <div className="setting-hint">
                Controls cost/quality tradeoff
              </div>
            </div>
            <div className="setting-control">
              <select
                className="settings-select"
                value={settings.model_strategy}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    model_strategy: e.target.value,
                  }))
                }
              >
                {STRATEGY_OPTIONS.map((s) => (
                  <option key={s} value={s}>
                    {s.charAt(0).toUpperCase() + s.slice(1)}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Max Agents row */}
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Max Parallel Workers</div>
              <div className="setting-hint">
                Number of agents that can run concurrently
              </div>
            </div>
            <div className="setting-control">
              <select
                className="settings-select"
                value={String(settings.max_agents)}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    max_agents: parseInt(e.target.value, 10),
                  }))
                }
              >
                {[1, 2, 3, 4, 5, 6, 8, 10, 12, 16].map((n) => (
                  <option key={n} value={String(n)}>
                    {n}
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* Timeout row */}
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Timeout (seconds)</div>
              <div className="setting-hint">
                Max time per agent before it is stopped
              </div>
            </div>
            <div className="setting-control">
              <input
                type="number"
                className="text-input mono"
                min={30}
                max={3600}
                value={settings.timeout}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    timeout: parseInt(e.target.value, 10) || 600,
                  }))
                }
              />
            </div>
          </div>

          {/* Max Retries row */}
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Max Retries</div>
              <div className="setting-hint">
                How many times to retry a failed agent task
              </div>
            </div>
            <div className="setting-control">
              <input
                type="number"
                className="text-input mono"
                min={0}
                max={10}
                value={settings.max_retries}
                onChange={(e) =>
                  setSettings((s) => ({
                    ...s,
                    max_retries: parseInt(e.target.value, 10) || 0,
                  }))
                }
              />
            </div>
          </div>
        </div>

        {/* Model Routing */}
        <div className="settings-group">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M13 3L4 14h7l-2 7 9-11h-7l2-7z"
                fill="currentColor"
                opacity="0.5"
                transform="scale(0.65) translate(1,1)"
              />
            </svg>
            <span className="settings-group-title">Model Routing</span>
            <span className="settings-group-desc">
              Per-role model assignments
            </span>
          </div>
          <ModelSelect
            label="Planner Model"
            value={settings.planner_model}
            onChange={(v) =>
              setSettings((s) => ({ ...s, planner_model: v }))
            }
          />
          <ModelSelect
            label="Agent — Low Complexity"
            value={settings.agent_model_low}
            onChange={(v) =>
              setSettings((s) => ({ ...s, agent_model_low: v }))
            }
          />
          <ModelSelect
            label="Agent — Medium Complexity"
            value={settings.agent_model_medium}
            onChange={(v) =>
              setSettings((s) => ({ ...s, agent_model_medium: v }))
            }
          />
          <ModelSelect
            label="Agent — High Complexity"
            value={settings.agent_model_high}
            onChange={(v) =>
              setSettings((s) => ({ ...s, agent_model_high: v }))
            }
          />
          <ModelSelect
            label="Reviewer Model"
            value={settings.reviewer_model}
            onChange={(v) =>
              setSettings((s) => ({ ...s, reviewer_model: v }))
            }
          />
        </div>

        {/* Claude SDK */}
        <div className="settings-group">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M13 3L4 14h7l-2 7 9-11h-7l2-7z"
                fill="currentColor"
                opacity="0.5"
                transform="scale(0.65) translate(1,1)"
              />
            </svg>
            <span className="settings-group-title">Claude SDK</span>
          </div>
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Authentication Status</div>
              <div className="setting-hint">
                {cliStatus === "connected"
                  ? "Claude CLI is reachable"
                  : cliStatus === "error"
                    ? "Could not reach Claude CLI"
                    : "Checking connection..."}
              </div>
            </div>
            <div className="setting-control">
              <div className="auth-status">
                <div
                  className={`status-dot-lg ${cliStatus === "connected" ? "green" : cliStatus === "error" ? "red" : ""}`}
                ></div>
                <span
                  className={`auth-status-text ${cliStatus === "connected" ? "connected" : ""}`}
                >
                  {cliStatus === "connected"
                    ? "Connected"
                    : cliStatus === "error"
                      ? "Unreachable"
                      : "Checking..."}
                </span>
                <button
                  className="btn-sm-outline"
                  onClick={checkCliStatus}
                  disabled={cliStatus === "checking"}
                >
                  Refresh
                </button>
              </div>
            </div>
          </div>
        </div>

        {/* Security */}
        <div className="settings-group">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M8 1L3 4v4c0 3.5 2.1 6.3 5 7 2.9-.7 5-3.5 5-7V4L8 1z"
                stroke="currentColor"
                strokeWidth="1.5"
                fill="none"
              />
            </svg>
            <span className="settings-group-title">Security</span>
          </div>
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Change Password</div>
              <div className="setting-hint">Update your account password</div>
            </div>
            <div className="setting-control">
              <button
                className="btn-sm-outline"
                disabled
                style={{ opacity: 0.5 }}
              >
                Coming soon
              </button>
            </div>
          </div>
        </div>

        {/* Danger Zone */}
        <div className="settings-group danger">
          <div className="settings-group-header">
            <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
              <path
                d="M8 1L1 14h14L8 1z"
                stroke="#ef4444"
                strokeWidth="1.5"
                strokeLinejoin="round"
              />
              <path
                d="M8 6v4M8 11.5v.5"
                stroke="#ef4444"
                strokeWidth="1.5"
                strokeLinecap="round"
              />
            </svg>
            <span
              className="settings-group-title"
              style={{ color: "var(--red)" }}
            >
              Danger Zone
            </span>
          </div>
          <div className="setting-row">
            <div className="setting-label-group">
              <div className="setting-label">Reset All Settings</div>
              <div className="setting-hint">
                Restore all settings to their default values
              </div>
            </div>
            <div className="setting-control">
              <button
                className="btn-danger"
                onClick={() => setSettings(DEFAULT_SETTINGS)}
              >
                Reset Settings
              </button>
            </div>
          </div>
        </div>
      </div>

      {/* Sticky Save Bar */}
      <div
        style={{
          position: "sticky",
          bottom: 0,
          padding: "16px 0",
          background: "var(--bg-base)",
          borderTop: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: "16px",
          marginTop: "24px",
        }}
      >
        <button
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving}
          style={{ padding: "10px 28px" }}
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
        {saved && (
          <span style={{ fontSize: "13px", color: "var(--green)" }}>
            Settings saved
          </span>
        )}
      </div>

      {/* Delete confirmation dialog */}
      {deletingTemplate && (
        <DeleteConfirmDialog
          templateName={deletingTemplate}
          onConfirm={() => handleDeleteTemplate(deletingTemplate)}
          onCancel={() => setDeletingTemplate(null)}
          deleting={templateSaving}
        />
      )}
    </div>
  );
}

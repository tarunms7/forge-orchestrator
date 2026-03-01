"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";
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

export default function SettingsPage() {
  const token = useAuthStore((s) => s.token);
  const { requestPermission } = useNotifications();
  const [settings, setSettings] = useState<Settings>(DEFAULT_SETTINGS);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [cliStatus, setCliStatus] = useState<string>("checking");

  useEffect(() => {
    if (!token) return;

    apiGet("/settings", token)
      .then((data) => setSettings(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));

    // Auto-check CLI status on load (health endpoint is at /health, not under /api)
    fetch("/health")
      .then((r) => (r.ok ? setCliStatus("connected") : setCliStatus("error")))
      .catch(() => setCliStatus("error"));
  }, [token]);

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
    </div>
  );
}

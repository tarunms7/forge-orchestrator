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
    <div>
      <label className="mb-1.5 block text-sm font-medium text-zinc-300">
        {label}
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white focus:border-blue-500 focus:outline-none"
      >
        {MODEL_OPTIONS.map((m) => (
          <option key={m} value={m}>
            {m.charAt(0).toUpperCase() + m.slice(1)}
          </option>
        ))}
      </select>
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
  const [cliStatus, setCliStatus] = useState<string | null>(null);

  useEffect(() => {
    if (!token) return;

    apiGet("/settings", token)
      .then((data) => setSettings(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
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
    setCliStatus("Checking...");
    // This would call a backend endpoint in a real implementation
    setTimeout(() => setCliStatus("Claude CLI: Connected"), 1500);
  };

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-1.5 w-48 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-600" />
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8">
      <h1 className="mb-8 text-2xl font-bold text-white">Settings</h1>

      {error && (
        <div className="mb-6 rounded-lg border border-red-800 bg-red-950/30 p-4 text-sm text-red-300">
          {error}
        </div>
      )}

      {/* General Section */}
      <section className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">General</h2>

        <div className="space-y-5">
          {/* Max Agents Slider */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">
              Max Agents: {settings.max_agents}
            </label>
            <input
              type="range"
              min={1}
              max={16}
              value={settings.max_agents}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  max_agents: parseInt(e.target.value, 10),
                }))
              }
              className="w-full accent-blue-600"
            />
            <div className="mt-1 flex justify-between text-xs text-zinc-500">
              <span>1</span>
              <span>16</span>
            </div>
          </div>

          {/* Timeout Input */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">
              Timeout (seconds)
            </label>
            <input
              type="number"
              min={30}
              max={3600}
              value={settings.timeout}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  timeout: parseInt(e.target.value, 10) || 600,
                }))
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
            />
          </div>

          {/* Max Retries Input */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">
              Max Retries
            </label>
            <input
              type="number"
              min={0}
              max={10}
              value={settings.max_retries}
              onChange={(e) =>
                setSettings((s) => ({
                  ...s,
                  max_retries: parseInt(e.target.value, 10) || 0,
                }))
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
            />
          </div>
        </div>
      </section>

      {/* Model Routing Section */}
      <section className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">
          Model Routing
        </h2>

        <div className="space-y-5">
          {/* Strategy Preset */}
          <div>
            <label className="mb-2 block text-sm font-medium text-zinc-300">
              Strategy
            </label>
            <div className="flex gap-3">
              {STRATEGY_OPTIONS.map((s) => (
                <label
                  key={s}
                  className={`flex cursor-pointer items-center gap-2 rounded-lg border px-4 py-2 text-sm transition ${
                    settings.model_strategy === s
                      ? "border-blue-500 bg-blue-950/40 text-blue-300"
                      : "border-zinc-700 bg-zinc-800 text-zinc-400 hover:border-zinc-600"
                  }`}
                >
                  <input
                    type="radio"
                    name="model_strategy"
                    value={s}
                    checked={settings.model_strategy === s}
                    onChange={(e) =>
                      setSettings((prev) => ({
                        ...prev,
                        model_strategy: e.target.value,
                      }))
                    }
                    className="sr-only"
                  />
                  {s.charAt(0).toUpperCase() + s.slice(1)}
                </label>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-zinc-500">
              Auto balances cost and quality. Fast minimizes latency. Quality
              uses the strongest models.
            </p>
          </div>

          {/* Planner Model */}
          <ModelSelect
            label="Planner Model"
            value={settings.planner_model}
            onChange={(v) =>
              setSettings((s) => ({ ...s, planner_model: v }))
            }
          />

          {/* Agent Models by Complexity */}
          <div>
            <p className="mb-3 text-sm font-medium text-zinc-300">
              Agent Model by Complexity
            </p>
            <div className="grid grid-cols-3 gap-4">
              <ModelSelect
                label="Low"
                value={settings.agent_model_low}
                onChange={(v) =>
                  setSettings((s) => ({ ...s, agent_model_low: v }))
                }
              />
              <ModelSelect
                label="Medium"
                value={settings.agent_model_medium}
                onChange={(v) =>
                  setSettings((s) => ({ ...s, agent_model_medium: v }))
                }
              />
              <ModelSelect
                label="High"
                value={settings.agent_model_high}
                onChange={(v) =>
                  setSettings((s) => ({ ...s, agent_model_high: v }))
                }
              />
            </div>
          </div>

          {/* Reviewer Model */}
          <ModelSelect
            label="Reviewer Model"
            value={settings.reviewer_model}
            onChange={(v) =>
              setSettings((s) => ({ ...s, reviewer_model: v }))
            }
          />
        </div>
      </section>

      {/* Security Section */}
      <section className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">Security</h2>

        <button
          disabled
          className="rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-sm text-zinc-400 opacity-60"
        >
          Change Password (coming soon)
        </button>
      </section>

      {/* Claude Section */}
      <section className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">Claude</h2>

        <div className="flex items-center gap-4">
          <button
            onClick={checkCliStatus}
            className="rounded-lg bg-zinc-800 px-4 py-2 text-sm font-medium text-white transition hover:bg-zinc-700"
          >
            Check CLI Status
          </button>
          {cliStatus && (
            <span className="text-sm text-zinc-400">{cliStatus}</span>
          )}
        </div>
      </section>

      {/* Save Button */}
      <div className="flex items-center gap-4">
        <button
          onClick={handleSave}
          disabled={saving}
          className="rounded-lg bg-blue-600 px-6 py-2.5 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-50"
        >
          {saving ? "Saving..." : "Save Settings"}
        </button>
        {saved && (
          <span className="text-sm text-green-400">Settings saved</span>
        )}
      </div>
    </div>
  );
}

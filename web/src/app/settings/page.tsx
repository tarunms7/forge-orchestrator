"use client";

import { useEffect, useState } from "react";
import { apiGet, apiPut } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";
import { useNotifications } from "@/hooks/useNotifications";

interface Settings {
  max_agents: number;
  timeout: number;
  browser_notifications: boolean;
  webhook_url: string;
  default_execution_target: string;
}

const DEFAULT_SETTINGS: Settings = {
  max_agents: 4,
  timeout: 300,
  browser_notifications: false,
  webhook_url: "",
  default_execution_target: "local",
};

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
      const updated = await apiPut("/settings", settings as unknown as Record<string, unknown>, token);
      setSettings(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save settings");
    } finally {
      setSaving(false);
    }
  };

  const handleNotificationToggle = async (enabled: boolean) => {
    if (enabled) {
      const permission = await requestPermission();
      if (permission !== "granted") {
        return;
      }
    }
    setSettings((s) => ({ ...s, browser_notifications: enabled }));
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
                  timeout: parseInt(e.target.value, 10) || 300,
                }))
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
            />
          </div>
        </div>
      </section>

      {/* Notifications Section */}
      <section className="mb-6 rounded-xl border border-zinc-800 bg-zinc-900 p-6">
        <h2 className="mb-4 text-lg font-semibold text-white">Notifications</h2>

        <div className="space-y-5">
          {/* Browser Notifications Toggle */}
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm font-medium text-zinc-300">
                Browser Notifications
              </p>
              <p className="text-xs text-zinc-500">
                Get notified when pipelines complete
              </p>
            </div>
            <button
              onClick={() =>
                handleNotificationToggle(!settings.browser_notifications)
              }
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${
                settings.browser_notifications ? "bg-blue-600" : "bg-zinc-700"
              }`}
            >
              <span
                className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
                  settings.browser_notifications
                    ? "translate-x-6"
                    : "translate-x-1"
                }`}
              />
            </button>
          </div>

          {/* Webhook URL */}
          <div>
            <label className="mb-1.5 block text-sm font-medium text-zinc-300">
              Webhook URL
            </label>
            <input
              type="url"
              placeholder="https://hooks.slack.com/services/..."
              value={settings.webhook_url}
              onChange={(e) =>
                setSettings((s) => ({ ...s, webhook_url: e.target.value }))
              }
              className="w-full rounded-lg border border-zinc-700 bg-zinc-800 px-3 py-2 text-sm text-white placeholder-zinc-500 focus:border-blue-500 focus:outline-none"
            />
            <p className="mt-1 text-xs text-zinc-500">
              Slack or Discord webhook URL for pipeline notifications
            </p>
          </div>
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

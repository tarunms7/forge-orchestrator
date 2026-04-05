"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiGet } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface ProviderHealthModel {
  alias: string;
  tier: string;
}

interface ProviderHealthEntry {
  name: string;
  models: ProviderHealthModel[];
}

interface ObservedHealth {
  spec: string;
  last_checked: string;
  stages_passing: string[];
  stages_failing: string[];
}

interface DashboardStats {
  total_runs: number;
  active: number;
  completed: number;
  failed: number;
  avg_duration_secs: number | null;
  total_spend_usd: number | null;
}

interface RecentPipeline {
  pipeline_id: string;
  description: string;
  phase: string;
  created_at: string;
  task_count: number;
}

function formatDuration(secs: number): string {
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.round(secs % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

const phaseToStatus: Record<string, string> = {
  complete: "success",
  executing: "running",
  planning: "running",
  planned: "running",
  error: "failed",
  failed: "failed",
};

export default function DashboardPage() {
  const token = useAuthStore((s) => s.token);
  const displayName = useAuthStore((s) => s.displayName);
  const router = useRouter();
  const [stats, setStats] = useState<DashboardStats>({
    total_runs: 0,
    active: 0,
    completed: 0,
    failed: 0,
    avg_duration_secs: null,
    total_spend_usd: null,
  });
  const [recent, setRecent] = useState<RecentPipeline[]>([]);
  const [pipelineInput, setPipelineInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [healthOk, setHealthOk] = useState<boolean | null>(null);
  const [providerHealth, setProviderHealth] = useState<ProviderHealthEntry[]>([]);
  const [observedHealth, setObservedHealth] = useState<ObservedHealth[]>([]);

  useEffect(() => {
    if (!token) return;
    Promise.all([
      apiGet("/tasks/stats", token).catch(() => {
        setError("Failed to load stats");
        return null;
      }),
      apiGet("/history", token).catch(() => {
        setError("Failed to load history");
        return null;
      }),
      apiGet("/providers", token).catch(() => null),
    ])
      .then(([statsData, historyData, providersData]) => {
        if (statsData) setStats(statsData);
        if (historyData) setRecent(historyData.slice(0, 5));
        if (providersData) {
          setProviderHealth(providersData.providers || []);
          setObservedHealth(providersData.observed_health || []);
        }
      })
      .finally(() => setLoading(false));
  }, [token]);

  useEffect(() => {
    const apiBase = process.env.NEXT_PUBLIC_API_URL || "/api";
    fetch(`${apiBase}/health`)
      .then((res) => setHealthOk(res.ok))
      .catch(() => setHealthOk(false));
  }, []);

  const handleStartPipeline = () => {
    const params = pipelineInput.trim()
      ? `?desc=${encodeURIComponent(pipelineInput.trim())}`
      : "";
    router.push(`/tasks/new${params}`);
  };

  const successRate =
    stats.total_runs > 0
      ? Math.round((stats.completed / stats.total_runs) * 100)
      : null;

  if (loading) {
    return (
      <div className="page-content" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "400px" }}>
        <div style={{ width: "192px", height: "6px", borderRadius: "999px", background: "var(--bg-surface-3)", overflow: "hidden" }}>
          <div style={{ width: "33%", height: "100%", borderRadius: "999px", background: "var(--accent)", animation: "pulse 2s ease-in-out infinite" }} />
        </div>
      </div>
    );
  }

  return (
    <div className="page-content">
      {/* Welcome */}
      <div className="welcome-section">
        <h1 className="welcome-title">Welcome back{displayName ? `, ${displayName.split(" ")[0]}` : ""}</h1>
        <p className="welcome-subtitle">
          Here&apos;s what&apos;s happening with your pipelines
        </p>
      </div>

      {/* Error Banner */}
      {error && (
        <div style={{ borderRadius: "var(--radius-md)", border: "1px solid rgba(239,68,68,0.3)", background: "var(--red-dim)", padding: "12px 16px", fontSize: "13px", color: "#fca5a5", marginBottom: "16px" }}>
          {error}
        </div>
      )}

      {/* New Pipeline */}
      <div className="new-pipeline-card">
        <div className="new-pipeline-label">Start a new pipeline</div>
        <div className="new-pipeline-form">
          <textarea
            className="new-pipeline-input"
            rows={3}
            placeholder="Describe what you want to build or fix..."
            value={pipelineInput}
            onChange={(e) => setPipelineInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                handleStartPipeline();
              }
            }}
          />
          <div className="new-pipeline-actions">
            <button
              className="btn btn-primary btn-glow"
              onClick={handleStartPipeline}
            >
              <svg
                width="14"
                height="14"
                viewBox="0 0 14 14"
                fill="currentColor"
              >
                <path d="M3 1l10 6-10 6V1z" />
              </svg>
              Run Pipeline
            </button>
          </div>
        </div>
      </div>

      {/* Overview Stats */}
      <div className="overview-grid">
        <div className="overview-card">
          <div className="overview-value">{stats.total_runs}</div>
          <div className="overview-label">Total Pipelines</div>
        </div>
        <div className={`overview-card${successRate !== null ? " success" : ""}`}>
          <div className="overview-value">
            {successRate !== null ? (
              <>
                {successRate}
                <span className="overview-unit">%</span>
              </>
            ) : (
              "--"
            )}
          </div>
          <div className="overview-label">Success Rate</div>
        </div>
        <div className="overview-card">
          <div className="overview-value">
            {stats.total_spend_usd !== null
              ? `$${stats.total_spend_usd.toFixed(2)}`
              : "--"}
          </div>
          <div className="overview-label">Total Spend</div>
        </div>
        <div className="overview-card">
          <div className="overview-value">
            {stats.avg_duration_secs !== null
              ? formatDuration(stats.avg_duration_secs)
              : "--"}
          </div>
          <div className="overview-label">Avg Duration</div>
        </div>
      </div>

      {/* Provider Health */}
      {providerHealth.length > 0 && (
        <div className="recent-section" style={{ marginBottom: "16px" }}>
          <h2 className="section-title" style={{ marginBottom: "12px" }}>Provider Health</h2>
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            {providerHealth.map((provider) => {
              // Determine provider-level health from observed data
              const providerSpecs = observedHealth.filter((h) => h.spec.startsWith(`${provider.name}:`));
              const hasFailures = providerSpecs.some((h) => h.stages_failing.length > 0);
              const hasData = providerSpecs.length > 0;
              const allPassing = hasData && !hasFailures;

              const dotColor = !hasData ? "var(--text-dim)" : allPassing ? "#22c55e" : hasFailures ? "#f59e0b" : "#22c55e";
              const statusLabel = !hasData ? "No data" : allPassing ? "Healthy" : "Degraded";

              return (
                <div
                  key={provider.name}
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: "8px",
                    padding: "8px 14px",
                    borderRadius: "var(--radius-md)",
                    background: "var(--bg-surface-2)",
                    border: "1px solid var(--border)",
                    fontSize: "13px",
                  }}
                >
                  <div
                    style={{
                      width: "8px",
                      height: "8px",
                      borderRadius: "50%",
                      background: dotColor,
                      flexShrink: 0,
                    }}
                  />
                  <span style={{ fontWeight: 600, color: "var(--text-primary)", textTransform: "capitalize" }}>
                    {provider.name}
                  </span>
                  <span style={{ color: "var(--text-dim)", fontSize: "12px" }}>
                    {statusLabel}
                  </span>
                  <span style={{ color: "var(--text-dim)", fontSize: "11px" }}>
                    ({provider.models.length} model{provider.models.length !== 1 ? "s" : ""})
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Recent Pipelines */}
      <div className="recent-section">
        <div className="recent-header">
          <h2 className="section-title">Recent Pipelines</h2>
          <Link href="/history" className="view-all-link">
            View all &rarr;
          </Link>
        </div>
        <div className="recent-list">
          {recent.length === 0 ? (
            <div className="recent-row" style={{ justifyContent: "center", cursor: "default" }}>
              <span className="recent-title" style={{ opacity: 0.5 }}>
                No recent pipelines
              </span>
            </div>
          ) : (
            recent.map((item) => (
              <Link
                key={item.pipeline_id}
                href={`/tasks/view?id=${item.pipeline_id}`}
                className="recent-row"
                style={{ textDecoration: "none", color: "inherit" }}
              >
                <div
                  className={`recent-status ${phaseToStatus[item.phase] || ""}`}
                />
                <span className="recent-title" title={item.description}>{item.description}</span>
                <span className="recent-id">
                  {item.pipeline_id.slice(0, 8)}
                </span>
                <span className="recent-tasks">
                  {item.task_count} task{item.task_count !== 1 ? "s" : ""}
                </span>
                <span className="recent-time">
                  {item.created_at
                    ? new Date(item.created_at).toLocaleDateString()
                    : ""}
                </span>
              </Link>
            ))
          )}
        </div>
      </div>

      {/* Backend Health */}
      <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px", color: "var(--text-dim)", marginTop: "8px" }}>
        <div style={{
          width: "8px",
          height: "8px",
          borderRadius: "50%",
          background: healthOk === null ? "var(--text-dim)" : healthOk ? "#22c55e" : "#ef4444",
          flexShrink: 0,
        }} />
        <span>
          {healthOk === null ? "Checking backend…" : healthOk ? "Backend connected" : "Backend unreachable"}
        </span>
      </div>
    </div>
  );
}

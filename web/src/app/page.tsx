"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { apiGet } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

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

  useEffect(() => {
    if (!token) return;
    apiGet("/tasks/stats", token)
      .then(setStats)
      .catch(() => {});
    apiGet("/history", token)
      .then((data) => setRecent(data.slice(0, 5)))
      .catch(() => {});
  }, [token]);

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

  return (
    <div className="page-content">
      {/* Welcome */}
      <div className="welcome-section">
        <h1 className="welcome-title">Welcome back{displayName ? `, ${displayName.split(" ")[0]}` : ""}</h1>
        <p className="welcome-subtitle">
          Here&apos;s what&apos;s happening with your pipelines
        </p>
      </div>

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

      {/* System Status */}
      <div className="system-section">
        <h2 className="section-title">System Status</h2>
        <div className="status-grid">
          <div className="status-card">
            <div className="status-dot-lg green" />
            <div className="status-info">
              <div className="status-name">Claude SDK</div>
              <div className="status-detail">Connected &middot; claude-opus-4-6</div>
            </div>
          </div>
          <div className="status-card">
            <div className="status-dot-lg green" />
            <div className="status-info">
              <div className="status-name">Git Repository</div>
              <div className="status-detail">forge-orchestrator &middot; main</div>
            </div>
          </div>
          <div className="status-card">
            <div className="status-dot-lg green" />
            <div className="status-info">
              <div className="status-name">Worktrees</div>
              <div className="status-detail">/tmp/forge-worktrees &middot; 0 active</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

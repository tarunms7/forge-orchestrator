"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiGet } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

interface HistoryItem {
  pipeline_id: string;
  description: string;
  phase: string;
  created_at: string;
  duration: number | null;
  task_count: number;
  build_cmd: string | null;
  test_cmd: string | null;
  github_issue_url: string | null;
  github_issue_number: number | null;
  project_path: string;
}

function StatusPill({ status }: { status: string }) {
  const phaseToStatus: Record<string, string> = {
    complete: "success",
    executing: "running",
    planning: "running",
    planned: "running",
    error: "failed",
    failed: "failed",
  };
  const cls = phaseToStatus[status] || "cancelled";
  const label = cls === "success" ? "Success" : cls === "running" ? "Running" : cls === "failed" ? "Failed" : status;
  return (
    <span className={`status-pill ${cls}`}>
      <span className="status-pill-dot"></span>
      {label}
    </span>
  );
}

function formatDuration(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return "--";
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s}s`;
}

export default function HistoryPage() {
  const token = useAuthStore((s) => s.token);
  const router = useRouter();
  const [history, setHistory] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [activeFilter, setActiveFilter] = useState("all");
  const [sortBy, setSortBy] = useState("recent");
  const [currentPage, setCurrentPage] = useState(1);
  const ITEMS_PER_PAGE = 10;

  // Fetch history data
  const fetchHistory = useCallback(() => {
    if (!token) return;
    apiGet("/history", token)
      .then((data) => {
        setHistory(data);
        setError(null);
      })
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  // Initial fetch
  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  // Re-fetch when tab becomes visible (handles back-navigation)
  useEffect(() => {
    const handler = () => {
      if (document.visibilityState === "visible") {
        fetchHistory();
      }
    };
    document.addEventListener("visibilitychange", handler);
    return () => document.removeEventListener("visibilitychange", handler);
  }, [fetchHistory]);

  const filteredHistory = history
    .filter(item => {
      if (activeFilter === "all") return true;
      const phaseMap: Record<string, string> = { running: "executing", success: "complete", failed: "error" };
      const targetPhase = phaseMap[activeFilter];
      return item.phase === targetPhase || item.phase === activeFilter;
    })
    .filter(item => {
      if (!searchQuery) return true;
      const q = searchQuery.toLowerCase();
      return item.description.toLowerCase().includes(q) || item.pipeline_id.toLowerCase().includes(q) || item.project_path.toLowerCase().includes(q);
    })
    .sort((a, b) => {
      if (sortBy === "oldest") return new Date(a.created_at).getTime() - new Date(b.created_at).getTime();
      if (sortBy === "longest") return (b.duration || 0) - (a.duration || 0);
      return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    });

  const totalPages = Math.max(1, Math.ceil(filteredHistory.length / ITEMS_PER_PAGE));
  const paginatedHistory = filteredHistory.slice((currentPage - 1) * ITEMS_PER_PAGE, currentPage * ITEMS_PER_PAGE);

  if (loading) {
    return (
      <div className="page-content" style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "400px" }}>
        <div style={{ width: "192px", height: "6px", borderRadius: "999px", background: "var(--bg-surface-3)", overflow: "hidden" }}>
          <div style={{ width: "33%", height: "100%", borderRadius: "999px", background: "var(--accent)", animation: "pulse 2s ease-in-out infinite" }} />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-content">
        <div style={{ borderRadius: "var(--radius-md)", border: "1px solid rgba(239,68,68,0.3)", background: "var(--red-dim)", padding: "16px", fontSize: "13px", color: "#fca5a5" }}>
          Failed to load history: {error}
        </div>
      </div>
    );
  }

  return (
    <div className="page-content">
      <style>{`.history-project { font-family: monospace; font-size: 12px; color: var(--text-dim); background: var(--bg-surface-2); padding: 2px 8px; border-radius: 4px; }`}</style>
      {/* Header */}
      <div className="page-header">
        <h1 className="page-title">Pipeline History</h1>
        <p className="page-subtitle">Browse and filter all pipeline runs</p>
      </div>

      {/* Toolbar */}
      <div className="history-toolbar">
        <div className="search-input-wrap">
          <svg className="search-icon" width="14" height="14" viewBox="0 0 14 14" fill="none">
            <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.5"/>
            <path d="M9.5 9.5L13 13" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
          </svg>
          <input type="text" className="search-input" placeholder="Search pipelines..." value={searchQuery} onChange={(e) => { setSearchQuery(e.target.value); setCurrentPage(1); }} />
        </div>
        <div className="filter-chips">
          {["all", "running", "success", "failed"].map(f => (
            <button key={f} className={`filter-chip${activeFilter === f ? " active" : ""}`} onClick={() => { setActiveFilter(f); setCurrentPage(1); }}>
              {f === "all" ? "All" : f === "running" ? "Running" : f === "success" ? "Completed" : "Failed"}
            </button>
          ))}
        </div>
        <select className="sort-select" value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
          <option value="recent">Most Recent</option>
          <option value="oldest">Oldest First</option>
          <option value="longest">Longest Duration</option>
        </select>
      </div>

      {/* Table */}
      <table className="history-table">
        <thead>
          <tr>
            <th style={{ width: "110px" }}>Status</th>
            <th>Pipeline</th>
            <th style={{ width: "140px" }}>Project</th>
            <th style={{ width: "80px" }}>Tasks</th>
            <th className="right" style={{ width: "80px" }}>Duration</th>
            <th className="right" style={{ width: "120px" }}>Date</th>
          </tr>
        </thead>
        <tbody>
          {paginatedHistory.length === 0 ? (
            <tr><td colSpan={6} style={{ textAlign: "center", padding: "48px 0", color: "var(--text-dim)" }}>
              {history.length === 0 ? "No pipeline runs yet" : "No matching results"}
            </td></tr>
          ) : (
            paginatedHistory.map((item) => (
              <tr key={item.pipeline_id} onClick={() => router.push(`/tasks/view?id=${item.pipeline_id}`)} onKeyDown={(e) => e.key === "Enter" && router.push(`/tasks/view?id=${item.pipeline_id}`)} tabIndex={0} role="link">
                <td><StatusPill status={item.phase} /></td>
                <td>
                  <div className="history-pipeline-cell">
                    <span className="history-pipeline-title">{item.description}</span>
                    <span className="history-id">{item.pipeline_id.slice(0, 8)}</span>
                    {item.build_cmd && (
                      <span
                        title={`Build: ${item.build_cmd}`}
                        style={{ marginLeft: "6px", fontSize: "10px", fontWeight: 600, padding: "1px 5px", borderRadius: "4px", background: "rgba(99,102,241,0.18)", color: "#a5b4fc", border: "1px solid rgba(99,102,241,0.3)", letterSpacing: "0.03em" }}
                      >B</span>
                    )}
                    {item.test_cmd && (
                      <span
                        title={`Test: ${item.test_cmd}`}
                        style={{ marginLeft: "4px", fontSize: "10px", fontWeight: 600, padding: "1px 5px", borderRadius: "4px", background: "rgba(34,197,94,0.15)", color: "#86efac", border: "1px solid rgba(34,197,94,0.3)", letterSpacing: "0.03em" }}
                      >T</span>
                    )}
                  </div>
                </td>
                <td>
                  {item.project_path ? (
                    <span className="history-project">
                      {item.project_path.split("/").filter(Boolean).pop() || item.project_path}
                    </span>
                  ) : (
                    <span style={{ color: "var(--text-dim)", fontSize: "12px" }}>--</span>
                  )}
                </td>
                <td><span className="history-tasks">{item.task_count}</span></td>
                <td className="history-duration">{formatDuration(item.duration)}</td>
                <td className="history-date">{item.created_at ? new Date(item.created_at).toLocaleDateString() : "--"}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {/* Pagination */}
      {filteredHistory.length > 0 && (
        <div className="pagination-bar">
          <span className="pagination-info">
            Showing {((currentPage - 1) * ITEMS_PER_PAGE) + 1}–{Math.min(currentPage * ITEMS_PER_PAGE, filteredHistory.length)} of {filteredHistory.length} pipelines
          </span>
          <div className="pagination-controls">
            <button className="page-btn" disabled={currentPage === 1} onClick={() => setCurrentPage(p => p - 1)} aria-label="Previous page">&#8249;</button>
            {Array.from({ length: totalPages }, (_, i) => i + 1).map(p => (
              <button key={p} className={`page-btn${p === currentPage ? " active" : ""}`} onClick={() => setCurrentPage(p)} aria-label={`Page ${p}`} aria-current={p === currentPage ? "page" : undefined}>{p}</button>
            ))}
            <button className="page-btn" disabled={currentPage === totalPages} onClick={() => setCurrentPage(p => p + 1)} aria-label="Next page">&#8250;</button>
          </div>
        </div>
      )}
    </div>
  );
}

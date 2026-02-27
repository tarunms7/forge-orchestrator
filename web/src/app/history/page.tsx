"use client";

import { useEffect, useState } from "react";
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
}

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-yellow-900/50 text-yellow-300 border-yellow-700",
  running: "bg-blue-900/50 text-blue-300 border-blue-700",
  complete: "bg-green-900/50 text-green-300 border-green-700",
  failed: "bg-red-900/50 text-red-300 border-red-700",
};

function StatusBadge({ status }: { status: string }) {
  const color = STATUS_COLORS[status] || "bg-zinc-800 text-zinc-300 border-zinc-700";
  return (
    <span
      className={`inline-block rounded-full border px-2.5 py-0.5 text-xs font-medium ${color}`}
    >
      {status}
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

  useEffect(() => {
    if (!token) return;

    apiGet("/history", token)
      .then((data) => setHistory(data))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token]);

  if (loading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <div className="h-1.5 w-48 overflow-hidden rounded-full bg-zinc-800">
          <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-600" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-lg border border-red-800 bg-red-950/30 p-4 text-sm text-red-300">
        Failed to load history: {error}
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-5xl px-4 py-8">
      <h1 className="mb-6 text-2xl font-bold text-white">Task History</h1>

      {history.length === 0 ? (
        <div className="flex h-64 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900">
          <div className="text-center">
            <p className="text-lg text-zinc-400">No pipeline runs yet</p>
            <p className="mt-1 text-sm text-zinc-500">
              Create a task to get started
            </p>
          </div>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-zinc-800">
          <table className="w-full">
            <thead>
              <tr className="border-b border-zinc-800 bg-zinc-900/80">
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                  Date
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                  Description
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                  Duration
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-zinc-400">
                  Tasks
                </th>
              </tr>
            </thead>
            <tbody>
              {history.map((item) => (
                <tr
                  key={item.pipeline_id}
                  onClick={() => router.push(`/tasks/${item.pipeline_id}`)}
                  className="cursor-pointer border-b border-zinc-800 bg-zinc-900 transition-colors hover:bg-zinc-800"
                >
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-zinc-400">
                    {item.created_at
                      ? new Date(item.created_at).toLocaleDateString()
                      : "--"}
                  </td>
                  <td className="px-4 py-3 text-sm text-zinc-200">
                    {item.description}
                  </td>
                  <td className="px-4 py-3">
                    <StatusBadge status={item.phase} />
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-zinc-400">
                    {formatDuration(item.duration)}
                  </td>
                  <td className="whitespace-nowrap px-4 py-3 text-sm text-zinc-400">
                    {item.task_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

"use client";

interface TimelineEvent {
  type: string;
  taskId?: string;
  payload: Record<string, unknown>;
  timestamp: string;
}

const EVENT_LABELS: Record<string, string> = {
  "pipeline:phase_changed": "Phase changed",
  "pipeline:plan_ready": "Plan ready",
  "pipeline:preflight_failed": "Pre-flight failed",
  "task:state_changed": "Task state",
  "task:review_update": "Review gate",
  "task:merge_result": "Merge result",
  "task:cost_update": "Cost update",
  "task:files_changed": "Files changed",
  "pipeline:pr_creating": "Creating PR",
  "pipeline:pr_created": "PR created",
  "pipeline:pr_failed": "PR failed",
};

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString();
  } catch {
    return iso;
  }
}

function eventSummary(ev: TimelineEvent): string {
  const p = ev.payload;
  switch (ev.type) {
    case "pipeline:phase_changed":
      return `→ ${p.phase}`;
    case "pipeline:plan_ready":
      return `${(p.tasks as unknown[])?.length || 0} tasks`;
    case "pipeline:preflight_failed":
      return `${(p.errors as string[])?.join(", ")}`;
    case "task:state_changed":
      return `${p.state}`;
    case "task:review_update":
      return `${p.gate} ${p.passed ? "passed" : "failed"}`;
    case "task:merge_result":
      return `${p.success ? "merged" : "failed"}`;
    case "task:cost_update":
      return `$${(p.cost_usd as number)?.toFixed(4)}`;
    case "task:files_changed":
      return `${(p.files as string[])?.length || 0} files`;
    case "pipeline:pr_created":
      return `${p.pr_url}`;
    default:
      return EVENT_LABELS[ev.type] || ev.type;
  }
}

const EVENT_COLORS: Record<string, string> = {
  "pipeline:phase_changed": "text-blue-400",
  "task:state_changed": "text-zinc-300",
  "task:review_update": "text-yellow-400",
  "task:merge_result": "text-green-400",
  "task:cost_update": "text-zinc-500",
  "pipeline:pr_created": "text-purple-400",
  "pipeline:preflight_failed": "text-red-400",
};

export type { TimelineEvent };

export default function TimelinePanel({ events }: { events: TimelineEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4">
        <h3 className="mb-2 text-sm font-semibold text-zinc-300">Timeline</h3>
        <p className="text-xs text-zinc-500">No events yet</p>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-zinc-800 bg-zinc-900 p-4 max-h-96 overflow-y-auto">
      <h3 className="mb-3 text-sm font-semibold text-zinc-300">Timeline</h3>
      <div className="space-y-1.5">
        {events.map((ev, i) => (
          <div key={i} className="flex items-start gap-2 text-xs">
            <span className="shrink-0 font-mono text-zinc-600">
              {formatTime(ev.timestamp)}
            </span>
            {ev.taskId && (
              <span className="shrink-0 rounded bg-zinc-800 px-1 text-zinc-500">
                {ev.taskId.slice(-8)}
              </span>
            )}
            <span className={EVENT_COLORS[ev.type] || "text-zinc-400"}>
              {eventSummary(ev)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

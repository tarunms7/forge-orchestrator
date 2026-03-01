"use client";

import { useEffect, useRef, useState } from "react";
import type { TaskState } from "@/stores/taskStore";
import { useTaskStore } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { apiPost } from "@/lib/api";

const STATE_BADGE: Record<
  TaskState["state"],
  { label: string; classes: string }
> = {
  pending: { label: "Pending", classes: "bg-zinc-700 text-zinc-300" },
  working: {
    label: "Working",
    classes: "bg-blue-600 text-blue-100 animate-pulse",
  },
  in_review: { label: "In Review", classes: "bg-yellow-600 text-yellow-100" },
  done: { label: "Done", classes: "bg-green-600 text-green-100" },
  error: { label: "Error", classes: "bg-red-600 text-red-100" },
  retrying: {
    label: "Retrying",
    classes: "bg-orange-600 text-orange-100 animate-pulse",
  },
};

function ReviewGateIcon({ result }: { result: string }) {
  if (result === "pass") {
    return (
      <svg
        className="h-4 w-4 text-green-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M5 13l4 4L19 7"
        />
      </svg>
    );
  }
  if (result === "fail") {
    return (
      <svg
        className="h-4 w-4 text-red-400"
        fill="none"
        viewBox="0 0 24 24"
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={2}
          d="M6 18L18 6M6 6l12 12"
        />
      </svg>
    );
  }
  return (
    <svg
      className="h-4 w-4 animate-spin text-yellow-400"
      fill="none"
      viewBox="0 0 24 24"
    >
      <circle
        className="opacity-25"
        cx="12"
        cy="12"
        r="10"
        stroke="currentColor"
        strokeWidth="4"
      />
      <path
        className="opacity-75"
        fill="currentColor"
        d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
      />
    </svg>
  );
}

function stripAnsi(str: string): string {
  return str.replace(/\x1B\[[0-9;]*[a-zA-Z]/g, "");
}

/** Renders a single output line with basic markdown-like formatting. */
function FormattedLine({ text }: { text: string }) {
  const clean = stripAnsi(text);

  // Heading detection (# or ## or ###)
  if (/^#{1,3}\s/.test(clean)) {
    const level = clean.match(/^(#+)/)?.[1].length ?? 1;
    const content = clean.replace(/^#+\s*/, "");
    const sizes = ["text-sm font-bold text-zinc-100", "text-sm font-semibold text-zinc-200", "text-xs font-semibold text-zinc-300"];
    return (
      <div className={`mt-2 mb-1 ${sizes[Math.min(level - 1, 2)]}`}>
        {content}
      </div>
    );
  }

  // Separator lines (---, ===, ***)
  if (/^[-=*]{3,}\s*$/.test(clean)) {
    return <div className="my-1 border-t border-zinc-800" />;
  }

  // Bullet points
  if (/^\s*[-*]\s/.test(clean)) {
    const content = clean.replace(/^\s*[-*]\s/, "");
    return (
      <div className="flex gap-2 text-zinc-400">
        <span className="text-zinc-600 select-none">&#x2022;</span>
        <span>{content}</span>
      </div>
    );
  }

  // Numbered items
  if (/^\s*\d+[.)]\s/.test(clean)) {
    const match = clean.match(/^\s*(\d+)[.)]\s(.*)/);
    if (match) {
      return (
        <div className="flex gap-2 text-zinc-400">
          <span className="text-zinc-500 select-none min-w-[1.2rem] text-right">{match[1]}.</span>
          <span>{match[2]}</span>
        </div>
      );
    }
  }

  // Code block markers (```)
  if (/^```/.test(clean)) {
    const lang = clean.replace(/^```\s*/, "");
    if (lang) {
      return (
        <div className="mt-1 text-xs text-zinc-600">{lang}</div>
      );
    }
    return <div className="my-0.5" />;
  }

  // Empty line → small spacer
  if (!clean.trim()) {
    return <div className="h-1" />;
  }

  // Default: normal text
  return (
    <div className="whitespace-pre-wrap break-words text-zinc-400">
      {clean}
    </div>
  );
}

function formatActivityTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function activityColor(type: string): string {
  const colors: Record<string, string> = {
    "task:state_changed": "text-zinc-300",
    "task:review_update": "text-yellow-400",
    "task:merge_result": "text-green-400",
    "task:cost_update": "text-zinc-500",
    "task:files_changed": "text-blue-400",
  };
  return colors[type] || "text-zinc-400";
}

function activityLabel(ev: { type: string; payload: Record<string, unknown> }): string {
  const p = ev.payload;
  switch (ev.type) {
    case "task:state_changed":
      return `State → ${p.state}`;
    case "task:review_update":
      return `${p.gate} ${p.passed ? "✓ passed" : "✗ failed"}`;
    case "task:merge_result":
      return p.success ? "Merged successfully" : `Merge failed: ${p.error || "unknown"}`;
    case "task:cost_update":
      return `Cost: $${(p.cost_usd as number)?.toFixed(4)}`;
    case "task:files_changed":
      return `${(p.files as string[])?.length || 0} files changed`;
    default:
      return ev.type.split(":")[1] || ev.type;
  }
}

const COLLAPSED_LINE_LIMIT = 8;

export default function AgentCard({ task, onClick }: { task: TaskState; onClick?: () => void }) {
  const outputRef = useRef<HTMLDivElement>(null);
  const badge = STATE_BADGE[task.state];
  const [expanded, setExpanded] = useState(false);
  const pipelineId = useTaskStore((s) => s.pipelineId);
  const timeline = useTaskStore((s) => s.timeline);
  const taskTimeline = timeline.filter(e => e.taskId === task.id);
  const token = useAuthStore((s) => s.token);

  useEffect(() => {
    if (outputRef.current && (expanded || task.output.length <= COLLAPSED_LINE_LIMIT)) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [task.output, expanded]);

  const showExpand = task.output.length > COLLAPSED_LINE_LIMIT;

  // Calculate stable indices: when collapsed, we show the last N lines
  // Use the absolute index from the full array as key for React stability
  const startIndex = expanded ? 0 : Math.max(0, task.output.length - COLLAPSED_LINE_LIMIT);
  const visibleLines = expanded ? task.output : task.output.slice(-COLLAPSED_LINE_LIMIT);

  return (
    <div
      onClick={onClick}
      className="cursor-pointer rounded-xl border border-zinc-800 bg-zinc-900 p-4 flex flex-col gap-3 transition hover:border-zinc-600"
    >
      {/* Header */}
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-sm font-semibold text-zinc-100">
            {task.title}
          </h3>
          <span className="mt-1 inline-block rounded bg-zinc-800 px-2 py-0.5 font-mono text-xs text-zinc-400">
            {task.branch}
          </span>
        </div>
        <span
          className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-medium ${badge.classes}`}
        >
          {badge.label}
        </span>
      </div>

      {/* Files changed */}
      {task.files.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500">
            Files changed ({task.files.length})
          </p>
          <ul className="space-y-0.5">
            {task.files.map((file) => (
              <li
                key={file}
                className="truncate font-mono text-xs text-zinc-400"
              >
                {file}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Agent output */}
      {task.output.length > 0 && (
        <div>
          <div className="mb-1 flex items-center justify-between">
            <p className="text-xs font-medium text-zinc-500">
              Agent Output ({task.output.length} messages)
            </p>
            {showExpand && (
              <button
                onClick={() => setExpanded(!expanded)}
                className="text-xs text-blue-400 hover:text-blue-300 transition-colors"
              >
                {expanded ? "Collapse" : `Show all ${task.output.length}`}
              </button>
            )}
          </div>
          <div
            ref={outputRef}
            className={`overflow-y-auto rounded-lg bg-zinc-950 p-3 font-mono text-xs leading-relaxed ${
              expanded ? "max-h-96" : "max-h-48"
            }`}
          >
            {!expanded && showExpand && (
              <div className="mb-2 text-center text-zinc-600 text-xs">
                &#x2022;&#x2022;&#x2022; {task.output.length - COLLAPSED_LINE_LIMIT} earlier messages hidden &#x2022;&#x2022;&#x2022;
              </div>
            )}
            {visibleLines.map((line, i) => (
              <FormattedLine key={`line-${startIndex + i}`} text={line} />
            ))}
          </div>
        </div>
      )}

      {/* Per-task activity log */}
      {taskTimeline.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500">
            Activity ({taskTimeline.length})
          </p>
          <div className="space-y-0.5 max-h-32 overflow-y-auto">
            {taskTimeline.map((ev, i) => (
              <div key={i} className="flex items-start gap-2 text-xs">
                <span className="shrink-0 font-mono text-zinc-600">
                  {formatActivityTime(ev.timestamp)}
                </span>
                <span className={activityColor(ev.type)}>
                  {activityLabel(ev)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Review gates */}
      {task.reviewGates.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-medium text-zinc-500">
            Review Gates
          </p>
          <div className="flex items-center gap-2">
            {task.reviewGates.map((gate) => {
              const label =
                gate.gate === "L1" ? "L1 (general)" :
                gate.gate === "L2" ? "L2 (LLM)" :
                String(gate.gate);
              return (
                <div
                  key={gate.gate}
                  className="flex items-center gap-1"
                  title={gate.details}
                >
                  <ReviewGateIcon result={gate.result} />
                  <span className="text-xs text-zinc-400">
                    {label}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Merge result */}
      {task.mergeResult && (
        <div
          className={`rounded-lg px-3 py-2 text-xs font-medium ${
            task.mergeResult.success
              ? "bg-green-950/50 text-green-300 border border-green-900"
              : "bg-red-950/50 text-red-300 border border-red-900"
          }`}
        >
          {task.mergeResult.success ? (
            <span className="flex items-center gap-2">
              <svg className="h-3.5 w-3.5 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
              </svg>
              Merged
              <span className="text-green-400">+{task.mergeResult.linesAdded ?? 0}</span>
              <span className="text-zinc-500">/</span>
              <span className="text-red-400">-{task.mergeResult.linesRemoved ?? 0}</span>
            </span>
          ) : (
            <span className="flex items-center gap-2">
              <svg className="h-3.5 w-3.5 text-red-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
              Merge failed: {task.mergeResult.error}
            </span>
          )}
        </div>
      )}

      {/* Cost */}
      {task.costUsd != null && task.costUsd > 0 && (
        <p className="text-xs text-zinc-500">Cost: ${task.costUsd.toFixed(4)}</p>
      )}

      {/* Retry button for errored tasks */}
      {task.state === "error" && (
        <button
          onClick={async () => {
            if (!pipelineId || !token) return;
            try {
              await apiPost(`/tasks/${pipelineId}/${task.id}/retry`, {}, token);
              window.location.reload();
            } catch {}
          }}
          className="mt-2 w-full rounded bg-yellow-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-yellow-700"
        >
          Retry Task
        </button>
      )}
    </div>
  );
}

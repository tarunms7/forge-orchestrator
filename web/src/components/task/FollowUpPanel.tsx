"use client";

import { useEffect, useRef, useState } from "react";
import { useTaskStore } from "@/stores/taskStore";
import type { FollowUpResult } from "@/stores/taskStore";
import { useAuthStore } from "@/stores/authStore";
import { CopyButton } from "@/components/CopyButton";

/* ── Follow-Up Result Card ─────────────────────────────────────────── */

function FollowUpResultCard({ result }: { result: FollowUpResult }) {
  const [expanded, setExpanded] = useState(false);
  const isWorking = result.status === "working";

  return (
    <div
      style={{
        borderRadius: "var(--radius-md)",
        border: `1px solid ${isWorking ? "rgba(59,130,246,0.3)" : "rgba(52,211,153,0.3)"}`,
        background: isWorking ? "var(--accent-glow)" : "rgba(52,211,153,0.06)",
        padding: "12px 16px",
        marginBottom: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: result.output.length > 0 ? "pointer" : "default",
        }}
        onClick={() => result.output.length > 0 && setExpanded(!expanded)}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {isWorking ? (
            <svg className="h-4 w-4 animate-spin" style={{ color: "var(--accent)" }} fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg style={{ width: 16, height: 16, color: "var(--green)" }} fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
          <span style={{ fontSize: 13, fontWeight: 500, color: "var(--text-primary)" }}>
            {result.title}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {result.filesChanged && result.filesChanged.length > 0 && (
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              {result.filesChanged.length} file{result.filesChanged.length !== 1 ? "s" : ""} changed
            </span>
          )}
          {result.output.length > 0 && (
            <svg
              className={`transition-transform ${expanded ? "rotate-90" : ""}`}
              style={{ width: 12, height: 12, color: "var(--text-tertiary)" }}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
            </svg>
          )}
        </div>
      </div>

      {expanded && result.output.length > 0 && (
        <div style={{ marginTop: 8 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "flex-end", marginBottom: 4 }}>
            <CopyButton text={result.output.join("\n")} variant="with-label" label="Copy" />
          </div>
          <div
            style={{
              padding: "8px 12px",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-surface-1)",
              maxHeight: 200,
              overflowY: "auto",
              fontFamily: "var(--font-mono, monospace)",
              fontSize: 11,
              lineHeight: 1.6,
              color: "var(--text-secondary)",
            }}
          >
            {result.output.map((line, i) => (
              <div key={i}>{line}</div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/* ── Follow-Up Panel ───────────────────────────────────────────────── */

export default function FollowUpPanel({ pipelineId }: { pipelineId: string }) {
  void pipelineId; // pipelineId is managed by the store
  const token = useAuthStore((s) => s.token);
  const followUpStatus = useTaskStore((s) => s.followUpStatus);
  const followUpResults = useTaskStore((s) => s.followUpResults);
  const followUpQuestions = useTaskStore((s) => s.followUpQuestions);
  const storeSubmitFollowUp = useTaskStore((s) => s.submitFollowUp);

  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const isSubmitting = followUpStatus === "submitting";
  const isExecuting = followUpStatus === "executing";
  const isDone = followUpStatus === "done";
  const isBusy = isSubmitting || isExecuting;

  const resultList = Object.values(followUpResults);

  // Auto-scroll panel into view when results come in
  useEffect(() => {
    if (resultList.length > 0 && panelRef.current) {
      panelRef.current.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }, [resultList.length]);

  async function handleSubmit() {
    const trimmed = input.trim();
    if (!trimmed || !token) return;

    setError(null);

    try {
      await storeSubmitFollowUp(trimmed, token);
      setInput("");
      // Status will transition to "executing" via WebSocket followup:started event
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to submit follow-up");
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    // Cmd/Ctrl+Enter to submit
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      handleSubmit();
    }
  }

  return (
    <div
      ref={panelRef}
      id="follow-up-panel"
      style={{
        marginTop: 24,
        borderRadius: "var(--radius-lg)",
        border: "1px solid var(--border)",
        background: "var(--bg-surface-2)",
        padding: 24,
      }}
    >
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <h3 style={{ fontSize: 16, fontWeight: 600, color: "var(--text-primary)", marginBottom: 4 }}>
          Follow-Up Questions
        </h3>
        <p style={{ fontSize: 13, color: "var(--text-tertiary)" }}>
          Ask follow-up questions or request changes. Agents will modify the existing tasks to address your concerns.
        </p>
      </div>

      {/* Previous questions */}
      {followUpQuestions.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          {followUpQuestions.map((q, i) => (
            <div
              key={i}
              style={{
                padding: "8px 12px",
                borderRadius: "var(--radius-md)",
                background: "var(--bg-surface-3)",
                marginBottom: 8,
                fontSize: 13,
                color: "var(--text-secondary)",
                borderLeft: "3px solid var(--accent)",
              }}
            >
              {q}
            </div>
          ))}
        </div>
      )}

      {/* Results */}
      {resultList.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.05em", marginBottom: 8 }}>
            {isDone ? "Results" : "Progress"}
          </div>
          {resultList.map((result) => (
            <FollowUpResultCard key={result.taskId} result={result} />
          ))}
        </div>
      )}

      {/* Status banner during execution */}
      {isExecuting && resultList.length === 0 && (
        <div
          style={{
            marginBottom: 16,
            display: "flex",
            alignItems: "center",
            gap: 8,
            padding: "10px 16px",
            borderRadius: "var(--radius-md)",
            background: "var(--accent-glow)",
            border: "1px solid rgba(59,130,246,0.3)",
            fontSize: 13,
            color: "var(--accent)",
          }}
        >
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Processing follow-up questions...
        </div>
      )}

      {/* Error */}
      {error && (
        <div
          style={{
            marginBottom: 12,
            padding: "8px 12px",
            borderRadius: "var(--radius-md)",
            background: "var(--red-dim)",
            border: "1px solid rgba(239,68,68,0.3)",
            fontSize: 13,
            color: "var(--red)",
          }}
        >
          {error}
        </div>
      )}

      {/* Input area — show for new round after done, or initially */}
      <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        <textarea
          ref={textareaRef}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isBusy}
          placeholder={isDone
            ? "Ask another follow-up question..."
            : "Describe what you'd like changed or ask questions about the results..."
          }
          rows={3}
          style={{
            width: "100%",
            padding: "10px 14px",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--border)",
            background: "var(--bg-surface-1)",
            color: "var(--text-primary)",
            fontSize: 13,
            lineHeight: 1.5,
            resize: "vertical",
            fontFamily: "inherit",
            outline: "none",
            opacity: isBusy ? 0.5 : 1,
          }}
        />
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <span style={{ fontSize: 11, color: "var(--text-dim)" }}>
            Press Cmd+Enter to submit
          </span>
          <button
            type="button"
            onClick={handleSubmit}
            disabled={isBusy || !input.trim()}
            className="btn btn-primary"
            style={{
              padding: "8px 20px",
              fontSize: 13,
              fontWeight: 600,
              opacity: isBusy || !input.trim() ? 0.4 : 1,
              cursor: isBusy || !input.trim() ? "not-allowed" : "pointer",
            }}
          >
            {isSubmitting ? "Submitting..." : isExecuting ? "Processing..." : "Submit Follow-Up"}
          </button>
        </div>
      </div>
    </div>
  );
}

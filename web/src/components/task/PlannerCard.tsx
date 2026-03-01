"use client";

import { useEffect, useRef } from "react";
import { useTaskStore } from "@/stores/taskStore";

export default function PlannerCard() {
  const plannerOutput = useTaskStore((s) => s.plannerOutput);
  const phase = useTaskStore((s) => s.phase);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plannerOutput]);

  if (phase !== "planning" && plannerOutput.length === 0) return null;

  const isActive = phase === "planning";

  return (
    <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          {isActive ? (
            <div className="h-2.5 w-2.5 rounded-full bg-blue-500 animate-pulse" />
          ) : (
            <svg className="h-4 w-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
          <h3 className="text-sm font-semibold text-zinc-200">
            {isActive ? "Planning..." : "Planning Complete"}
          </h3>
        </div>
      </div>

      {/* Streaming output */}
      {plannerOutput.length > 0 && (
        <div
          ref={scrollRef}
          className="max-h-48 overflow-y-auto p-3 font-mono text-xs leading-relaxed"
        >
          {plannerOutput.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap text-zinc-400">
              {line}
            </div>
          ))}
        </div>
      )}

      {/* Loading indicator when no output yet */}
      {isActive && plannerOutput.length === 0 && (
        <div className="flex items-center gap-2 px-4 py-3 text-sm text-zinc-400">
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Analyzing project and decomposing task...
        </div>
      )}
    </div>
  );
}

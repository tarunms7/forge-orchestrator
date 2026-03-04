"use client";

import { useTaskStore } from "@/stores/taskStore";

/** Color thresholds for budget usage */
function getBarColor(pct: number): string {
  if (pct >= 80) return "var(--red)";
  if (pct >= 50) return "var(--amber)";
  return "var(--green)";
}

function getBgColor(pct: number): string {
  if (pct >= 80) return "var(--red-dim)";
  if (pct >= 50) return "var(--amber-dim)";
  return "var(--green-dim)";
}

export default function CostIndicator() {
  const pipelineCost = useTaskStore((s) => s.pipelineCost);
  const estimatedCostUsd = useTaskStore((s) => s.estimatedCostUsd);
  const budgetLimitUsd = useTaskStore((s) => s.budgetLimitUsd);

  // Nothing to show if no cost data
  if (pipelineCost === 0 && estimatedCostUsd === 0 && budgetLimitUsd === 0) {
    return null;
  }

  const hasBudget = budgetLimitUsd > 0;
  const pct = hasBudget ? Math.min((pipelineCost / budgetLimitUsd) * 100, 100) : 0;
  const barColor = hasBudget ? getBarColor(pct) : "var(--accent)";
  const bgColor = hasBudget ? getBgColor(pct) : "var(--accent-glow)";

  return (
    <div className="cost-indicator">
      {/* Running total */}
      <div className="cost-indicator-header">
        <span className="cost-indicator-label">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 8c-1.657 0-3 .895-3 2s1.343 2 3 2 3 .895 3 2-1.343 2-3 2m0-8c1.11 0 2.08.402 2.599 1M12 8V7m0 1v8m0 0v1m0-1c-1.11 0-2.08-.402-2.599-1M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
          </svg>
          Cost
        </span>
        <span className="cost-indicator-value" style={{ color: barColor }}>
          ${pipelineCost.toFixed(2)}
          {hasBudget && (
            <span className="cost-indicator-budget"> / ${budgetLimitUsd.toFixed(2)}</span>
          )}
        </span>
      </div>

      {/* Progress bar (only when budget is set) */}
      {hasBudget && (
        <div
          className="cost-indicator-bar-track"
          style={{ background: bgColor }}
          role="progressbar"
          aria-valuenow={pipelineCost}
          aria-valuemin={0}
          aria-valuemax={budgetLimitUsd}
          aria-label={`Cost: $${pipelineCost.toFixed(2)} of $${budgetLimitUsd.toFixed(2)} budget`}
        >
          <div
            className="cost-indicator-bar-fill"
            style={{
              width: `${pct}%`,
              background: barColor,
            }}
          />
        </div>
      )}

      {/* Estimated cost */}
      {estimatedCostUsd > 0 && (
        <div className="cost-indicator-estimate">
          Est. ${estimatedCostUsd.toFixed(2)}
        </div>
      )}
    </div>
  );
}

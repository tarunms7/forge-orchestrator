"use client";

type ProviderConfig = Record<string, unknown> | null;
type RoutingItem = { stage: string; label: string; provider: string; model: string; mismatch: boolean; mismatchDetail: string | null };

const STAGE_ORDER = ["planner", "contract_builder", "agent_low", "agent_medium", "agent_high", "reviewer", "ci_fix"] as const;
const STAGE_LABELS: Record<string, string> = {
  planner: "Planner",
  contract_builder: "Contracts",
  agent_low: "Agent L",
  agent_medium: "Agent M",
  agent_high: "Agent H",
  reviewer: "Reviewer",
  ci_fix: "CI Fix",
};

const sortStages = (items: RoutingItem[]) => {
  const rank = new Map<string, number>(STAGE_ORDER.map((stage, index) => [stage, index]));
  return [...items].sort((a, b) => (rank.get(a.stage) ?? 999) - (rank.get(b.stage) ?? 999));
};

function parseRoutingItems(providerConfig: ProviderConfig): RoutingItem[] {
  if (!providerConfig) return [];

  const fromStage = (stage: string, value: Record<string, unknown>): RoutingItem => ({
    stage,
    label: typeof value.label === "string" ? value.label : (STAGE_LABELS[stage] ?? stage),
    provider:
      typeof value.actual_provider === "string" ? value.actual_provider
      : typeof value.provider === "string" ? value.provider
      : typeof value.spec === "string" ? value.spec.split(":")[0] || "unknown"
      : "unknown",
    model:
      typeof value.actual_model === "string" ? value.actual_model
      : typeof value.model === "string" ? value.model
      : typeof value.spec === "string" ? value.spec.split(":")[1] || value.spec
      : "unknown",
    mismatch: value.mismatch === true,
    mismatchDetail: typeof value.mismatch_detail === "string" ? value.mismatch_detail : null,
  });

  if (Array.isArray(providerConfig.entries)) {
    return sortStages(providerConfig.entries.flatMap((entry) => {
      if (!entry || typeof entry !== "object") return [];
      const value = entry as Record<string, unknown>;
      const stage = typeof value.stage === "string" ? value.stage : "unknown";
      return [fromStage(stage, value)];
    }));
  }

  if (providerConfig.stages && typeof providerConfig.stages === "object") {
    return sortStages(Object.entries(providerConfig.stages as Record<string, unknown>).flatMap(([stage, value]) => {
      if (!value || typeof value !== "object") return [];
      return [fromStage(stage, value as Record<string, unknown>)];
    }));
  }

  return sortStages(Object.entries(providerConfig).flatMap(([stage, spec]) => {
    if (typeof spec !== "string") return [];
    const [provider = "unknown", model = spec] = spec.split(":");
    return [{ stage, label: STAGE_LABELS[stage] ?? stage, provider, model, mismatch: false, mismatchDetail: null }];
  }));
}

const colorForProvider = (provider: string) => provider.toLowerCase() === "claude" ? "#22c55e" : "#58a6ff";

export default function RoutingAuditPanel({ providerConfig }: { providerConfig: ProviderConfig }) {
  const items = parseRoutingItems(providerConfig);
  if (!providerConfig) return null;

  const providers = new Set(items.map((item) => item.provider).filter((provider) => provider !== "unknown"));
  const hasMismatches = items.some((item) => item.mismatch);

  return (
    <div style={{ marginTop: 12, padding: 10, borderRadius: "var(--radius-md)", border: "1px solid var(--border)", background: "var(--bg-surface-2)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: items.length > 0 ? 8 : 0 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>Routing</span>
        {hasMismatches && (
          <span style={{ padding: "2px 8px", borderRadius: 999, border: "1px solid rgba(239,68,68,0.2)", background: "var(--red-dim)", color: "var(--red)", fontSize: 12, fontWeight: 500 }}>
            Mismatch
          </span>
        )}
        {providers.size > 1 && (
          <span style={{ padding: "2px 8px", borderRadius: 999, border: "1px solid var(--border)", background: "var(--bg-surface-3)", color: "var(--text-secondary)", fontSize: 12, fontWeight: 500 }}>
            Mixed routing
          </span>
        )}
      </div>
      {items.length > 0 ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
          {items.map((item) => {
            const color = colorForProvider(item.provider);
            const borderColor = item.mismatch ? "rgba(239,68,68,0.35)" : `${color}33`;
            const background = item.mismatch ? "rgba(239,68,68,0.08)" : `${color}14`;
            return (
              <div
                key={item.stage}
                title={item.mismatchDetail ?? `${item.label}: ${item.provider}:${item.model}`}
                style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "5px 9px", borderRadius: "var(--radius-sm)", border: `1px solid ${borderColor}`, background, fontSize: 12, color: "var(--text-primary)" }}
              >
                <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
                <span style={{ color, fontFamily: "var(--font-mono, monospace)", fontWeight: 600 }}>{item.provider}:{item.model}</span>
              </div>
            );
          })}
        </div>
      ) : (
        <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>Routing snapshot unavailable.</div>
      )}
    </div>
  );
}

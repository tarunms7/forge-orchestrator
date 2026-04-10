"use client";

type ProviderConfig = Record<string, unknown> | null;

type RoutingItem = {
  key: string;
  label: string;
  provider: string;
  model: string;
};

const STAGE_ORDER = [
  "planner",
  "contract_builder",
  "agent_low",
  "agent_medium",
  "agent_high",
  "reviewer",
  "ci_fix",
] as const;

const STAGE_LABELS: Record<string, string> = {
  planner: "Planner",
  contract_builder: "Contracts",
  agent_low: "Agent L",
  agent_medium: "Agent M",
  agent_high: "Agent H",
  reviewer: "Reviewer",
  ci_fix: "CI Fix",
};

function orderItems(items: RoutingItem[]): RoutingItem[] {
  const rank = new Map(STAGE_ORDER.map((stage, index) => [stage, index]));
  return [...items].sort((left, right) => (rank.get(left.key) ?? 999) - (rank.get(right.key) ?? 999));
}

function toItems(providerConfig: ProviderConfig): RoutingItem[] {
  if (!providerConfig) return [];

  if (Array.isArray(providerConfig.entries)) {
    return orderItems(providerConfig.entries.flatMap((entry) => {
      if (!entry || typeof entry !== "object") return [];
      const auditEntry = entry as Record<string, unknown>;
      const key = typeof auditEntry.stage === "string" ? auditEntry.stage : "stage";
      return [{
        key,
        label: typeof auditEntry.label === "string" ? auditEntry.label : STAGE_LABELS[key] ?? key,
        provider: typeof auditEntry.actual_provider === "string" ? auditEntry.actual_provider : "unknown",
        model: typeof auditEntry.actual_model === "string" ? auditEntry.actual_model : "unknown",
      }];
    }));
  }

  if (providerConfig.stages && typeof providerConfig.stages === "object") {
    return orderItems(Object.entries(providerConfig.stages as Record<string, unknown>).flatMap(([key, value]) => {
      if (!value || typeof value !== "object") return [];
      const stage = value as Record<string, unknown>;
      return [{
        key,
        label: STAGE_LABELS[key] ?? key,
        provider: typeof stage.provider === "string" ? stage.provider : "unknown",
        model: typeof stage.model === "string" ? stage.model : "unknown",
      }];
    }));
  }

  return orderItems(Object.entries(providerConfig).flatMap(([key, value]) =>
    typeof value === "string"
      ? [{
          key,
          label: STAGE_LABELS[key] ?? key,
          provider: value.split(":")[0] || "unknown",
          model: value.split(":")[1] || value,
        }]
      : []
  ));
}

function providerColor(provider: string): string {
  return provider === "claude" ? "#22c55e" : "#58a6ff";
}

export default function RoutingAuditPanel({ providerConfig }: { providerConfig: ProviderConfig }) {
  const items = toItems(providerConfig);

  if (!providerConfig || items.length === 0) return null;

  const providerCount = new Set(items.map((item) => item.provider).filter((provider) => provider !== "unknown")).size;

  return (
    <div
      style={{
        marginTop: 12,
        display: "flex",
        flexWrap: "wrap",
        alignItems: "center",
        gap: 8,
        padding: 10,
        borderRadius: "var(--radius-md)",
        border: "1px solid var(--border)",
        background: "var(--bg-surface-2)",
      }}
    >
      <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-tertiary)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
        Routing
      </span>
      {providerCount > 1 && (
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 999,
            border: "1px solid var(--border)",
            background: "var(--bg-surface-3)",
            color: "var(--text-secondary)",
            fontSize: 12,
            fontWeight: 500,
          }}
        >
          Mixed routing
        </span>
      )}
      {items.map((item) => (
        <div
          key={item.key}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: 6,
            padding: "5px 9px",
            borderRadius: "var(--radius-sm)",
            border: `1px solid ${providerColor(item.provider)}33`,
            background: `${providerColor(item.provider)}14`,
            color: "var(--text-primary)",
            fontSize: 12,
            lineHeight: 1.2,
          }}
          title={`${item.label}: ${item.provider}:${item.model}`}
        >
          <span style={{ color: "var(--text-secondary)" }}>{item.label}</span>
          <span style={{ color: providerColor(item.provider), fontFamily: "var(--font-mono, monospace)", fontWeight: 600 }}>
            {item.provider}:{item.model}
          </span>
        </div>
      ))}
    </div>
  );
}

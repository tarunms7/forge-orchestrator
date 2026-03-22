"use client";

import { useEffect, useState } from "react";
import { fetchContracts } from "@/lib/api";

/* ── Types ─────────────────────────────────────────────────────────── */

interface FieldSpec {
  name: string;
  type: string;
  required: boolean;
  description: string;
}

interface ApiContract {
  id: string;
  method: string;
  path: string;
  description: string;
  request_body: FieldSpec[] | null;
  response_body: FieldSpec[];
  response_example: string;
  auth_required: boolean;
  producer_task_id: string;
  consumer_task_ids: string[];
}

interface TypeContractDef {
  name: string;
  description: string;
  field_specs: FieldSpec[];
  used_by_tasks: string[];
}

interface ContractSetData {
  api_contracts: ApiContract[];
  type_contracts: TypeContractDef[];
}

/* ── Helpers ───────────────────────────────────────────────────────── */

const METHOD_COLORS: Record<string, string> = {
  GET: "var(--green)",
  POST: "var(--accent)",
  PUT: "var(--amber)",
  DELETE: "var(--red)",
  PATCH: "var(--accent)",
};

function MethodBadge({ method }: { method: string }) {
  const color = METHOD_COLORS[method.toUpperCase()] || "var(--text-secondary)";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 8px",
        borderRadius: "var(--radius-md)",
        fontSize: 11,
        fontWeight: 700,
        fontFamily: "var(--font-mono, monospace)",
        color,
        background: `color-mix(in srgb, ${color} 15%, transparent)`,
        letterSpacing: "0.02em",
      }}
    >
      {method.toUpperCase()}
    </span>
  );
}

function TaskBadge({ taskId }: { taskId: string }) {
  // Extract short suffix (e.g. "9df8148-task-2" → "task-2")
  const parts = taskId.split("-");
  const label = parts.length >= 2 ? parts.slice(-2).join("-") : taskId;
  return (
    <span
      style={{
        display: "inline-block",
        padding: "1px 6px",
        borderRadius: "var(--radius-md)",
        fontSize: 10,
        fontWeight: 500,
        color: "var(--text-tertiary)",
        background: "var(--bg-surface-3)",
        border: "1px solid var(--border-subtle)",
      }}
    >
      {label}
    </span>
  );
}

function FieldsTable({ fields }: { fields: FieldSpec[] }) {
  return (
    <table
      style={{
        width: "100%",
        borderCollapse: "collapse",
        fontSize: 12,
        marginTop: 8,
      }}
    >
      <thead>
        <tr>
          {["Field", "Type", "Required", "Description"].map((h) => (
            <th
              key={h}
              style={{
                textAlign: "left",
                padding: "6px 8px",
                borderBottom: "1px solid var(--border-subtle)",
                color: "var(--text-tertiary)",
                fontWeight: 600,
                fontSize: 11,
                textTransform: "uppercase",
                letterSpacing: "0.04em",
              }}
            >
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {fields.map((f) => (
          <tr key={f.name}>
            <td
              style={{
                padding: "5px 8px",
                borderBottom: "1px solid var(--border-subtle)",
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono, monospace)",
                fontWeight: 500,
              }}
            >
              {f.name}
            </td>
            <td
              style={{
                padding: "5px 8px",
                borderBottom: "1px solid var(--border-subtle)",
                color: "var(--text-secondary)",
                fontFamily: "var(--font-mono, monospace)",
              }}
            >
              {f.type}
            </td>
            <td
              style={{
                padding: "5px 8px",
                borderBottom: "1px solid var(--border-subtle)",
                color: f.required ? "var(--green)" : "var(--text-dim)",
              }}
            >
              {f.required ? "Yes" : "No"}
            </td>
            <td
              style={{
                padding: "5px 8px",
                borderBottom: "1px solid var(--border-subtle)",
                color: "var(--text-secondary)",
              }}
            >
              {f.description}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ── Main Component ────────────────────────────────────────────────── */

export default function ContractsPanel({
  pipelineId,
  token,
}: {
  pipelineId: string;
  token: string;
}) {
  const [data, setData] = useState<ContractSetData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    if (!pipelineId || !token) return;
    setLoading(true);
    setError(null);
    let cancelled = false;
    (async () => {
      try {
        const res = await fetchContracts(pipelineId, token);
        if (!cancelled) setData(res);
      } catch (err: unknown) {
        if (!cancelled) setError(err instanceof Error ? err.message : "Failed to load contracts");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [pipelineId, token]);

  // Loading state
  if (loading) {
    return (
      <div style={{ marginTop: 24 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            padding: 32,
            color: "var(--text-tertiary)",
            fontSize: 13,
            gap: 8,
          }}
        >
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Loading contracts...
        </div>
      </div>
    );
  }

  // Error state
  if (error) {
    return (
      <div style={{ marginTop: 24 }}>
        <div
          style={{
            padding: "12px 16px",
            borderRadius: "var(--radius-md)",
            background: "var(--red-dim)",
            border: "1px solid rgba(239,68,68,0.3)",
            fontSize: 13,
            color: "var(--red)",
          }}
        >
          {error}
        </div>
      </div>
    );
  }

  // Empty state
  const isEmpty =
    !data ||
    (data.api_contracts.length === 0 && data.type_contracts.length === 0);

  if (isEmpty) {
    return (
      <div style={{ marginTop: 24 }}>
        <p
          style={{
            textAlign: "center",
            color: "var(--text-tertiary)",
            fontSize: 13,
            padding: 24,
          }}
        >
          No contracts were generated for this pipeline.
        </p>
      </div>
    );
  }

  return (
    <div style={{ marginTop: 24 }}>
      {/* Collapsible Header */}
      <button
        type="button"
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          background: "none",
          border: "none",
          cursor: "pointer",
          padding: 0,
          marginBottom: open ? 16 : 0,
        }}
      >
        <svg
          className={`transition-transform ${open ? "rotate-90" : ""}`}
          style={{ width: 16, height: 16, color: "var(--text-tertiary)" }}
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
        </svg>
        <h2
          style={{
            fontSize: "1.1rem",
            fontWeight: 600,
            color: "var(--text-primary)",
            margin: 0,
          }}
        >
          Contracts
          <span
            style={{
              marginLeft: 8,
              fontSize: 12,
              fontWeight: 400,
              color: "var(--text-tertiary)",
            }}
          >
            {data.api_contracts.length} API · {data.type_contracts.length} Type
          </span>
        </h2>
      </button>

      {open && (
        <div>
          {/* API Contracts Section */}
          {data.api_contracts.length > 0 && (
            <div style={{ marginBottom: 24 }}>
              <h3
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--text-secondary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  marginBottom: 12,
                }}
              >
                API Contracts
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {data.api_contracts.map((contract) => (
                  <ApiContractCard key={contract.id} contract={contract} />
                ))}
              </div>
            </div>
          )}

          {/* Type Contracts Section */}
          {data.type_contracts.length > 0 && (
            <div>
              <h3
                style={{
                  fontSize: 13,
                  fontWeight: 600,
                  color: "var(--text-secondary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  marginBottom: 12,
                }}
              >
                Type Contracts
              </h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                {data.type_contracts.map((tc) => (
                  <TypeContractCard key={tc.name} contract={tc} />
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Sub-Components ────────────────────────────────────────────────── */

function ApiContractCard({ contract }: { contract: ApiContract }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{
        background: "var(--bg-surface-2)",
        borderRadius: "var(--radius-md)",
        padding: "1rem",
        border: "1px solid var(--border-subtle)",
      }}
    >
      {/* Header row */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
          gap: 8,
          background: "none",
          border: "none",
          width: "100%",
          padding: 0,
          font: "inherit",
          color: "inherit",
          textAlign: "left",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 0 }}>
          <svg
            className={`transition-transform ${expanded ? "rotate-90" : ""}`}
            style={{ width: 12, height: 12, color: "var(--text-tertiary)", flexShrink: 0 }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <MethodBadge method={contract.method} />
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-primary)",
              fontFamily: "var(--font-mono, monospace)",
            }}
          >
            {contract.path}
          </span>
          {contract.auth_required && (
            <svg
              style={{ width: 14, height: 14, color: "var(--amber)", flexShrink: 0 }}
              fill="none"
              viewBox="0 0 24 24"
              stroke="currentColor"
              strokeWidth={2}
              aria-label="Auth required"
            >
              <title>Auth required</title>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
            </svg>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
          <TaskBadge taskId={contract.producer_task_id} />
          {contract.consumer_task_ids.map((cid) => (
            <TaskBadge key={cid} taskId={cid} />
          ))}
        </div>
      </button>

      {/* Description (always visible if present) */}
      {contract.description && (
        <p
          style={{
            margin: "8px 0 0 20px",
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.4,
          }}
        >
          {contract.description}
        </p>
      )}

      {/* Expanded details */}
      {expanded && (
        <div style={{ marginTop: 12, paddingLeft: 20 }}>
          {/* Request Body */}
          {contract.request_body && contract.request_body.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-tertiary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  marginBottom: 4,
                }}
              >
                Request Body
              </div>
              <FieldsTable fields={contract.request_body} />
            </div>
          )}

          {/* Response Body */}
          {contract.response_body.length > 0 && (
            <div style={{ marginBottom: 12 }}>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-tertiary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  marginBottom: 4,
                }}
              >
                Response Body
              </div>
              <FieldsTable fields={contract.response_body} />
            </div>
          )}

          {/* Response Example */}
          {contract.response_example && (
            <div>
              <div
                style={{
                  fontSize: 11,
                  fontWeight: 600,
                  color: "var(--text-tertiary)",
                  textTransform: "uppercase",
                  letterSpacing: "0.04em",
                  marginBottom: 4,
                }}
              >
                Example Response
              </div>
              <pre
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--radius-md)",
                  background: "var(--bg-surface-3)",
                  fontSize: 11,
                  fontFamily: "var(--font-mono, monospace)",
                  color: "var(--text-secondary)",
                  overflowX: "auto",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  lineHeight: 1.5,
                  margin: 0,
                }}
              >
                {(() => {
                  try {
                    return JSON.stringify(JSON.parse(contract.response_example), null, 2);
                  } catch {
                    return contract.response_example;
                  }
                })()}
              </pre>
            </div>
          )}

          {/* Producer / Consumer info */}
          <div style={{ marginTop: 12, display: "flex", gap: 16, flexWrap: "wrap" }}>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              <span style={{ fontWeight: 600 }}>Producer:</span>{" "}
              <TaskBadge taskId={contract.producer_task_id} />
            </div>
            {contract.consumer_task_ids.length > 0 && (
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600 }}>Consumers:</span>
                {contract.consumer_task_ids.map((cid) => (
                  <TaskBadge key={cid} taskId={cid} />
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function TypeContractCard({ contract }: { contract: TypeContractDef }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div
      style={{
        background: "var(--bg-surface-2)",
        borderRadius: "var(--radius-md)",
        padding: "1rem",
        border: "1px solid var(--border-subtle)",
      }}
    >
      {/* Header row */}
      <button
        type="button"
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          cursor: "pointer",
          gap: 8,
          background: "none",
          border: "none",
          width: "100%",
          padding: 0,
          font: "inherit",
          color: "inherit",
          textAlign: "left",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <svg
            className={`transition-transform ${expanded ? "rotate-90" : ""}`}
            style={{ width: 12, height: 12, color: "var(--text-tertiary)", flexShrink: 0 }}
            fill="none"
            viewBox="0 0 24 24"
            stroke="currentColor"
            strokeWidth={2}
          >
            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
          </svg>
          <span
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--text-primary)",
              fontFamily: "var(--font-mono, monospace)",
            }}
          >
            {contract.name}
          </span>
          <span
            style={{
              fontSize: 11,
              color: "var(--text-tertiary)",
            }}
          >
            {contract.field_specs.length} field{contract.field_specs.length !== 1 ? "s" : ""}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 4, flexShrink: 0 }}>
          {contract.used_by_tasks.map((tid) => (
            <TaskBadge key={tid} taskId={tid} />
          ))}
        </div>
      </button>

      {/* Description */}
      {contract.description && (
        <p
          style={{
            margin: "8px 0 0 20px",
            fontSize: 12,
            color: "var(--text-secondary)",
            lineHeight: 1.4,
          }}
        >
          {contract.description}
        </p>
      )}

      {/* Expanded details */}
      {expanded && (
        <div style={{ marginTop: 12, paddingLeft: 20 }}>
          {contract.field_specs.length > 0 && (
            <FieldsTable fields={contract.field_specs} />
          )}

          {contract.used_by_tasks.length > 0 && (
            <div style={{ marginTop: 12, fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 4, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 600 }}>Used by:</span>
              {contract.used_by_tasks.map((tid) => (
                <TaskBadge key={tid} taskId={tid} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

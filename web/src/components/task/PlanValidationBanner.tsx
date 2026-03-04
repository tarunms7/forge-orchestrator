"use client";

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export default function PlanValidationBanner({
  validation,
}: {
  validation: ValidationResult;
}) {
  if (validation.valid) {
    return (
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "10px 16px",
          borderRadius: "var(--radius-md)",
          border: "1px solid rgba(34,197,94,0.3)",
          background: "var(--green-dim)",
          fontSize: 13,
          color: "var(--green)",
          fontWeight: 500,
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2.5}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M5 13l4 4L19 7"
          />
        </svg>
        No validation errors
      </div>
    );
  }

  return (
    <div
      style={{
        padding: "12px 16px",
        borderRadius: "var(--radius-md)",
        border: "1px solid rgba(239,68,68,0.3)",
        background: "var(--red-dim)",
        fontSize: 13,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          color: "var(--red)",
          fontWeight: 600,
          marginBottom: 8,
        }}
      >
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth={2}
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            d="M12 9v3.75m9-.75a9 9 0 11-18 0 9 9 0 0118 0zm-9 3.75h.008v.008H12v-.008z"
          />
        </svg>
        Validation Errors ({validation.errors.length})
      </div>
      <ul
        style={{
          margin: 0,
          paddingLeft: 24,
          color: "var(--red)",
          lineHeight: 1.6,
        }}
      >
        {validation.errors.map((error, i) => (
          <li key={i}>{error}</li>
        ))}
      </ul>
    </div>
  );
}

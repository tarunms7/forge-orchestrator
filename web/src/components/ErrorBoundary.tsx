"use client";
import React from "react";

interface Props {
  children: React.ReactNode;
}

interface State {
  hasError: boolean;
  btnHover: boolean;
}

export class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false, btnHover: false };
  }

  static getDerivedStateFromError(): Partial<State> {
    return { hasError: true };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo): void {
    console.error("ErrorBoundary caught:", error, info.componentStack);
  }

  render(): React.ReactNode {
    if (this.state.hasError) {
      return (
        <div
          style={{
            display: "flex",
            minHeight: "100vh",
            alignItems: "center",
            justifyContent: "center",
            background: "var(--bg-surface-1)",
            color: "var(--text-primary)",
          }}
        >
          <div
            style={{
              textAlign: "center",
              display: "flex",
              flexDirection: "column",
              gap: "1rem",
            }}
          >
            <h1 className="text-2xl font-semibold">Something went wrong</h1>
            <p style={{ color: "var(--text-secondary)" }}>An unexpected error occurred.</p>
            <button
              style={{
                padding: "0.5rem 1rem",
                borderRadius: "0.375rem",
                background: this.state.btnHover
                  ? "var(--bg-surface-4)"
                  : "var(--bg-surface-3)",
                border: "none",
                color: "var(--text-primary)",
                cursor: "pointer",
                transition: "background 150ms",
              }}
              onMouseEnter={() => this.setState({ btnHover: true })}
              onMouseLeave={() => this.setState({ btnHover: false })}
              onClick={() => {
                this.setState({ hasError: false });
                window.location.reload();
              }}
            >
              Reload
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

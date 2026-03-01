"use client";

import { useState, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { apiPost } from "@/lib/api";
import { useAuthStore } from "@/stores/authStore";

export default function LoginPage() {
  const router = useRouter();
  const setAuth = useAuthStore((s) => s.setAuth);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);

    try {
      const data = await apiPost("/auth/login", { email, password });
      setAuth(data.access_token, data.user_id);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="auth-page">
      <div className="auth-card">
        {/* Logo */}
        <div className="auth-header">
          <svg className="auth-logo" width="36" height="36" viewBox="0 0 20 20" fill="none">
            <rect x="3" y="2" width="3.5" height="16" rx="1" fill="var(--accent)" />
            <rect x="3" y="2" width="14" height="3.5" rx="1" fill="var(--accent)" />
            <rect x="3" y="8.5" width="10" height="3" rx="1" fill="var(--accent)" opacity="0.6" />
          </svg>
          <h1 className="auth-title">Sign in to Forge</h1>
          <p className="auth-subtitle">Multi-agent orchestration engine</p>
        </div>

        {/* Error */}
        {error && (
          <div className="auth-error">{error}</div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="auth-form">
          <div className="auth-field">
            <label htmlFor="email" className="auth-label">Email</label>
            <input
              id="email"
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="auth-input"
              placeholder="you@example.com"
            />
          </div>

          <div className="auth-field">
            <label htmlFor="password" className="auth-label">Password</label>
            <input
              id="password"
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="auth-input"
              placeholder="••••••••"
            />
          </div>

          <button type="submit" disabled={loading} className="auth-submit">
            {loading ? "Signing in..." : "Sign In"}
          </button>
        </form>

        {/* Register link */}
        <p className="auth-footer">
          Don&apos;t have an account?{" "}
          <Link href="/register" className="auth-link">Create one</Link>
        </p>
      </div>
    </div>
  );
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api';

async function fetchWithAuth(
  path: string,
  options: RequestInit,
  token?: string,
  _retried = false,
): Promise<Response> {
  const headers: Record<string, string> = { ...(options.headers as Record<string, string> || {}) };
  if (token) headers["Authorization"] = `Bearer ${token}`;

  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });

  // Auto-refresh on 401 (token expired or invalid secret) — retry once with new token.
  // We delegate to useAuthStore.refreshToken() so that the userId is properly
  // recovered from the JWT `sub` claim even after a hard page reload.
  if (res.status === 401 && !_retried) {
    try {
      const { useAuthStore } = await import("@/stores/authStore");
      const refreshed = await useAuthStore.getState().refreshToken();

      if (refreshed) {
        // refreshToken() already updated the store (token + userId from JWT).
        // Grab the new token and retry the original request.
        const newToken = useAuthStore.getState().token;
        if (newToken) {
          return fetchWithAuth(path, options, newToken, true);
        }
      }

      // Refresh failed (e.g. server restarted with a new JWT secret).
      // Clear auth state and force navigation to login so the user isn't left
      // on a broken page where every subsequent action silently fails.
      useAuthStore.getState().logout();
    } catch {
      // Store import failed — fall through to the redirect below.
    }

    if (typeof window !== "undefined") {
      window.location.href = "/login";
    }
  }

  return res;
}

/** Extract a human-readable error message from a failed response body.
 *  FastAPI 422 returns `detail` as an array of validation errors — stringify them. */
function extractErrorMessage(data: Record<string, unknown>, fallback: string): string {
  if (Array.isArray(data.detail)) {
    return data.detail
      .map((d: Record<string, unknown>) => (d.msg as string) || JSON.stringify(d))
      .join("; ");
  }
  return (data.detail as string) || fallback;
}

export async function apiPost(path: string, body: Record<string, unknown>, token?: string) {
  const res = await fetchWithAuth(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(data, res.statusText));
  }
  return res.json();
}

export async function apiGet(path: string, token: string) {
  const res = await fetchWithAuth(path, { method: "GET" }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(data, res.statusText));
  }
  return res.json();
}

export async function apiPut(path: string, body: Record<string, unknown>, token: string) {
  const res = await fetchWithAuth(path, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(data, res.statusText));
  }
  return res.json();
}

export async function apiDelete(path: string, token: string) {
  const res = await fetchWithAuth(path, { method: "DELETE" }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(data, res.statusText));
  }
  return res.json();
}

/* ── Pipeline Action Helpers ─────────────────────────────────────── */

export async function submitFollowUp(
  pipelineId: string,
  questions: string,
  token: string,
) {
  return apiPost(`/tasks/${pipelineId}/followup`, { questions: [{ text: questions }] }, token);
}

export async function cancelPipeline(pipelineId: string, token: string) {
  return apiPost(`/tasks/${pipelineId}/cancel`, {}, token);
}

export async function restartPipeline(pipelineId: string, token: string) {
  return apiPost(`/tasks/${pipelineId}/restart`, {}, token);
}

export async function pausePipeline(pipelineId: string, token: string) {
  return apiPost(`/tasks/${pipelineId}/pause`, {}, token);
}

export async function resumePipeline(pipelineId: string, token: string) {
  return apiPost(`/tasks/${pipelineId}/resume`, {}, token);
}

/* ── Task Approval Helpers ───────────────────────────────────────── */

export async function approveTask(pipelineId: string, taskId: string, token: string) {
  return apiPost(`/tasks/${pipelineId}/tasks/${taskId}/approve`, {}, token);
}

export async function rejectTask(
  pipelineId: string,
  taskId: string,
  reason: string | null,
  token: string,
) {
  return apiPost(`/tasks/${pipelineId}/tasks/${taskId}/reject`, { reason }, token);
}


export async function getTaskDiff(pipelineId: string, taskId: string, token: string) {
  return apiGet(`/tasks/${pipelineId}/tasks/${taskId}/diff`, token);
}

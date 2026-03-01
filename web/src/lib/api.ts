const API_BASE = process.env.NEXT_PUBLIC_API_URL || '/api';

/** Attempt to refresh the access token via the httpOnly refresh cookie. */
async function tryRefreshToken(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/auth/refresh`, {
      method: "POST",
      credentials: "include",
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.access_token || null;
  } catch {
    return null;
  }
}

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

  // Auto-refresh on 401 (token expired) — retry once with new token
  if (res.status === 401 && !_retried) {
    const newToken = await tryRefreshToken();
    if (newToken) {
      // Update the auth store with the new token
      try {
        const { useAuthStore } = await import("@/stores/authStore");
        const current = useAuthStore.getState();
        useAuthStore.setState({ token: newToken, userId: current.userId });
      } catch {
        // Store import may fail in some contexts — continue anyway
      }
      return fetchWithAuth(path, options, newToken, true);
    }
  }

  return res;
}

export async function apiPost(path: string, body: Record<string, unknown>, token?: string) {
  const res = await fetchWithAuth(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
  }
  return res.json();
}

export async function apiGet(path: string, token: string) {
  const res = await fetchWithAuth(path, { method: "GET" }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
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
    throw new Error(data.detail || res.statusText);
  }
  return res.json();
}

export async function apiDelete(path: string, token: string) {
  const res = await fetchWithAuth(path, { method: "DELETE" }, token);
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || res.statusText);
  }
  return res.json();
}

import { create } from "zustand";

/** Decode the payload of a JWT without verifying the signature (client-side only). */
function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const parts = token.split(".");
    if (parts.length !== 3) return null;
    // Convert base64url → base64, then decode
    const base64 = parts[1].replace(/-/g, "+").replace(/_/g, "/");
    return JSON.parse(atob(base64)) as Record<string, unknown>;
  } catch {
    return null;
  }
}

interface AuthState {
  token: string | null;
  userId: string | null;
  displayName: string | null;
  setAuth: (token: string, userId: string, displayName?: string) => void;
  logout: () => void;
  refreshToken: () => Promise<boolean>;
}

// Token is stored in memory only (not localStorage) to prevent XSS token theft.
// The httpOnly refresh token cookie handles session persistence across page loads.
export const useAuthStore = create<AuthState>()((set, get) => ({
  token: null,
  userId: null,
  displayName: null,
  setAuth: (token, userId, displayName) => set({ token, userId, displayName: displayName ?? null }),
  logout: () => {
    set({ token: null, userId: null, displayName: null });
    // Clear any legacy localStorage data from previous versions
    if (typeof window !== "undefined") {
      localStorage.removeItem("forge-auth");
    }
    // Clear refresh token cookie by calling logout endpoint
    fetch(`${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/logout`, {
      method: "POST",
      credentials: "include",
    }).catch((err) => {
      console.warn("Logout endpoint failed:", err);
    });
  },
  refreshToken: async () => {
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/refresh`,
        { method: "POST", credentials: "include" }
      );
      if (!res.ok) return false;
      const data = await res.json();
      // userId is stored in memory only and is lost on a hard page reload.
      // Recover it from the JWT `sub` claim so the store stays consistent
      // even when the access token is silently refreshed after a reload.
      const payload = decodeJwtPayload(data.access_token);
      const current = get();
      const userId = (payload?.sub as string | undefined) ?? current.userId;
      const displayName = (payload?.dn as string | undefined) ?? current.displayName;
      set({ token: data.access_token, userId, displayName });
      return true;
    } catch {
      return false;
    }
  },
}));

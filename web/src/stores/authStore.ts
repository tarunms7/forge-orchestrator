import { create } from "zustand";

interface AuthState {
  token: string | null;
  userId: string | null;
  setAuth: (token: string, userId: string) => void;
  logout: () => void;
  refreshToken: () => Promise<boolean>;
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  token: null,
  userId: null,
  setAuth: (token, userId) => set({ token, userId }),
  logout: () => {
    set({ token: null, userId: null });
    // Clear refresh token cookie by calling logout endpoint
    fetch(`${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/logout`, {
      method: "POST",
      credentials: "include",
    }).catch(() => {});
  },
  refreshToken: async () => {
    try {
      const res = await fetch(
        `${process.env.NEXT_PUBLIC_API_URL || "/api"}/auth/refresh`,
        { method: "POST", credentials: "include" }
      );
      if (!res.ok) return false;
      const data = await res.json();
      const current = get();
      set({ token: data.access_token, userId: current.userId });
      return true;
    } catch {
      return false;
    }
  },
}));

"use client";
import { useAuthStore } from "@/stores/authStore";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const PUBLIC_PATHS = ["/login", "/register"];

function isPublicPath(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  return PUBLIC_PATHS.includes(normalized);
}

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = isPublicPath(pathname);
  // Only need to refresh if not public and no token
  const needsRefresh = !isPublic && !token;
  const [refreshing, setRefreshing] = useState(needsRefresh);

  useEffect(() => {
    if (!needsRefresh) {
      return;
    }
    let cancelled = false;
    refreshToken().then((ok) => {
      if (cancelled) return;
      if (!ok) router.push("/login");
      setRefreshing(false);
    });
    return () => { cancelled = true; };
  }, [needsRefresh, router, refreshToken]);

  if (refreshing) {
    return (
      <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", background: "var(--bg-base)" }}>
        <div style={{ color: "var(--text-tertiary)" }}>Loading...</div>
      </div>
    );
  }

  if (!token && !isPublic) return null;

  return <>{children}</>;
}

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
  const [checking, setChecking] = useState(!isPublicPath(pathname));

  useEffect(() => {
    if (isPublicPath(pathname)) {
      setChecking(false);
      return;
    }
    if (token) {
      setChecking(false);
      return;
    }
    // No token in memory — try refresh
    refreshToken().then((ok) => {
      if (!ok) router.push("/login");
      setChecking(false);
    });
  }, [token, pathname, router, refreshToken]);

  if (checking) {
    return (
      <div style={{ display: "flex", height: "100vh", alignItems: "center", justifyContent: "center", background: "var(--bg-base)" }}>
        <div style={{ color: "var(--text-tertiary)" }}>Loading...</div>
      </div>
    );
  }

  if (!token && !isPublicPath(pathname)) return null;

  return <>{children}</>;
}

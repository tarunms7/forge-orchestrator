"use client";
import { useAuthStore } from "@/stores/authStore";
import { useRouter, usePathname } from "next/navigation";
import { useEffect, useState } from "react";

const PUBLIC_PATHS = ["/login", "/register"];

export function AuthGuard({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const refreshToken = useAuthStore((s) => s.refreshToken);
  const router = useRouter();
  const pathname = usePathname();
  const [checking, setChecking] = useState(!PUBLIC_PATHS.includes(pathname));

  useEffect(() => {
    if (PUBLIC_PATHS.includes(pathname)) {
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
      <div className="flex h-screen items-center justify-center bg-zinc-950">
        <div className="text-zinc-400">Loading...</div>
      </div>
    );
  }

  if (!token && !PUBLIC_PATHS.includes(pathname)) return null;

  return <>{children}</>;
}

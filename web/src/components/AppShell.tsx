"use client";

import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { AuthGuard } from "@/components/AuthGuard";
import { Sidebar } from "@/components/Sidebar";

const PUBLIC_PATHS = ["/login", "/register"];

function isPublicPath(pathname: string): boolean {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  return PUBLIC_PATHS.includes(normalized);
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const pathname = usePathname();

  const isPublic = isPublicPath(pathname);
  const showNav = token && !isPublic;

  return (
    <AuthGuard>
      {showNav && <Sidebar />}
      <main className="min-h-screen bg-zinc-950">
        {children}
      </main>
    </AuthGuard>
  );
}

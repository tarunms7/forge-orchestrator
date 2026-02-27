"use client";

import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { AuthGuard } from "@/components/AuthGuard";
import { Sidebar } from "@/components/Sidebar";

const PUBLIC_PATHS = ["/login", "/register"];

export function AppShell({ children }: { children: React.ReactNode }) {
  const token = useAuthStore((s) => s.token);
  const pathname = usePathname();

  const isPublic = PUBLIC_PATHS.includes(pathname);
  const showSidebar = token && !isPublic;

  return (
    <AuthGuard>
      {showSidebar ? (
        <div className="flex h-screen bg-zinc-950">
          <Sidebar />
          <main className="flex-1 overflow-y-auto">{children}</main>
        </div>
      ) : (
        <>{children}</>
      )}
    </AuthGuard>
  );
}

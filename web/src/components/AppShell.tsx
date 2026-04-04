"use client";

import { useState } from "react";
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
  const [collapsed, setCollapsed] = useState(() => {
    if (typeof window === "undefined") return true;
    return localStorage.getItem("forge-sidebar") !== "expanded";
  });

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("forge-sidebar", next ? "collapsed" : "expanded");
      return next;
    });
  };

  const isPublic = isPublicPath(pathname);
  const showNav = token && !isPublic;

  if (!showNav) {
    return (
      <AuthGuard>
        <main className="min-h-screen">{children}</main>
      </AuthGuard>
    );
  }

  return (
    <AuthGuard>
      <div className={`app-layout${collapsed ? " sidebar-collapsed" : ""}`}>
        <Sidebar collapsed={collapsed} onToggle={toggleCollapsed} />
        <main className="main-content">{children}</main>
      </div>
    </AuthGuard>
  );
}

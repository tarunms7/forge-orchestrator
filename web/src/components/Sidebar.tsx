"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";

const NAV_ITEMS = [
  { label: "Dashboard", href: "/" },
  { label: "New Task", href: "/tasks/new" },
  { label: "History", href: "/history" },
  { label: "Settings", href: "/settings" },
];

export function Sidebar() {
  const pathname = usePathname();
  const logout = useAuthStore((s) => s.logout);

  return (
    <aside className="flex h-screen w-64 flex-col border-r border-zinc-800 bg-zinc-900">
      {/* Logo */}
      <div className="px-6 py-5">
        <h1 className="text-xl font-bold text-white">Forge</h1>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-3">
        {NAV_ITEMS.map((item) => {
          const isActive = pathname === item.href;
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`block rounded-md px-3 py-2 text-sm font-medium transition ${
                isActive
                  ? "bg-zinc-800 text-white"
                  : "text-zinc-400 hover:bg-zinc-800 hover:text-white"
              }`}
            >
              {item.label}
            </Link>
          );
        })}
      </nav>

      {/* User section */}
      <div className="border-t border-zinc-800 px-3 py-4">
        <button
          onClick={logout}
          className="w-full rounded-md px-3 py-2 text-left text-sm font-medium text-zinc-400 transition hover:bg-zinc-800 hover:text-white"
        >
          Log out
        </button>
      </div>
    </aside>
  );
}

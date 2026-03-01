"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
const NAV_ITEMS = [
  {
    label: "Dashboard",
    href: "/",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path d="M2 2h5v5H2V2zm7 0h5v5H9V2zM2 9h5v5H2V9zm7 0h5v5H9V9z" opacity="0.7" />
      </svg>
    ),
  },
  {
    label: "History",
    href: "/history",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path d="M8 0a8 8 0 100 16A8 8 0 008 0zm1 12H7V7h2v5zm0-7H7V3h2v2z" opacity="0.7" />
      </svg>
    ),
  },
  {
    label: "Settings",
    href: "/settings",
    icon: (
      <svg width="16" height="16" viewBox="0 0 16 16" fill="currentColor">
        <path
          d="M14 1H2a1 1 0 00-1 1v12a1 1 0 001 1h12a1 1 0 001-1V2a1 1 0 00-1-1zM5 13H3v-2h2v2zm0-4H3V7h2v2zm0-4H3V3h2v2zm8 8H7v-2h6v2zm0-4H7V7h6v2zm0-4H7V3h6v2z"
          opacity="0.7"
        />
      </svg>
    ),
  },
];

interface SidebarProps {
  collapsed: boolean;
  onToggle: () => void;
}

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="sidebar">
      {/* Header */}
      <div className="sidebar-header">
        {collapsed ? (
          <button
            onClick={onToggle}
            className="logo"
            style={{ background: "none", border: "none", cursor: "pointer", padding: 0 }}
            title="Expand sidebar"
          >
            <svg className="logo-mark" width="20" height="20" viewBox="0 0 20 20" fill="none">
              <rect x="3" y="2" width="3.5" height="16" rx="1" fill="#3b82f6" />
              <rect x="3" y="2" width="14" height="3.5" rx="1" fill="#3b82f6" />
              <rect x="3" y="8.5" width="10" height="3" rx="1" fill="#3b82f6" opacity="0.6" />
            </svg>
          </button>
        ) : (
          <>
            <Link href="/" className="logo">
              <svg className="logo-mark" width="20" height="20" viewBox="0 0 20 20" fill="none">
                <rect x="3" y="2" width="3.5" height="16" rx="1" fill="#3b82f6" />
                <rect x="3" y="2" width="14" height="3.5" rx="1" fill="#3b82f6" />
                <rect x="3" y="8.5" width="10" height="3" rx="1" fill="#3b82f6" opacity="0.6" />
              </svg>
              <span className="logo-text">Forge</span>
            </Link>
            <button className="sidebar-toggle" onClick={onToggle} title="Collapse sidebar">
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
                <path d="M10 12L6 8l4-4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </button>
          </>
        )}
      </div>

      {/* Navigation */}
      <nav className="sidebar-nav">
        {NAV_ITEMS.map((item) => {
          const isActive =
            item.href === "/"
              ? pathname === "/"
              : pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={`nav-item${isActive ? " active" : ""}`}
            >
              {item.icon}
              <span className="nav-text">{item.label}</span>
            </Link>
          );
        })}
      </nav>

      {/* Footer */}
      <div className="sidebar-footer">
        <div className="user-pill">
          <div className="user-avatar">T</div>
          <span className="user-name">Tarun M.</span>
        </div>
      </div>
    </aside>
  );
}

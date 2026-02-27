"use client";

import Link from "next/link";

const STATS = [
  { label: "Total Runs", value: "0" },
  { label: "Active", value: "0" },
  { label: "Completed", value: "0" },
];

const QUICK_ACTIONS = [
  {
    title: "New Task",
    description: "Start a new orchestration pipeline with your project.",
    href: "/tasks/new",
    icon: (
      <svg
        className="h-6 w-6 text-blue-400"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={1.5}
        stroke="currentColor"
      >
        <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
      </svg>
    ),
  },
  {
    title: "Task History",
    description: "View past pipeline runs, results, and diffs.",
    href: "/history",
    icon: (
      <svg
        className="h-6 w-6 text-blue-400"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={1.5}
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"
        />
      </svg>
    ),
  },
  {
    title: "Settings",
    description: "Configure agents, models, and preferences.",
    href: "/settings",
    icon: (
      <svg
        className="h-6 w-6 text-blue-400"
        fill="none"
        viewBox="0 0 24 24"
        strokeWidth={1.5}
        stroke="currentColor"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.247a1.125 1.125 0 0 1-.26 1.431l-1.003.827c-.293.241-.438.613-.43.992a7.723 7.723 0 0 1 0 .255c-.008.378.137.75.43.991l1.004.827c.424.35.534.955.26 1.43l-1.298 2.248a1.125 1.125 0 0 1-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.47 6.47 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.594c-.55 0-1.019-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 0 1-1.369-.49l-1.297-2.247a1.125 1.125 0 0 1 .26-1.431l1.004-.827c.292-.24.437-.613.43-.991a6.932 6.932 0 0 1 0-.255c.007-.38-.138-.751-.43-.992l-1.004-.827a1.125 1.125 0 0 1-.26-1.43l1.297-2.247a1.125 1.125 0 0 1 1.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.086.22-.128.332-.183.582-.495.644-.869l.214-1.28Z"
        />
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
        />
      </svg>
    ),
  },
];

export default function DashboardPage() {
  return (
    <div className="min-h-screen bg-black text-zinc-100">
      <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
        {/* Stats Bar */}
        <div className="mb-8 grid grid-cols-3 gap-4">
          {STATS.map((stat) => (
            <div
              key={stat.label}
              className="rounded-xl border border-zinc-800 bg-zinc-900 px-4 py-3 text-center"
            >
              <div className="text-2xl font-bold text-white">{stat.value}</div>
              <div className="text-xs text-zinc-400">{stat.label}</div>
            </div>
          ))}
        </div>

        {/* Welcome Header */}
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-white">Welcome to Forge</h1>
          <p className="mt-2 text-zinc-400">
            Multi-agent orchestration engine. Create tasks, monitor pipelines,
            and review results.
          </p>
        </div>

        {/* Quick Actions Grid */}
        <div className="mb-10">
          <h2 className="mb-4 text-lg font-semibold text-zinc-200">
            Quick Actions
          </h2>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            {QUICK_ACTIONS.map((action) => (
              <Link
                key={action.href}
                href={action.href}
                className="group rounded-xl border border-zinc-800 bg-zinc-900 p-5 transition-colors hover:border-zinc-600 hover:bg-zinc-800/70"
              >
                <div className="mb-3 flex h-10 w-10 items-center justify-center rounded-lg bg-zinc-800 group-hover:bg-zinc-700">
                  {action.icon}
                </div>
                <h3 className="text-sm font-semibold text-white">
                  {action.title}
                </h3>
                <p className="mt-1 text-sm text-zinc-400">
                  {action.description}
                </p>
              </Link>
            ))}
          </div>
        </div>

        {/* Recent Activity */}
        <div>
          <h2 className="mb-4 text-lg font-semibold text-zinc-200">
            Recent Activity
          </h2>
          <div className="flex h-48 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900">
            <p className="text-sm text-zinc-500">No recent tasks</p>
          </div>
        </div>
      </div>
    </div>
  );
}

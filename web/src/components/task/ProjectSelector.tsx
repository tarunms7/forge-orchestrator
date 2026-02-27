"use client";

export type ProjectSource = "existing" | "clone" | "create";

export interface ProjectConfig {
  source: ProjectSource;
  path?: string;
  githubUrl?: string;
  projectName?: string;
}

interface ProjectSelectorProps {
  value: ProjectConfig;
  onChange: (config: ProjectConfig) => void;
}

const SOURCE_OPTIONS: { value: ProjectSource; label: string; description: string }[] = [
  {
    value: "existing",
    label: "Existing local repo",
    description: "Use an existing repository on your machine",
  },
  {
    value: "clone",
    label: "Clone from GitHub",
    description: "Clone a repository from a GitHub URL",
  },
  {
    value: "create",
    label: "Create new",
    description: "Start a fresh project from scratch",
  },
];

export default function ProjectSelector({ value, onChange }: ProjectSelectorProps) {
  function handleSourceChange(source: ProjectSource) {
    onChange({ source, path: undefined, githubUrl: undefined, projectName: undefined });
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-white">Select Project</h2>
        <p className="mt-1 text-sm text-zinc-400">
          Choose how you want to set up the project for this task.
        </p>
      </div>

      <div className="space-y-3">
        {SOURCE_OPTIONS.map((option) => (
          <label
            key={option.value}
            className={`flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition ${
              value.source === option.value
                ? "border-blue-600 bg-zinc-800/50"
                : "border-zinc-700 bg-zinc-900 hover:border-zinc-600"
            }`}
          >
            <input
              type="radio"
              name="project-source"
              value={option.value}
              checked={value.source === option.value}
              onChange={() => handleSourceChange(option.value)}
              className="mt-0.5 h-4 w-4 accent-blue-600"
            />
            <div>
              <div className="text-sm font-medium text-white">{option.label}</div>
              <div className="text-sm text-zinc-400">{option.description}</div>
            </div>
          </label>
        ))}
      </div>

      {/* Conditional fields */}
      {value.source === "existing" && (
        <div>
          <label htmlFor="project-path" className="block text-sm font-medium text-zinc-300">
            Repository path
          </label>
          <input
            id="project-path"
            type="text"
            value={value.path || ""}
            onChange={(e) => onChange({ ...value, path: e.target.value })}
            placeholder="/home/user/my-project"
            className="mt-1 block w-full rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-white placeholder-zinc-500 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />
        </div>
      )}

      {value.source === "clone" && (
        <div>
          <label htmlFor="github-url" className="block text-sm font-medium text-zinc-300">
            GitHub URL
          </label>
          <input
            id="github-url"
            type="url"
            value={value.githubUrl || ""}
            onChange={(e) => onChange({ ...value, githubUrl: e.target.value })}
            placeholder="https://github.com/owner/repo"
            className="mt-1 block w-full rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-white placeholder-zinc-500 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />
        </div>
      )}

      {value.source === "create" && (
        <div>
          <label htmlFor="project-name" className="block text-sm font-medium text-zinc-300">
            Project name
          </label>
          <input
            id="project-name"
            type="text"
            value={value.projectName || ""}
            onChange={(e) => onChange({ ...value, projectName: e.target.value })}
            placeholder="my-new-project"
            className="mt-1 block w-full rounded-lg border border-zinc-700 bg-zinc-800 px-4 py-2 text-white placeholder-zinc-500 focus:border-blue-600 focus:outline-none focus:ring-1 focus:ring-blue-600"
          />
        </div>
      )}
    </div>
  );
}

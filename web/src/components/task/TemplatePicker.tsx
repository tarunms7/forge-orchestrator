"use client";

import { useState } from "react";

interface Template {
  name: string;
  description: string;
  category: string;
}

const BUILT_IN_TEMPLATES: Template[] = [
  {
    name: "REST API",
    description:
      "Build a RESTful API with CRUD endpoints, input validation, error handling, and OpenAPI documentation.",
    category: "backend",
  },
  {
    name: "CLI Tool",
    description:
      "Create a command-line tool with argument parsing, help text, colored output, and proper exit codes.",
    category: "tooling",
  },
  {
    name: "Bug Fix",
    description:
      "Diagnose and fix a bug. Investigate the root cause, apply the fix, and add a regression test.",
    category: "maintenance",
  },
  {
    name: "Refactor",
    description:
      "Refactor code for better readability, performance, or maintainability while preserving behavior.",
    category: "maintenance",
  },
  {
    name: "Add Tests",
    description:
      "Write comprehensive unit and integration tests for existing code with good coverage of edge cases.",
    category: "testing",
  },
];

const CATEGORY_COLORS: Record<string, string> = {
  backend: "bg-blue-950 text-blue-400 border-blue-800",
  tooling: "bg-purple-950 text-purple-400 border-purple-800",
  maintenance: "bg-yellow-950 text-yellow-400 border-yellow-800",
  testing: "bg-green-950 text-green-400 border-green-800",
};

interface TemplatePickerProps {
  onSelect: (description: string) => void;
}

export default function TemplatePicker({ onSelect }: TemplatePickerProps) {
  const [selected, setSelected] = useState<string | null>(null);

  function handleSelect(template: Template) {
    setSelected(template.name);
    onSelect(template.description);
  }

  return (
    <div className="space-y-3">
      <div>
        <h3 className="text-sm font-medium text-zinc-300">Quick Start Templates</h3>
        <p className="mt-0.5 text-xs text-zinc-500">
          Click a template to pre-fill the task description.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {BUILT_IN_TEMPLATES.map((template) => {
          const isSelected = selected === template.name;
          const categoryClass =
            CATEGORY_COLORS[template.category] || "bg-zinc-800 text-zinc-400 border-zinc-700";

          return (
            <button
              key={template.name}
              type="button"
              onClick={() => handleSelect(template)}
              className={`group flex flex-col items-start gap-2 rounded-lg border p-4 text-left transition ${
                isSelected
                  ? "border-blue-600 bg-zinc-800/70"
                  : "border-zinc-700 bg-zinc-900 hover:border-zinc-600 hover:bg-zinc-800/50"
              }`}
            >
              <div className="flex w-full items-center justify-between">
                <span className="text-sm font-medium text-white">{template.name}</span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-xs font-medium ${categoryClass}`}
                >
                  {template.category}
                </span>
              </div>
              <p className="line-clamp-2 text-xs text-zinc-400">{template.description}</p>
            </button>
          );
        })}
      </div>
    </div>
  );
}

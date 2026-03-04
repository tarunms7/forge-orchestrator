export interface EditableTask {
  id: string;
  title: string;
  description: string;
  files: string[];
  depends_on: string[];
  complexity: "low" | "medium" | "high";
}

export interface ValidationResult {
  valid: boolean;
  errors: string[];
}

export function validateTaskGraph(tasks: EditableTask[]): ValidationResult {
  const errors: string[] = [];
  const ids = new Set(tasks.map(t => t.id));

  // 1. No empty tasks
  if (tasks.length === 0) {
    errors.push("Plan must have at least one task.");
  }

  // 2. No duplicate IDs
  const seenIds = new Set<string>();
  for (const t of tasks) {
    if (seenIds.has(t.id)) errors.push(`Duplicate task ID: "${t.id}".`);
    seenIds.add(t.id);
  }

  // 3. All dependencies reference valid IDs
  for (const t of tasks) {
    for (const dep of t.depends_on) {
      if (!ids.has(dep)) {
        errors.push(`Task "${t.id}" depends on unknown task "${dep}".`);
      }
    }
  }

  // 4. No self-dependencies
  for (const t of tasks) {
    if (t.depends_on.includes(t.id)) {
      errors.push(`Task "${t.id}" depends on itself.`);
    }
  }

  // 5. Cycle detection (DFS)
  const visited = new Set<string>();
  const inStack = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    visited.add(node);
    inStack.add(node);
    for (const dep of adj[node] || []) {
      if (inStack.has(dep)) {
        errors.push(`Cycle detected involving tasks: ${node} → ${dep}.`);
        return true;
      }
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    inStack.delete(node);
    return false;
  }
  for (const t of tasks) {
    if (!visited.has(t.id)) dfs(t.id);
  }

  // 6. Every task must have at least one file
  for (const t of tasks) {
    if (t.files.length === 0) {
      errors.push(`Task "${t.id}" must declare at least one target file.`);
    }
  }

  // 7. No file conflicts (same file in two independent tasks)
  // NOTE: file conflicts between tasks with a dependency chain are OK
  // (the dependent task intentionally modifies the same file).
  // Only flag conflicts between tasks with NO transitive dependency.
  const fileOwners: Record<string, string> = {};
  for (const t of tasks) {
    for (const f of t.files) {
      if (f in fileOwners && !hasTransitiveDep(tasks, t.id, fileOwners[f]) &&
          !hasTransitiveDep(tasks, fileOwners[f], t.id)) {
        errors.push(
          `File "${f}" is claimed by both "${fileOwners[f]}" and "${t.id}" ` +
          `with no dependency between them.`
        );
      }
      if (!(f in fileOwners)) fileOwners[f] = t.id;
    }
  }

  // 8. Non-empty title
  for (const t of tasks) {
    if (!t.title.trim()) errors.push(`Task "${t.id}" has an empty title.`);
  }

  return { valid: errors.length === 0, errors };
}

export function hasTransitiveDep(
  tasks: EditableTask[], fromId: string, toId: string
): boolean {
  const visited = new Set<string>();
  const adj: Record<string, string[]> = {};
  for (const t of tasks) adj[t.id] = t.depends_on;

  function dfs(node: string): boolean {
    if (node === toId) return true;
    visited.add(node);
    for (const dep of adj[node] || []) {
      if (!visited.has(dep) && dfs(dep)) return true;
    }
    return false;
  }
  return dfs(fromId);
}

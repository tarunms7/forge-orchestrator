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

  // 6. No file conflicts (same file in two independent tasks)
  // Track ALL owners of each file to detect all pairwise conflicts.
  const fileOwners: Record<string, string[]> = {};
  for (const t of tasks) {
    for (const f of t.files) {
      if (!fileOwners[f]) fileOwners[f] = [];
      fileOwners[f].push(t.id);
    }
  }
  for (const [file, owners] of Object.entries(fileOwners)) {
    for (let i = 0; i < owners.length; i++) {
      for (let j = i + 1; j < owners.length; j++) {
        if (
          !hasTransitiveDep(tasks, owners[i], owners[j]) &&
          !hasTransitiveDep(tasks, owners[j], owners[i])
        ) {
          errors.push(
            `File "${file}" is claimed by both "${owners[i]}" and "${owners[j]}" ` +
            `with no dependency between them.`
          );
        }
      }
    }
  }

  // 7. Non-empty title
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

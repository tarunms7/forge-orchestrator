# Merge Conflict Fix + Resume Button — Design Doc

**Date:** 2026-03-01
**Status:** Approved
**Bugs:** Tier 2 conflict resolution never triggers; Resume button always visible during execution

---

## Bug 1: `_find_conflicts` runs in wrong directory

### Root Cause

`worker.py:_find_conflicts()` runs `git diff --name-only --diff-filter=U` in `self._repo` (main repo), but the rebase happens in the worktree. The main repo has no rebase state, so it always returns `[]` → `conflicting_files` is always empty → Tier 2 (Claude conflict resolution) never triggers → every merge conflict falls through to Tier 3 (full agent re-run), which recreates the same conflict.

### Fix

1. **`_find_conflicts(worktree_path=None)`** — accept optional worktree path, use it as cwd
2. **`_rebase()`** — pass `worktree_path` to `_find_conflicts()` when detecting conflicts
3. **Enriched Tier 2 prompt** — tell the resolver what other tasks already merged, so it understands context for both git-level and semantic conflicts
4. **Post-merge lint check** — after Tier 2 resolves and merges, run `ruff check` on changed files. If lint fails, the merge is still considered successful (lint can be fixed in a follow-up), but it provides signal.

### Files Changed

- `forge/merge/worker.py` — `_find_conflicts` signature + cwd fix
- `forge/core/daemon.py` — enriched `_resolve_conflicts` prompt
- `forge/merge/worker_test.py` — tests for conflict detection in worktree

### Conflict Resolution Flow (After Fix)

```
Merge attempt fails
  → Tier 1: abort + retry rebase (handles timing races)
  → Tier 2: _find_conflicts() returns ACTUAL files
    → Claude resolver gets: conflicting files + context of what other tasks merged
    → Resolves markers, commits
    → Retry merge
  → Tier 3: full agent re-run (last resort)
```

---

## Bug 2: Resume button shows during active execution

### Root Cause

`page.tsx:368` renders "Resume Pipeline" when `phase !== "complete" && phase !== "idle" && phase !== "planning" && phase !== "planned"` — so it shows during `executing`, which is normal active execution.

### Fix

- Show "Resume" only when pipeline is complete but has errored tasks (actionable state)
- During `executing`, only show "Cancel Pipeline"
- During `complete` with all tasks done, show nothing (CompletionSummary handles it)

### Logic

```
if phase === "executing":
  show Cancel only
elif phase === "complete" && hasErroredTasks:
  show Resume only
else:
  show nothing (idle/planning/planned/all-done)
```

### Files Changed

- `web/src/app/tasks/view/page.tsx` — button visibility logic (~10 lines)

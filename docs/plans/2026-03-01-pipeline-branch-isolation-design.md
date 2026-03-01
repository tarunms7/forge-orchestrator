# Pipeline Branch Isolation — Design Doc

**Date:** 2026-03-01
**Status:** Approved
**Problem:** Subtask merges go directly into `main` via `git checkout main && git merge --ff-only`, mutating the user's working directory and causing divergence between local and remote.

---

## Current Flow (Broken)

```
main ←── task-1 (git checkout main && git merge --ff-only)
     ←── task-2 (same — mutates user's checkout)
     ←── task-3 (same)
     └── auto-PR tries to create PR from already-merged main → fails or is meaningless
```

**Problems:**
1. `_fast_forward()` does `git checkout main` in the user's working repo
2. Tasks merge directly into main — no isolation
3. Local main diverges from remote (nothing is pushed until auto-PR)
4. User cannot work on another branch while pipeline runs
5. Auto-PR creates branch from already-merged state → confusing

## New Flow

```
main (untouched)
  └── forge/pipeline-{id}     ← created at pipeline start
        ←── task-1 rebases onto pipeline branch, ff-merge via update-ref
        ←── task-2 same
        ←── task-3 same
        └── auto-PR: forge/pipeline-{id} → main
```

**Key properties:**
- User's working directory is NEVER mutated
- `main` is NEVER touched locally
- All task merges go to the pipeline branch
- Code reaches main only through a PR
- Multiple pipelines can run simultaneously (different pipeline branches)

## Changes

### 1. `daemon.py` — Create pipeline branch as merge target

**Before (line 236-238):**
```python
current_branch = _get_current_branch(self._project_dir)
merge_worker = MergeWorker(self._project_dir, main_branch=current_branch)
```

**After:**
```python
base_branch = _get_current_branch(self._project_dir)
pipeline_branch = f"forge/pipeline-{pipeline_id[:8]}"
subprocess.run(
    ["git", "branch", pipeline_branch, base_branch],
    cwd=self._project_dir, check=True, capture_output=True,
)
merge_worker = MergeWorker(self._project_dir, main_branch=pipeline_branch)
```

Store `base_branch` in the database so auto-PR knows the PR target.

### 2. `worker.py` — Replace `_fast_forward()` with `update-ref`

**Before:**
```python
def _fast_forward(self, branch: str) -> None:
    subprocess.run(["git", "checkout", self._main], cwd=self._repo, ...)
    subprocess.run(["git", "merge", "--ff-only", branch], cwd=self._repo, ...)
```

**After:**
```python
def _fast_forward(self, branch: str) -> None:
    # Advance the merge target ref without checking it out.
    # This only works for fast-forward merges (guaranteed after rebase).
    task_sha = subprocess.run(
        ["git", "rev-parse", branch],
        cwd=self._repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-ref", f"refs/heads/{self._main}", task_sha],
        cwd=self._repo, check=True, capture_output=True,
    )
```

No `git checkout`. User's working directory is untouched.

### 3. `tasks.py` — Simplify `_auto_create_pr()`

The pipeline branch already exists with all merged code. Auto-PR just needs to:
1. Push the pipeline branch to remote
2. Create PR from `forge/pipeline-{id}` → `main` (base branch from DB)

No need to create a new branch or detect current branch at PR time.

### 4. `worktree.py` — No changes needed

Worktrees already branch from the current repo state. Since `git branch pipeline-branch base-branch` creates the pipeline branch as a ref, worktrees created after that point will rebase against the pipeline branch (via `_rebase()`).

### 5. Database — Store base branch on pipeline record

Add `base_branch` column to pipeline table so auto-PR knows the target. Falls back to `"main"` if not set (backward compat).

## Edge Cases

- **Empty repo:** Pipeline branch creation falls back to orphan branch (existing logic in worktree.py handles this)
- **Multiple concurrent pipelines:** Each gets its own `forge/pipeline-{id}` branch — no conflicts
- **Pipeline retry (task-2 retried):** Pipeline branch already exists, MergeWorker still targets it
- **User switches branches during pipeline:** No effect — pipeline branch is independent ref

## Files Changed

- `forge/merge/worker.py` — `_fast_forward()` replaced with `update-ref`
- `forge/core/daemon.py` — create pipeline branch, pass as merge target, store base_branch
- `forge/api/routes/tasks.py` — simplify `_auto_create_pr()` to use pipeline branch + base_branch from DB
- `forge/db/database.py` — add `base_branch` to pipeline record
- `forge/merge/worker_test.py` — update tests for new `_fast_forward` behavior

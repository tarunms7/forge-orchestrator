# Fix CI + Add Claude Code Auto-Fix Action

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 12 ruff lint errors breaking CI on main, then add Claude Code's GitHub Action as a second auto-fix layer for PR CI failures.

**Architecture:** Two-part fix: (1) resolve all ruff violations in `daemon_executor.py` and `ci_watcher_test.py`, (2) add a new GitHub Actions workflow using `anthropics/claude-code-action@v1` that triggers when CI fails on PRs and dispatches Claude to fix + push.

**Tech Stack:** Python/ruff, GitHub Actions, `anthropics/claude-code-action@v1`

---

### Task 1: Fix E402 — Move `_GIT_ADD_EXCLUDES` below imports in `daemon_executor.py`

**Files:**
- Modify: `forge/core/daemon_executor.py:1-52`

The E402 errors (8 of them) all stem from `_GIT_ADD_EXCLUDES` (a list constant, lines 12-27) sitting between the stdlib imports and the project imports. Moving it below all imports fixes all 8 E402 violations.

- [ ] **Step 1: Move the constant below imports**

In `forge/core/daemon_executor.py`, move the `_GIT_ADD_EXCLUDES` block (including its comment) from between lines 11-27 to after line 49 (after all imports, before `logger = ...`). The result should look like:

```python
"""ExecutorMixin — decomposed _execute_task extracted from ForgeDaemon."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import UTC, datetime

from forge.core.budget import BudgetExceededError, check_budget
from forge.core.daemon_helpers import (
    _build_agent_prompt,
    _build_retry_prompt,
    _extract_activity,
    _extract_implementation_summary,
    _find_related_test_files,
    _get_changed_files_vs_main,
    _get_diff_stats,
    _get_diff_vs_main,
    _load_conventions_md,
    _parse_forge_question,
    _resolve_ref,
    _run_git,
)
from forge.core.logging_config import make_console
from forge.core.model_router import select_model
from forge.core.models import TaskState
from forge.core.sanitize import validate_task_id
from forge.learning.guard import GuardTriggered, RuntimeGuard
from forge.learning.store import format_lessons_block, row_to_lesson

# Pathspec exclusions appended to every ``git add -A`` so that virtual
# environments, dependency caches, and build artifacts are never staged —
# even when the repo has no .gitignore.
# NOTE: Use long-form :(exclude) instead of :! because the short form
# breaks on some git versions when the path contains underscores
# (e.g., :!__pycache__ triggers "Unimplemented pathspec magic '_'").
_GIT_ADD_EXCLUDES: list[str] = [
    ":(exclude).venv",
    ":(exclude)venv",
    ":(exclude).env",
    ":(exclude)node_modules",
    ":(exclude)__pycache__",
    ":(exclude).ruff_cache",
    ":(exclude).pytest_cache",
    ":(exclude).mypy_cache",
]

logger = logging.getLogger("forge")
console = make_console()
```

- [ ] **Step 2: Run ruff to verify E402 is gone**

Run: `ruff check forge/core/daemon_executor.py --select E402`
Expected: No errors (0 found)

- [ ] **Step 3: Commit**

```bash
git add forge/core/daemon_executor.py
git commit -m "fix: move _GIT_ADD_EXCLUDES below imports to resolve E402 lint errors"
```

---

### Task 2: Fix I001 — Sort local imports in `daemon_executor.py:1113`

**Files:**
- Modify: `forge/core/daemon_executor.py:1113-1114`

The local imports inside `_on_task_answered` are in wrong order (`forge.storage.db` before `sqlalchemy`). Ruff expects third-party imports (`sqlalchemy`) before first-party (`forge.*`).

- [ ] **Step 1: Swap the import order**

At the local import block around line 1113, swap the two lines:

Before:
```python
            from forge.storage.db import TaskQuestionRow
            from sqlalchemy import select as sa_select
```

After:
```python
            from sqlalchemy import select as sa_select

            from forge.storage.db import TaskQuestionRow
```

- [ ] **Step 2: Run ruff to verify**

Run: `ruff check forge/core/daemon_executor.py --select I001`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add forge/core/daemon_executor.py
git commit -m "fix: sort local imports in _on_task_answered to resolve I001"
```

---

### Task 3: Fix F821 — Add `agent_id` parameter to `_attempt_merge`

**Files:**
- Modify: `forge/core/daemon_executor.py` — function signature at line ~1357 and all 3 call sites

`_attempt_merge` uses `agent_id` at line 1493 (passed to `_handle_agent_question`) but doesn't receive it as a parameter. Additionally, the call at line 1178 (in `_handle_review_answer`) passes arguments in the wrong positional order.

- [ ] **Step 1: Add `agent_id` parameter to `_attempt_merge` signature**

Change the function signature from:
```python
    async def _attempt_merge(
        self,
        db,
        merge_worker,
        worktree_mgr,
        task,
        task_id: str,
        worktree_path: str,
        agent_model: str,
        pid: str,
        *,
        pipeline_branch: str | None = None,
        pre_retry_ref: str | None = None,
        agent_summary: str = "",
    ) -> None:
```

To:
```python
    async def _attempt_merge(
        self,
        db,
        merge_worker,
        worktree_mgr,
        task,
        task_id: str,
        agent_id: str,
        worktree_path: str,
        agent_model: str,
        pid: str,
        *,
        pipeline_branch: str | None = None,
        pre_retry_ref: str | None = None,
        agent_summary: str = "",
    ) -> None:
```

- [ ] **Step 2: Fix call site at ~line 244 (in `_execute_task`)**

This call is missing `agent_id`. Add it:

Before:
```python
        await self._attempt_merge(
            db,
            merge_worker,
            worktree_mgr,
            task,
            task_id,
            worktree_path,
            agent_model,
            pid,
            pipeline_branch=pipeline_branch,
            pre_retry_ref=pre_retry_ref,
            agent_summary=agent_result.summary if agent_result else "",
        )
```

After:
```python
        await self._attempt_merge(
            db,
            merge_worker,
            worktree_mgr,
            task,
            task_id,
            agent_id,
            worktree_path,
            agent_model,
            pid,
            pipeline_branch=pipeline_branch,
            pre_retry_ref=pre_retry_ref,
            agent_summary=agent_result.summary if agent_result else "",
        )
```

- [ ] **Step 3: Fix call site at ~line 1036 (in `_resume_task`)**

Same fix — add `agent_id`:

Before:
```python
        await self._attempt_merge(
            db,
            merge_worker,
            worktree_mgr,
            task,
            task_id,
            worktree_path,
            agent_model,
            pid,
            pipeline_branch=pipeline_branch,
            agent_summary=agent_result.summary if agent_result else "",
        )
```

After:
```python
        await self._attempt_merge(
            db,
            merge_worker,
            worktree_mgr,
            task,
            task_id,
            agent_id,
            worktree_path,
            agent_model,
            pid,
            pipeline_branch=pipeline_branch,
            agent_summary=agent_result.summary if agent_result else "",
        )
```

- [ ] **Step 4: Fix call site at ~line 1178 (in `_handle_review_answer`) — wrong arg order**

This call has args in completely wrong positional order. The current call is:
```python
            await self._attempt_merge(
                db,
                self._merge_worker,
                task,
                task_id,
                agent_id,
                self._worktree_mgr,
                agent_model,
                pid,
            )
```

It should be (matching the updated signature order, and note: `_handle_review_answer` doesn't have `worktree_path` available — need to look up from the task):
```python
            worktree_path = getattr(task, "worktree_path", "") or ""
            await self._attempt_merge(
                db,
                self._merge_worker,
                self._worktree_mgr,
                task,
                task_id,
                agent_id,
                worktree_path,
                agent_model,
                pid,
            )
```

NOTE: Check whether `task.worktree_path` or similar attribute exists on the task object. If not, look at how other callers obtain `worktree_path` and replicate. The key pattern to look for is how `_execute_task` and `_resume_task` get `worktree_path` — they typically get it from `worktree_mgr.acquire()` or from the task's stored state. For a review-answer that already has a worktree, it may be `await db.get_task_worktree(task_id)` or stored on the task row.

- [ ] **Step 5: Run ruff to verify F821 is gone**

Run: `ruff check forge/core/daemon_executor.py --select F821`
Expected: No errors

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon_executor.py
git commit -m "fix: add agent_id param to _attempt_merge, fix broken call in _handle_review_answer"
```

---

### Task 4: Fix I001 + F401 in `ci_watcher_test.py`

**Files:**
- Modify: `forge/core/ci_watcher_test.py:1-21`

Two issues: unused import `CIFixResult` and unsorted import block.

- [ ] **Step 1: Auto-fix with ruff**

Run: `ruff check forge/core/ci_watcher_test.py --fix`

This handles both I001 (import sorting) and F401 (unused import removal) automatically.

- [ ] **Step 2: Verify clean**

Run: `ruff check forge/core/ci_watcher_test.py`
Expected: No errors

- [ ] **Step 3: Commit**

```bash
git add forge/core/ci_watcher_test.py
git commit -m "fix: remove unused import and sort imports in ci_watcher_test"
```

---

### Task 5: Run full ruff + format + tests to confirm green CI locally

**Files:** None (verification only)

- [ ] **Step 1: Run full ruff lint**

Run: `ruff check forge/`
Expected: `All checks passed!` (0 errors)

- [ ] **Step 2: Run ruff format check**

Run: `ruff format --check forge/`
Expected: All files formatted (exit 0)

- [ ] **Step 3: Run tests (same command as CI)**

Run:
```bash
python -m pytest forge/ -q --timeout=120 \
  --ignore=forge/tests/integration \
  --ignore=forge/core/integration_test.py \
  --deselect forge/core/planning_phase_test.py \
  --deselect forge/core/planning_regression_test.py \
  --deselect forge/core/daemon_executor_question_test.py::TestOnTaskAnswered::test_resumes_awaiting_input_task \
  --deselect forge/core/daemon_executor_question_test.py::TestOnTaskAnswered::test_skips_already_active_task \
  --deselect forge/core/daemon_helpers_test.py::TestGetDiffVsMainBaseRef::test_uses_base_ref_when_provided \
  --deselect forge/core/daemon_review_test.py::TestReviewUsesRepoConfig::test_review_uses_repo_config \
  --deselect forge/tui/widgets/logo_test.py::test_forge_logo_matches_expected_ascii_art \
  --deselect forge/agents/adapter_question_test.py::test_balanced_autonomy_protocol \
  --deselect forge/agents/adapter_test.py::test_build_options_has_no_allowed_tools \
  --ignore=forge/api/routes/tasks_test.py \
  --ignore=forge/api/routes/webhooks_test.py \
  --ignore=forge/api/routes/history_test.py \
  --deselect forge/tui/screens/pipeline_test.py::test_retry_emits_when_error \
  --deselect forge/tui/screens/pipeline_test.py::test_skip_emits_when_error \
  -k "not test_pipeline_lifecycle"
```

Expected: All tests pass

- [ ] **Step 4: If any test fails due to the `_attempt_merge` signature change, fix the test**

The signature change in Task 3 may break tests that mock or call `_attempt_merge`. Grep for `_attempt_merge` in test files and update any calls to include the new `agent_id` parameter.

---

### Task 6: Add Claude Code Auto-Fix GitHub Action

**Files:**
- Create: `.github/workflows/claude-autofix.yml`

This workflow triggers when the CI workflow fails on a PR, then dispatches Claude Code to diagnose and fix.

- [ ] **Step 1: Create the workflow file**

Create `.github/workflows/claude-autofix.yml`:

```yaml
name: Claude Auto-Fix

on:
  # Trigger when the CI workflow completes with failure on a PR
  workflow_run:
    workflows: ["CI"]
    types: [completed]

permissions:
  contents: write
  pull-requests: write
  issues: write

jobs:
  autofix:
    name: Auto-fix CI failures
    # Only run when CI failed AND the trigger was a pull_request (not a push to main)
    if: >
      github.event.workflow_run.conclusion == 'failure' &&
      github.event.workflow_run.event == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          ref: ${{ github.event.workflow_run.head_branch }}
          fetch-depth: 0

      - name: Get PR number
        id: pr
        run: |
          PR_NUMBER=$(gh pr list --head "${{ github.event.workflow_run.head_branch }}" --json number --jq '.[0].number')
          echo "number=${PR_NUMBER}" >> "$GITHUB_OUTPUT"
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}

      - name: Claude Code Auto-Fix
        if: steps.pr.outputs.number != ''
        uses: anthropics/claude-code-action@v1
        with:
          prompt: |
            CI has failed on this PR. Your job is to fix the failures so CI passes.

            1. Run `gh run view ${{ github.event.workflow_run.id }} --log-failed` to get the failure logs
            2. Diagnose the root cause from the logs
            3. Fix the code — lint errors, test failures, build issues, whatever is broken
            4. Run the failing checks locally to verify your fix works:
               - `ruff check forge/` for lint
               - `ruff format --check forge/` for format
               - `python -m pytest forge/ -q --timeout=120` for tests (with the same deselects as CI)
               - `cd web && npm ci && npm run build` for frontend
            5. Commit and push the fix

            Be surgical. Only fix what's needed. Do NOT refactor or add features.
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          model: sonnet
          max_turns: 30
          timeout_minutes: 15
```

- [ ] **Step 2: Verify YAML syntax**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/claude-autofix.yml'))" 2>&1 || echo "Install pyyaml: pip install pyyaml"`

If pyyaml not available, just visually verify the YAML is properly indented.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/claude-autofix.yml
git commit -m "feat: add Claude Code auto-fix GitHub Action for PR CI failures"
```

---

### Task 7: Final verification and push

- [ ] **Step 1: Run full lint + format + test suite one final time**

```bash
ruff check forge/ && ruff format --check forge/
```

Expected: 0 errors, all formatted.

- [ ] **Step 2: Push branch and create PR**

```bash
git push -u origin <branch-name>
gh pr create --title "fix: resolve all CI lint errors + add Claude auto-fix action" --body "..."
```

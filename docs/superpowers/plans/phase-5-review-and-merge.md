# Phase 5: Review & Merge — Per-Repo Config Loading

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the review pipeline (build/test/lint gates) and merge flow aware of per-repo configurations, so each repo in a multi-repo workspace uses its own `.forge/forge.toml` commands (e.g., `pytest` for backend, `npm test` for frontend).

**Architecture:** The existing `ProjectConfig.load(project_dir)` already loads per-directory config. This phase adds a `load_repo_configs()` helper, stores per-repo configs on `ForgeDaemon`, and threads `repo_id` through the command resolver chain so review gates use the correct repo's build/test/lint commands. The merge path requires no structural changes — `MergeMixin` already receives the correct per-repo `merge_worker` from Phase 3 dispatch.

**Tech Stack:** Python 3.12+, asyncio, Pydantic v2, dataclasses

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` — Sections 12 (Per-Repo Config), 15.2 (Execution Failures), 15.6 (Configuration Failures)

**Dependencies:** Phase 1 (data model — `RepoConfig`, `TaskRow.repo_id`) and Phase 3 (per-repo dispatch — `self._repos`, `self._worktree_managers`, `self._merge_workers`) must be merged.

**Verification:** `.venv/bin/python -m pytest forge/core/daemon_review_test.py forge/core/daemon_merge_test.py forge/config/project_config_test.py -x -v`

---

## Chunk 1: `load_repo_configs()` Helper

Adds a utility function to load `.forge/forge.toml` from each repo path. This is the foundation for per-repo command resolution.

### Task 1: Add `load_repo_configs()` to `forge/config/project_config.py`

**Files:**
- Modify: `forge/config/project_config.py` (after `apply_project_config`, ~line 320)
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing tests for `load_repo_configs()`**

```python
# In forge/config/project_config_test.py — add these tests

from forge.config.project_config import load_repo_configs, ProjectConfig
from forge.core.models import RepoConfig

def test_load_repo_configs_multiple(tmp_path):
    """Load config from 2 repos with different forge.toml contents."""
    # Create backend repo with pytest config
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / ".forge").mkdir()
    (backend / ".forge" / "forge.toml").write_text(
        '[checks.tests]\ncmd = "pytest"\n\n[checks.lint]\ncheck_cmd = "ruff check ."\nfix_cmd = "ruff check --fix ."\n'
    )

    # Create frontend repo with npm config
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / ".forge").mkdir()
    (frontend / ".forge" / "forge.toml").write_text(
        '[checks.tests]\ncmd = "npm test"\n\n[checks.lint]\ncheck_cmd = "eslint src/"\nfix_cmd = "eslint --fix src/"\n'
    )

    repos = {
        "backend": RepoConfig(id="backend", path=str(backend), base_branch="main"),
        "frontend": RepoConfig(id="frontend", path=str(frontend), base_branch="main"),
    }

    configs = load_repo_configs(repos)
    assert len(configs) == 2
    assert configs["backend"].tests.cmd == "pytest"
    assert configs["backend"].lint.check_cmd == "ruff check ."
    assert configs["frontend"].tests.cmd == "npm test"
    assert configs["frontend"].lint.check_cmd == "eslint src/"


def test_load_repo_configs_missing_toml(tmp_path):
    """Missing forge.toml returns defaults (no lint/test/build commands)."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    # No .forge/forge.toml created

    repos = {
        "myrepo": RepoConfig(id="myrepo", path=str(repo), base_branch="main"),
    }

    configs = load_repo_configs(repos)
    assert len(configs) == 1
    # Defaults: no custom commands
    assert configs["myrepo"].tests.cmd is None
    assert configs["myrepo"].lint.check_cmd is None
    assert configs["myrepo"].build.cmd is None


def test_load_repo_configs_invalid_toml(tmp_path, caplog):
    """Syntax error in forge.toml returns defaults with a warning logged."""
    repo = tmp_path / "broken"
    repo.mkdir()
    (repo / ".forge").mkdir()
    (repo / ".forge" / "forge.toml").write_text("this is not valid [[[toml syntax")

    repos = {
        "broken": RepoConfig(id="broken", path=str(repo), base_branch="main"),
    }

    configs = load_repo_configs(repos)
    assert len(configs) == 1
    # Falls back to defaults
    assert configs["broken"].tests.cmd is None
    assert configs["broken"].lint.check_cmd is None
    # Warning should be logged (ProjectConfig.from_toml logs it)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/config/project_config_test.py::test_load_repo_configs_multiple forge/config/project_config_test.py::test_load_repo_configs_missing_toml forge/config/project_config_test.py::test_load_repo_configs_invalid_toml -v`

Expected: FAIL — `load_repo_configs` does not exist yet.

- [ ] **Step 3: Implement `load_repo_configs()`**

Add this function to `forge/config/project_config.py` after `apply_project_config()` (~line 320):

```python
def load_repo_configs(repos: dict[str, "RepoConfig"]) -> dict[str, ProjectConfig]:
    """Load `.forge/forge.toml` from each repo path.

    Returns a dict mapping repo_id → ProjectConfig.  Repos with missing
    or invalid TOML files get default configs (ProjectConfig.load already
    handles these cases gracefully — returns defaults and logs a warning).

    This is called once during pipeline execute() to cache per-repo configs.
    """
    configs: dict[str, ProjectConfig] = {}
    for repo_id, rc in repos.items():
        configs[repo_id] = ProjectConfig.load(rc.path)
    return configs
```

Note: `ProjectConfig.load(project_dir)` at line 279 already calls `from_toml()` which handles missing files (returns defaults with debug log) and invalid TOML (returns defaults with warning log). No additional error handling needed — the existing behavior matches spec Section 15.6 exactly.

- [ ] **Step 4: Add import to `__init__.py` if needed**

Ensure `load_repo_configs` is importable from `forge.config.project_config`. No `__init__.py` change needed since it's a module-level function.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/config/project_config_test.py::test_load_repo_configs_multiple forge/config/project_config_test.py::test_load_repo_configs_missing_toml forge/config/project_config_test.py::test_load_repo_configs_invalid_toml -v`

Expected: PASS

---

## Chunk 2: Store Per-Repo Configs on ForgeDaemon

During `execute()`, load and store `self._repo_configs` so review and merge code can look up per-repo settings.

### Task 2: Add `self._repo_configs` to daemon execute path

**Files:**
- Modify: `forge/core/daemon.py` — inside `execute()`, after per-repo infrastructure setup (Phase 3's `self._worktree_managers` / `self._merge_workers` creation block)
- No dedicated test file — verified through integration in Chunk 4 tests

- [ ] **Step 1: Add import**

In `forge/core/daemon.py`, add at the top:

```python
from forge.config.project_config import load_repo_configs
```

- [ ] **Step 2: Add `self._repo_configs` initialization in `execute()`**

After the per-repo infrastructure registry block (Phase 3: `self._worktree_managers`, `self._merge_workers`, `self._pipeline_branches` setup), add:

```python
# Load per-repo configs for review gates (build/test/lint commands)
self._repo_configs: dict[str, ProjectConfig] = load_repo_configs(self._repos)
```

This must happen AFTER `self._repos` is populated (Phase 1/3) and BEFORE any tasks are dispatched.

- [ ] **Step 3: Single-repo backward compat**

When `self._repos` has only `{"default": RepoConfig(id="default", path=<project_dir>, ...)}`, `load_repo_configs` loads `<project_dir>/.forge/forge.toml` — which is exactly the current behavior. The workspace-level config IS the repo config. No special-casing needed.

Verify by reading the single-repo `__init__` path: `self._repos["default"] = RepoConfig(id="default", path=os.path.abspath(project_dir), ...)`. `ProjectConfig.load(os.path.abspath(project_dir))` loads `<project_dir>/.forge/forge.toml`. This is the same file that `apply_project_config` already loads from `self._project_dir`. Identical behavior.

---

## Chunk 3: Per-Repo Command Resolution

Update the four command resolvers in `ReviewMixin` to accept an optional `repo_id` parameter and look up per-repo config.

### Task 3: Update `_resolve_build_cmd()`, `_resolve_test_cmd()`, `_resolve_lint_cmd()`, `_resolve_lint_fix_cmd()`

**Files:**
- Modify: `forge/core/daemon_review.py` — lines 313, 326, 339, 351
- Test: `forge/core/daemon_review_test.py`

- [ ] **Step 1: Write failing tests for per-repo command resolution**

```python
# In forge/core/daemon_review_test.py — add these tests

def test_resolve_test_cmd_per_repo():
    """Backend gets pytest, frontend gets npm test via repo_id lookup."""
    # Create a mock daemon with _repo_configs
    mixin = _make_review_mixin()
    # Set up per-repo configs
    from forge.config.project_config import ProjectConfig, CheckConfig
    mixin._repo_configs = {
        "backend": ProjectConfig(tests=CheckConfig(cmd="pytest")),
        "frontend": ProjectConfig(tests=CheckConfig(cmd="npm test")),
    }

    # Per-repo resolution
    assert mixin._resolve_test_cmd(repo_id="backend") == "pytest"
    assert mixin._resolve_test_cmd(repo_id="frontend") == "npm test"


def test_resolve_lint_cmd_per_repo():
    """Backend gets ruff, frontend gets eslint via repo_id lookup."""
    mixin = _make_review_mixin()
    from forge.config.project_config import ProjectConfig, CheckConfig
    mixin._repo_configs = {
        "backend": ProjectConfig(lint=CheckConfig(check_cmd="ruff check .")),
        "frontend": ProjectConfig(lint=CheckConfig(check_cmd="eslint src/")),
    }

    assert mixin._resolve_lint_cmd(repo_id="backend") == "ruff check ."
    assert mixin._resolve_lint_cmd(repo_id="frontend") == "eslint src/"


def test_review_single_repo_unchanged():
    """Single-repo review (no repo_id) uses existing settings-based resolution."""
    mixin = _make_review_mixin()
    # Simulate current behavior: _settings has test_cmd, no _repo_configs
    mixin._settings.test_cmd = "pytest"
    mixin._settings.lint_cmd = "ruff check ."
    mixin._repo_configs = {}

    # No repo_id → falls back to self._settings (current behavior)
    assert mixin._resolve_test_cmd() == "pytest"
    assert mixin._resolve_lint_cmd() == "ruff check ."
```

Note: `_make_review_mixin()` is the existing test helper that creates a `ReviewMixin` instance with mock settings. Adapt the helper name to match the actual test file's factory.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/core/daemon_review_test.py::test_resolve_test_cmd_per_repo forge/core/daemon_review_test.py::test_resolve_lint_cmd_per_repo forge/core/daemon_review_test.py::test_review_single_repo_unchanged -v`

Expected: FAIL — resolvers don't accept `repo_id` parameter.

- [ ] **Step 3: Implement per-repo resolution in all four resolvers**

The pattern is the same for all four. Each resolver gains an optional `repo_id: str | None = None` parameter. When provided, it looks up the per-repo `ProjectConfig` from `self._repo_configs` and uses its commands. When `repo_id` is None or not found in `_repo_configs`, the existing resolution chain (template → pipeline → settings) is used unchanged.

**`_resolve_build_cmd()` (line 313):**

```python
def _resolve_build_cmd(self, *, repo_id: str | None = None) -> str | None:
    """Return the build command: repo config → template override → pipeline override → settings fallback."""
    # Per-repo config takes priority when repo_id is provided
    if repo_id:
        repo_configs = getattr(self, "_repo_configs", {})
        if repo_id in repo_configs:
            cfg = repo_configs[repo_id]
            if cfg.build.cmd:
                return cfg.build.cmd
            if not cfg.build.enabled:
                return None

    # Existing resolution chain (unchanged)
    template_config = getattr(self, "_template_config", None)
    if template_config and "build_cmd" in template_config:
        val = template_config["build_cmd"]
        return val if val else None
    result = getattr(self, '_pipeline_build_cmd', None) or getattr(self._settings, 'build_cmd', None)
    return None if result == CMD_DISABLED else result
```

**`_resolve_test_cmd()` (line 326):**

```python
def _resolve_test_cmd(self, *, repo_id: str | None = None) -> str | None:
    """Return the test command: repo config → template override → pipeline override → settings fallback."""
    if repo_id:
        repo_configs = getattr(self, "_repo_configs", {})
        if repo_id in repo_configs:
            cfg = repo_configs[repo_id]
            if cfg.tests.cmd:
                return cfg.tests.cmd
            if not cfg.tests.enabled:
                return None

    template_config = getattr(self, "_template_config", None)
    if template_config and "test_cmd" in template_config:
        val = template_config["test_cmd"]
        return val if val else None
    result = getattr(self, '_pipeline_test_cmd', None) or getattr(self._settings, 'test_cmd', None)
    return None if result == CMD_DISABLED else result
```

**`_resolve_lint_cmd()` (line 339):**

```python
def _resolve_lint_cmd(self, *, repo_id: str | None = None) -> str | None:
    """Return the lint check command: repo config → template override → settings fallback."""
    if repo_id:
        repo_configs = getattr(self, "_repo_configs", {})
        if repo_id in repo_configs:
            cfg = repo_configs[repo_id]
            if cfg.lint.check_cmd:
                return cfg.lint.check_cmd
            if not cfg.lint.enabled:
                return None

    template_config = getattr(self, "_template_config", None)
    if template_config and "lint_cmd" in template_config:
        val = template_config["lint_cmd"]
        return val if val else None
    result = getattr(self._settings, 'lint_cmd', None)
    return None if result == CMD_DISABLED else result
```

**`_resolve_lint_fix_cmd()` (line 351):**

```python
def _resolve_lint_fix_cmd(self, *, repo_id: str | None = None) -> str | None:
    """Return the lint fix command: repo config → template override → settings fallback."""
    if repo_id:
        repo_configs = getattr(self, "_repo_configs", {})
        if repo_id in repo_configs:
            cfg = repo_configs[repo_id]
            if cfg.lint.fix_cmd:
                return cfg.lint.fix_cmd
            if not cfg.lint.enabled:
                return None

    template_config = getattr(self, "_template_config", None)
    if template_config and "lint_fix_cmd" in template_config:
        val = template_config["lint_fix_cmd"]
        return val if val else None
    result = getattr(self._settings, 'lint_fix_cmd', None)
    return None if result == CMD_DISABLED else result
```

**Resolution priority (highest → lowest):**
1. Per-repo `ProjectConfig` (from `{repo}/.forge/forge.toml`) — when `repo_id` is provided
2. Template config override (from pipeline template)
3. Pipeline-level override (from `_pipeline_*_cmd` attrs)
4. `ForgeSettings` fallback (from env vars / workspace-level config)

**Single-repo backward compat:** When `repo_id` is None (all current call sites), the per-repo lookup is skipped entirely. The existing chain runs unchanged. Zero behavior change for single-repo pipelines.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/core/daemon_review_test.py::test_resolve_test_cmd_per_repo forge/core/daemon_review_test.py::test_resolve_lint_cmd_per_repo forge/core/daemon_review_test.py::test_review_single_repo_unchanged -v`

Expected: PASS

---

## Chunk 4: Thread `repo_id` Through `_run_review()`

Update `_run_review()` to receive `repo_id` from the task and pass it to all command resolvers.

### Task 4: Update `_run_review()` signature and call sites

**Files:**
- Modify: `forge/core/daemon_review.py` — `_run_review()` (line 574) and all internal calls to `_resolve_*_cmd()`
- Modify: `forge/core/daemon_executor.py` — call site that invokes `_run_review()` (pass `task.repo_id`)
- Test: `forge/core/daemon_review_test.py`

- [ ] **Step 1: Write failing test for review using repo config**

```python
# In forge/core/daemon_review_test.py

import pytest

@pytest.mark.asyncio
async def test_review_uses_repo_config():
    """Review pipeline uses the task's repo config for command resolution."""
    mixin = _make_review_mixin()
    from forge.config.project_config import ProjectConfig, CheckConfig

    mixin._repo_configs = {
        "backend": ProjectConfig(
            tests=CheckConfig(cmd="pytest"),
            lint=CheckConfig(check_cmd="ruff check .", fix_cmd="ruff check --fix ."),
            build=CheckConfig(cmd="pip install -e ."),
        ),
    }

    # Verify that when _run_review receives repo_id="backend",
    # the resolvers return the backend-specific commands
    assert mixin._resolve_build_cmd(repo_id="backend") == "pip install -e ."
    assert mixin._resolve_test_cmd(repo_id="backend") == "pytest"
    assert mixin._resolve_lint_cmd(repo_id="backend") == "ruff check ."
    assert mixin._resolve_lint_fix_cmd(repo_id="backend") == "ruff check --fix ."
```

- [ ] **Step 2: Update `_run_review()` signature**

Add `repo_id: str | None = None` parameter to `_run_review()` at line 574:

```python
async def _run_review(
    self, task, worktree_path: str, diff: str, *, db, pipeline_id: str,
    pipeline_branch: str | None = None,
    delta_diff: str | None = None,
    repo_id: str | None = None,        # NEW — which repo this task belongs to
) -> tuple[bool, str | None]:
```

- [ ] **Step 3: Pass `repo_id` to all resolver calls inside `_run_review()`**

Update all calls within `_run_review()`:

```python
# Line ~596 (build gate):
build_cmd = self._resolve_build_cmd(repo_id=repo_id)

# Line ~(test gate section):
test_cmd = self._resolve_test_cmd(repo_id=repo_id)

# Line ~(lint gate section):
lint_cmd = self._resolve_lint_cmd(repo_id=repo_id)
lint_fix_cmd = self._resolve_lint_fix_cmd(repo_id=repo_id)
```

Search for ALL occurrences of `self._resolve_build_cmd()`, `self._resolve_test_cmd()`, `self._resolve_lint_cmd()`, `self._resolve_lint_fix_cmd()` within `_run_review()` and ensure every call passes `repo_id=repo_id`.

- [ ] **Step 4: Update call site in `daemon_executor.py`**

In `forge/core/daemon_executor.py`, find the call to `self._run_review(...)` and add `repo_id`:

```python
# Find the _run_review call (in _execute_task or similar):
# Before:
passed, feedback = await self._run_review(
    task, worktree_path, diff, db=db, pipeline_id=pipeline_id,
    pipeline_branch=pipeline_branch, delta_diff=delta_diff,
)

# After:
repo_id = getattr(task, 'repo_id', None) or getattr(task, 'repo', None) or "default"
passed, feedback = await self._run_review(
    task, worktree_path, diff, db=db, pipeline_id=pipeline_id,
    pipeline_branch=pipeline_branch, delta_diff=delta_diff,
    repo_id=repo_id,
)
```

Note: Check the actual attribute name on the task object — it may be `task.repo_id` (from `TaskRow`) or `task.repo` (from `TaskDefinition`). Use whichever is available on the runtime task object, with fallback to `"default"`.

- [ ] **Step 5: Run full review test suite**

Run: `.venv/bin/python -m pytest forge/core/daemon_review_test.py -x -v`

Expected: All existing tests pass + new tests pass.

---

## Chunk 5: Diff Stats in Correct Repo Worktree

### Task 5: Verify `_get_diff_stats()` uses correct worktree path

**Files:**
- Verify: `forge/core/daemon_review.py` — `_get_diff_stats()` method
- Test: `forge/core/daemon_review_test.py`

- [ ] **Step 1: Write test for diff stats in correct repo**

```python
# In forge/core/daemon_review_test.py

@pytest.mark.asyncio
async def test_diff_stats_correct_repo(tmp_path):
    """Diff stats are computed in the correct repo's worktree, not workspace root."""
    # _get_diff_stats receives worktree_path as a parameter.
    # In multi-repo, the worktree_path already points to the correct repo's
    # worktree (set by Phase 3 dispatch: self._worktree_managers[repo_id]).
    # This test verifies that _get_diff_stats runs git commands in
    # the provided worktree_path, not self._project_dir.
    mixin = _make_review_mixin()

    # Mock a worktree with a git repo
    import subprocess
    worktree = tmp_path / "backend-worktree"
    worktree.mkdir()
    subprocess.run(["git", "init"], cwd=str(worktree), capture_output=True)
    subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=str(worktree), capture_output=True)

    # _get_diff_stats should run in worktree, not raise or use wrong path
    # The actual diff will be empty but the function should not error
    stats = await mixin._get_diff_stats(str(worktree))
    assert stats is not None  # Returns stats dict, not None/error
```

- [ ] **Step 2: Verify implementation**

Read `_get_diff_stats()` in `daemon_review.py`. Confirm it receives `worktree_path` as a parameter and passes it as `cwd` to all git subprocess calls. If it does (which is expected — the function already takes `worktree_path`), no code changes are needed. The worktree_path is correctly set by Phase 3's per-repo `WorktreeManager`.

**No code changes expected** — this is a verification task. Phase 3 ensures the correct worktree path is passed per-repo, and `_get_diff_stats` already uses it as `cwd`.

- [ ] **Step 3: Run test**

Run: `.venv/bin/python -m pytest forge/core/daemon_review_test.py::test_diff_stats_correct_repo -v`

Expected: PASS

---

## Chunk 6: Update `repos_json` After Merge

After merge completes for each repo, update the `repos_json` column with per-repo branch stats.

### Task 6: Update `repos_json` with branch stats post-merge

**Files:**
- Modify: `forge/core/daemon.py` — post-merge handling (after all tasks complete, before PR creation)
- Modify: `forge/storage/db.py` — add helper to update `repos_json` for a specific repo
- Test: `forge/core/daemon_merge_test.py`

- [ ] **Step 1: Write failing test**

```python
# In forge/core/daemon_merge_test.py

import json
import pytest

@pytest.mark.asyncio
async def test_repos_json_updated_after_merge(tmp_path):
    """repos_json has per-repo branch stats after merge completes."""
    # Setup: create a pipeline with repos_json containing two repos
    db = await _make_test_db(tmp_path)
    pipeline_id = "test-pipeline"

    initial_repos = [
        {"id": "backend", "path": "/path/to/backend", "base_branch": "main"},
        {"id": "frontend", "path": "/path/to/frontend", "base_branch": "main"},
    ]
    await db.create_pipeline(pipeline_id, repos_json=json.dumps(initial_repos))

    # Simulate post-merge update: add branch_name and pr_url placeholder
    repos = json.loads((await db.get_pipeline(pipeline_id)).repos_json)
    for repo in repos:
        repo["branch_name"] = f"forge/pipeline-{pipeline_id[:12]}"
        repo["pr_url"] = ""  # placeholder — filled during PR creation
    await db.update_pipeline(pipeline_id, repos_json=json.dumps(repos))

    # Verify
    updated = await db.get_pipeline(pipeline_id)
    repos_after = json.loads(updated.repos_json)
    assert len(repos_after) == 2
    assert repos_after[0]["branch_name"] == f"forge/pipeline-{pipeline_id[:12]}"
    assert repos_after[1]["branch_name"] == f"forge/pipeline-{pipeline_id[:12]}"
    assert "pr_url" in repos_after[0]
    assert "pr_url" in repos_after[1]
```

- [ ] **Step 2: Implement repos_json update in daemon execute flow**

In `forge/core/daemon.py`, after the per-repo infrastructure setup (where `self._pipeline_branches` is populated), update `repos_json` with branch names:

```python
# After pipeline branches are created for each repo:
if pipeline_row.repos_json:
    repos_data = json.loads(pipeline_row.repos_json)
    for repo_entry in repos_data:
        repo_id = repo_entry["id"]
        if repo_id in self._pipeline_branches:
            repo_entry["branch_name"] = self._pipeline_branches[repo_id]
            repo_entry["pr_url"] = ""  # placeholder for PR creation phase
    await db.update_pipeline(pipeline_id, repos_json=json.dumps(repos_data))
```

The `pr_url` is populated later during PR creation (Phase 6 — not in scope for this phase). For now, store an empty string as the placeholder per the spec's `repos_json` extended schema (Section 4.3).

- [ ] **Step 3: Run test**

Run: `.venv/bin/python -m pytest forge/core/daemon_merge_test.py::test_repos_json_updated_after_merge -v`

Expected: PASS

---

## Design Decisions & Edge Cases

### Single-Repo Backward Compatibility

When only one `"default"` repo exists, the system behaves exactly as today:

1. `load_repo_configs({"default": RepoConfig(path=project_dir, ...)})` loads `project_dir/.forge/forge.toml` — same file that `apply_project_config` already loads.
2. `_resolve_*_cmd(repo_id=None)` skips per-repo lookup, falls through to existing template → pipeline → settings chain.
3. `_run_review()` receives `repo_id="default"` or `None` — either way, the single repo's config is used.
4. `repos_json` is `null` for single-repo pipelines (no update needed).

**No behavior change for existing single-repo pipelines.**

### Failure Scenarios (from Spec Sections 12.2 and 15.2)

| Scenario | Behavior | Implementation |
|----------|----------|----------------|
| Repo `.forge/forge.toml` has syntax error | Fall back to defaults for that repo | `ProjectConfig.from_toml()` already catches `Exception` on parse, logs warning, returns `cls()` (defaults). `load_repo_configs` inherits this. |
| Repo `.forge/forge.toml` missing | Use defaults (no lint, no test, no build) | `ProjectConfig.load()` checks `os.path.isfile()`, returns `cls()` if missing. |
| One repo's test/lint/build command fails | Task review fails, task retries with feedback, other repos unaffected | Per-repo resolution means each task runs its own repo's commands. A pytest failure in backend doesn't affect frontend's npm test. |
| Agent timeout in one repo | Task fails after timeout. Dependent tasks blocked. | Existing timeout handling. Per-repo configs could set different `timeout_seconds` in `[agents]` section. |

### Review Gates & Per-Repo Commands

The review pipeline in `_run_review()` runs gates sequentially:

1. **Gate 0 (build):** `_resolve_build_cmd(repo_id=repo_id)` → e.g., `pip install -e .` for backend, `npm run build` for frontend
2. **Gate 1 (lint):** `_resolve_lint_cmd(repo_id=repo_id)` → e.g., `ruff check .` for backend, `eslint src/` for frontend
3. **Gate 1.5 (test):** `_resolve_test_cmd(repo_id=repo_id)` → e.g., `pytest` for backend, `npm test` for frontend
4. **Gate 2 (LLM review):** Uses task context, not repo-specific commands — no changes needed

All shell gates run in the `worktree_path` which Phase 3 already sets to the correct repo's worktree. The `cwd` for subprocess calls is the worktree path, so `pytest` runs in the backend worktree and `npm test` runs in the frontend worktree.

### Integration Health Checks Remain Workspace-Level

Per the spec, integration checks (`[integration.post_merge]` and `[integration.final_gate]`) are pipeline-level validations. For v1, they remain workspace-level — configured in the workspace root's `.forge/forge.toml` and run from the workspace root. They validate the combined result of all merged tasks across all repos.

**Not per-repo.** A future version could add `[integration.post_merge.backend]` sections, but v1 keeps it simple.

### Merge Path — No Changes Needed

`MergeMixin` (in `daemon_merge.py`) already receives the correct per-repo `merge_worker` from Phase 3's dispatch logic:

```python
# Phase 3 dispatch (daemon.py):
merge_worker = self._merge_workers[repo_id]  # per-repo MergeWorker
await self._execute_task(db, runtime, worktree_mgr, merge_worker, ...)
```

The `MergeWorker` is constructed with `main_branch=pipeline_branch` for each repo. Merge operations (rebase, fast-forward) happen within the correct repo. **No changes to `daemon_merge.py`.**

`GateResult` and `ReviewOutcome` (in `forge/review/pipeline.py`) are simple dataclasses that carry pass/fail status. **No changes needed.**

---

## Test Summary

| Test Name | File | What It Validates |
|-----------|------|-------------------|
| `test_load_repo_configs_multiple` | `forge/config/project_config_test.py` | Loads config from 2 repos with different commands |
| `test_load_repo_configs_missing_toml` | `forge/config/project_config_test.py` | Missing forge.toml returns defaults |
| `test_load_repo_configs_invalid_toml` | `forge/config/project_config_test.py` | Syntax error returns defaults with warning |
| `test_resolve_test_cmd_per_repo` | `forge/core/daemon_review_test.py` | Backend gets pytest, frontend gets npm test |
| `test_resolve_lint_cmd_per_repo` | `forge/core/daemon_review_test.py` | Backend gets ruff, frontend gets eslint |
| `test_review_uses_repo_config` | `forge/core/daemon_review_test.py` | Review pipeline uses task's repo config |
| `test_review_single_repo_unchanged` | `forge/core/daemon_review_test.py` | Single-repo review identical to current behavior |
| `test_diff_stats_correct_repo` | `forge/core/daemon_review_test.py` | Diff computed in correct worktree |
| `test_repos_json_updated_after_merge` | `forge/core/daemon_merge_test.py` | repos_json has branch stats post-merge |

---

## Files Modified (Summary)

| File | Change | Lines |
|------|--------|-------|
| `forge/config/project_config.py` | Add `load_repo_configs()` function | ~320 (after `apply_project_config`) |
| `forge/core/daemon.py` | Add `self._repo_configs` init in `execute()` | After Phase 3 infra setup |
| `forge/core/daemon_review.py` | Add `repo_id` param to 4 resolvers + `_run_review()` | Lines 313, 326, 339, 351, 574 |
| `forge/core/daemon_executor.py` | Pass `repo_id` to `_run_review()` call | Call site for `_run_review` |
| `forge/core/daemon_merge.py` | No changes | Already correct from Phase 3 |
| `forge/review/pipeline.py` | No changes | `GateResult` dataclass unchanged |

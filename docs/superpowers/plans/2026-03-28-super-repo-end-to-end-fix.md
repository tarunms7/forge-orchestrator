# Super-Repo (Multi-Repo Workspace) End-to-End Fix

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Forge work flawlessly when `project_dir` is a plain folder (or an empty git-init'd wrapper) containing multiple git repos as subdirectories.

**Architecture:** Add a `_repo_paths(repos, project_dir)` helper that returns the list of directories to run git commands against. Every git operation that currently uses `project_dir` directly switches to iterating over this list. Single-repo mode is unaffected — the helper returns `[project_dir]` when repos is None or has one "default" entry.

**Tech Stack:** Python 3.12, asyncio, pytest

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `forge/core/preflight.py` | Pre-flight validation | Modify — make `_check_git_repo`, `_check_base_branch`, `_check_working_tree_clean` multi-repo aware |
| `forge/core/preflight_test.py` | Preflight tests | Modify — add super-repo test cases |
| `forge/core/daemon.py` | Pipeline orchestration | Modify — fix `_preflight_checks()`, `_auto_detect_commands()`, pipeline branch creation |
| `forge/core/daemon_executor.py` | Task execution & post-merge | Modify — fix `run_post_merge_check` to use repo path |
| `forge/core/integration.py` | Integration health checks | Modify — `_temp_health_worktree` accepts `repo_path` |
| `forge/core/integration_test.py` | Integration tests | No change needed (tests already pass repo paths via `git_repo` fixture) |
| `forge/cli/clean.py` | Cleanup command | Modify — skip non-git top-level dir |

---

### Task 1: Fix `_check_git_repo` in preflight.py

**Files:**
- Modify: `forge/core/preflight.py:69-107` (run_preflight), `forge/core/preflight.py:187-205` (_check_git_repo)
- Test: `forge/core/preflight_test.py`

- [ ] **Step 1: Write failing test for super-repo preflight**

Add to `forge/core/preflight_test.py`:

```python
@pytest.mark.asyncio
async def test_run_preflight_super_repo(tmp_path):
    """Preflight passes when project_dir is a plain folder with git repos inside."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Create two sub-repos inside a plain (non-git) folder
    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )

    from forge.core.models import RepoConfig

    repos = {
        "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
        "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
    }

    report = await run_preflight(str(tmp_path), repos=repos)

    # Git repo check must pass (checking sub-repos, not the wrapper)
    git_check = next((c for c in report.checks if c.name == "git_repo"), None)
    assert git_check is not None
    assert git_check.passed, f"git_repo check failed: {git_check.message}"

    # Base branch check must pass
    branch_check = next((c for c in report.checks if c.name == "base_branch"), None)
    assert branch_check is not None
    assert branch_check.passed, f"base_branch check failed: {branch_check.message}"

    # Overall must pass
    assert report.passed, f"Preflight failed: {report.summary()}"


@pytest.mark.asyncio
async def test_run_preflight_super_repo_git_wrapper(tmp_path):
    """Preflight passes when project_dir is a git-init'd wrapper with repos inside."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Init the wrapper (but no commits, no tracked files)
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)

    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )

    from forge.core.models import RepoConfig

    repos = {
        "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
        "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
    }

    report = await run_preflight(str(tmp_path), repos=repos)
    git_check = next((c for c in report.checks if c.name == "git_repo"), None)
    assert git_check is not None
    assert git_check.passed, f"git_repo check failed: {git_check.message}"
    assert report.passed, f"Preflight failed: {report.summary()}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/core/preflight_test.py::test_run_preflight_super_repo -xvs`
Expected: FAIL — `_check_git_repo` returns "Not a git repository" for the plain wrapper folder.

- [ ] **Step 3: Fix `_check_git_repo` to accept repos**

In `forge/core/preflight.py`, change `_check_git_repo` signature and logic:

```python
async def _check_git_repo(project_dir: str, repos: dict | None = None) -> CheckResult:
    """Verify git repositories are accessible.

    Multi-repo: checks each repo path. Single-repo: checks project_dir.
    """
    dirs_to_check = []
    if repos and len(repos) > 1:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path))
    else:
        dirs_to_check.append(("default", project_dir))

    failed = []
    for repo_id, path in dirs_to_check:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "rev-parse",
            "--git-dir",
            cwd=path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=5)
        if proc.returncode != 0:
            label = f"{repo_id} ({path})" if repo_id != "default" else path
            failed.append(label)

    if failed:
        return CheckResult(
            name="git_repo",
            passed=False,
            message=f"Not a git repository: {', '.join(failed)}",
            fix_hint="Run `git init` or check repo paths in .forge/workspace.toml",
        )
    count = len(dirs_to_check)
    msg = f"{count} git repositories detected" if count > 1 else "Git repository detected"
    return CheckResult(name="git_repo", passed=True, message=msg)
```

- [ ] **Step 4: Update `run_preflight` to pass repos to `_check_git_repo`**

In `run_preflight()`, change line 88 from:

```python
        _check_git_repo(project_dir),
```

to:

```python
        _check_git_repo(project_dir, repos),
```

- [ ] **Step 5: Fix `_check_base_branch` and `_check_working_tree_clean` fallback logic**

Both functions have this pattern:

```python
if repos and len(repos) > 1:
    # use repo paths
else:
    dirs_to_check.append(("default", project_dir, base_branch))
```

Change both to use repos when ANY repos are provided (not just when > 1):

In `_check_base_branch` (line 212-217), change:

```python
    dirs_to_check = []
    if repos and len(repos) > 1:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path, rc.base_branch or base_branch))
    else:
        dirs_to_check.append(("default", project_dir, base_branch))
```

to:

```python
    dirs_to_check = []
    if repos:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path, rc.base_branch or base_branch))
    else:
        dirs_to_check.append(("default", project_dir, base_branch))
```

In `_check_working_tree_clean` (line 259-264), change:

```python
    dirs_to_check = []
    if repos and len(repos) > 1:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path))
    else:
        dirs_to_check.append(("default", project_dir))
```

to:

```python
    dirs_to_check = []
    if repos:
        for repo_id, rc in repos.items():
            dirs_to_check.append((repo_id, rc.path))
    else:
        dirs_to_check.append(("default", project_dir))
```

- [ ] **Step 6: Run all preflight tests**

Run: `python -m pytest forge/core/preflight_test.py -xvs`
Expected: ALL PASS including the two new super-repo tests.

- [ ] **Step 7: Commit**

```bash
git add forge/core/preflight.py forge/core/preflight_test.py
git commit -m "fix(preflight): make git checks multi-repo aware for super-repo workspaces

_check_git_repo now checks each repo path instead of project_dir when
repos are provided. _check_base_branch and _check_working_tree_clean
now use repos when any are provided, not only when len > 1."
```

---

### Task 2: Fix daemon `_preflight_checks()` for multi-repo

**Files:**
- Modify: `forge/core/daemon.py:451-545` (_auto_detect_commands, _preflight_checks)

- [ ] **Step 1: Write failing test for daemon preflight with super-repo**

Add to `forge/core/daemon_test.py` (or `forge/core/daemon_autodetect_test.py` if that's more appropriate):

```python
@pytest.mark.asyncio
async def test_preflight_checks_multi_repo(tmp_path):
    """Daemon preflight passes for a multi-repo workspace (plain wrapper dir)."""
    import subprocess

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon
    from forge.core.models import RepoConfig

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    repos = []
    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )
        repos.append(RepoConfig(id=name, path=str(repo_dir), base_branch="main"))

    daemon = ForgeDaemon(str(tmp_path), settings=ForgeSettings(), repos=repos)

    # Mock DB
    from unittest.mock import AsyncMock

    db = AsyncMock()
    db.update_pipeline_status = AsyncMock()
    db.log_event = AsyncMock()

    result = await daemon._preflight_checks(str(tmp_path), db, "test-pipeline")
    assert result is True, "Preflight should pass for multi-repo workspace"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/core/daemon_test.py::test_preflight_checks_multi_repo -xvs`
Expected: FAIL — `git rev-parse --is-inside-work-tree` fails on the plain wrapper dir.

- [ ] **Step 3: Fix `_preflight_checks` to use repo paths**

In `forge/core/daemon.py`, replace `_preflight_checks` (lines 495-545):

```python
    async def _preflight_checks(self, project_dir: str, db: Database, pipeline_id: str) -> bool:
        """Run pre-execution validation. Returns True if all checks pass."""
        self._auto_detect_commands(project_dir)
        errors = []

        # Determine which directories to check — repo paths for multi-repo,
        # project_dir for single-repo.
        multi = len(self._repos) > 1
        check_dirs = (
            [(rid, rc.path) for rid, rc in self._repos.items()]
            if multi
            else [("default", project_dir)]
        )

        for repo_id, repo_path in check_dirs:
            label = f" [{repo_id}]" if multi else ""

            # Valid git repo?
            result = await async_subprocess(
                ["git", "rev-parse", "--is-inside-work-tree"],
                cwd=repo_path,
            )
            if result.returncode != 0:
                errors.append(f"Not a git repository{label}: {repo_path}")
                continue  # Skip remaining checks for this repo

            # Ensure at least one commit exists (worktrees need valid HEAD)
            has_commits_result = await async_subprocess(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
            )
            if has_commits_result.returncode != 0:
                console.print(f"[dim]  Creating initial commit{label} (empty repo)...[/dim]")
                await async_subprocess(
                    ["git", "commit", "--allow-empty", "-m", "chore: initial commit (forge)"],
                    cwd=repo_path,
                )

            # Git remote (warning only)
            result = await async_subprocess(
                ["git", "remote"],
                cwd=repo_path,
            )
            if not result.stdout.strip():
                console.print(
                    f"[yellow]  Warning: No git remote configured{label}. PR creation will be skipped.[/yellow]"
                )

        # gh CLI auth (optional, check once)
        if shutil.which("gh"):
            result = await async_subprocess(
                ["gh", "auth", "status"], cwd=check_dirs[0][1]
            )
            if result.returncode != 0:
                console.print(
                    "[yellow]  Warning: gh CLI not authenticated (PR creation will fail)[/yellow]"
                )

        if errors:
            console.print(f"[bold red]Pre-flight failed: {'; '.join(errors)}[/bold red]")
            await self._emit(
                "pipeline:preflight_failed", {"errors": errors}, db=db, pipeline_id=pipeline_id
            )
            await db.update_pipeline_status(pipeline_id, "error")
            return False
        return True
```

- [ ] **Step 4: Fix `_auto_detect_commands` to check repo paths**

In `forge/core/daemon.py`, update `_auto_detect_commands` (lines 451-493). When multi-repo, scan each repo path for `package.json`, `pyproject.toml`, `Makefile` instead of just `project_dir`:

```python
    def _auto_detect_commands(self, project_dir: str) -> None:
        """Auto-detect build_cmd and test_cmd from project config files."""
        # For multi-repo, check each repo. For single-repo, check project_dir.
        dirs_to_scan = (
            [rc.path for rc in self._repos.values()]
            if len(self._repos) > 1
            else [project_dir]
        )

        # --- build_cmd ---
        if self._settings.build_cmd is None:
            for scan_dir in dirs_to_scan:
                pkg_json = os.path.join(scan_dir, "package.json")
                if os.path.exists(pkg_json):
                    try:
                        with open(pkg_json, encoding="utf-8") as fh:
                            data = json.load(fh)
                        if data.get("scripts", {}).get("build"):
                            self._settings.build_cmd = "npm run build"
                            logger.info("Auto-detected build_cmd: %s", self._settings.build_cmd)
                            break
                    except (json.JSONDecodeError, OSError):
                        logger.debug("Failed to read package.json for build_cmd auto-detection")

        # --- test_cmd ---
        if self._settings.test_cmd is None:
            for scan_dir in dirs_to_scan:
                pyproject = os.path.join(scan_dir, "pyproject.toml")
                if os.path.exists(pyproject):
                    try:
                        with open(pyproject, encoding="utf-8") as fh:
                            content = fh.read()
                        if "[tool.pytest]" in content or "[tool.pytest.ini_options]" in content:
                            self._settings.test_cmd = "python -m pytest"
                            logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                            break
                    except OSError:
                        logger.debug("Failed to read pyproject.toml for test_cmd auto-detection")

        if self._settings.test_cmd is None:
            for scan_dir in dirs_to_scan:
                makefile = os.path.join(scan_dir, "Makefile")
                if os.path.exists(makefile):
                    try:
                        with open(makefile, encoding="utf-8") as fh:
                            content = fh.read()
                        if re.search(r"^test[:\s]", content, re.MULTILINE):
                            self._settings.test_cmd = "make test"
                            logger.info("Auto-detected test_cmd: %s", self._settings.test_cmd)
                            break
                    except OSError:
                        logger.debug("Failed to read Makefile for test_cmd auto-detection")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest forge/core/daemon_test.py::test_preflight_checks_multi_repo -xvs`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add forge/core/daemon.py forge/core/daemon_test.py
git commit -m "fix(daemon): preflight checks iterate repo paths for multi-repo

_preflight_checks now runs git validation on each repo path instead of
project_dir. _auto_detect_commands scans all repo paths for config files."
```

---

### Task 3: Fix pipeline branch creation for multi-repo

**Files:**
- Modify: `forge/core/daemon.py:1112-1179`

- [ ] **Step 1: Write failing test for pipeline branch creation in super-repo**

Add to `forge/core/daemon_test.py`:

```python
@pytest.mark.asyncio
async def test_pipeline_branch_created_per_repo(tmp_path):
    """Pipeline branches are created in each repo, not in the wrapper dir."""
    import subprocess

    from forge.core.models import RepoConfig

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    repos = []
    for name in ("backend", "frontend"):
        repo_dir = tmp_path / name
        repo_dir.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo_dir), capture_output=True)
        subprocess.run(
            ["git", "commit", "--allow-empty", "-m", "init"],
            cwd=str(repo_dir),
            capture_output=True,
            env=git_env,
        )
        repos.append(RepoConfig(id=name, path=str(repo_dir), base_branch="main"))

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon

    daemon = ForgeDaemon(str(tmp_path), settings=ForgeSettings(), repos=repos)

    # Call _create_pipeline_branches
    daemon._setup_per_repo_infra("forge/test-pipeline")
    await daemon._create_pipeline_branches()

    # Verify branch exists in each repo
    for repo in repos:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "forge/test-pipeline"],
            cwd=repo.path,
            capture_output=True,
        )
        assert result.returncode == 0, f"Pipeline branch missing in {repo.id}"
```

- [ ] **Step 2: Run test to verify it passes (this should already work)**

Run: `python -m pytest forge/core/daemon_test.py::test_pipeline_branch_created_per_repo -xvs`
Expected: PASS (the `_create_pipeline_branches` method already iterates repos).

- [ ] **Step 3: Fix the single-repo branch creation block to use repo path**

In `forge/core/daemon.py`, the block at lines 1112-1170 resolves `base_branch` and creates the pipeline branch. The problem: it uses `self._project_dir` for `git branch -f` and `_get_current_branch`. For multi-repo, we must use a repo path.

Replace lines 1112-1179 with:

```python
        # On resume/retry, use the stored base branch from the original run.
        # Re-detecting via _get_current_branch would pick up whatever the user
        # has checked out NOW, which may be different from the original base.
        # Use the base branch stored by the TUI (user's explicit choice).
        # Fall back to detecting the current checkout only if not stored.
        #
        # For multi-repo: detect from the first repo's current branch.
        # For single-repo: detect from project_dir.
        _first_repo_path = next(iter(self._repos.values())).path
        base_branch = getattr(pipeline_record, "base_branch", None) or await _get_current_branch(
            _first_repo_path
        )
        custom_branch = getattr(pipeline_record, "branch_name", None) if pipeline_record else None
        if custom_branch and custom_branch.strip():
            pipeline_branch = custom_branch.strip()
        else:
            description = pipeline_record.description if pipeline_record else ""
            pipeline_branch = (
                (await _generate_branch_name(description))
                if description
                else f"forge/pipeline-{pid[:8]}"
            )
        # Persist the final computed branch name so the PR creation endpoint can use it
        await db.set_pipeline_branch_name(pid, pipeline_branch)
        # Notify TUI so diff views can resolve the branch immediately
        await self._emit(
            "pipeline:branch_resolved", {"branch": pipeline_branch}, db=db, pipeline_id=pid
        )

        # Set up per-repo infrastructure (worktree managers, merge workers, pipeline branches)
        self._setup_per_repo_infra(pipeline_branch)

        # Create pipeline branches in all repos.
        # For multi-repo, each repo gets its own pipeline branch.
        # For single-repo, this creates the branch in project_dir.
        # _create_pipeline_branches is idempotent (git branch -f).
        if not resume:
            await self._create_pipeline_branches()
            await db.set_pipeline_base_branch(pid, base_branch)
        else:
            # Verify the branch still exists in at least one repo
            branch_exists = False
            for rc in self._repos.values():
                branch_check = await async_subprocess(
                    ["git", "rev-parse", "--verify", pipeline_branch],
                    cwd=rc.path,
                )
                if branch_check.returncode == 0:
                    branch_exists = True
                    break

            if not branch_exists:
                console.print(
                    f"[yellow]Pipeline branch {pipeline_branch} missing — recreating from {base_branch}[/yellow]"
                )
                await self._create_pipeline_branches()

        console.print(f"[dim]Merge target: {pipeline_branch} (base: {base_branch})[/dim]")

        # Create pipeline branches in all repos (multi-repo)
        # Already handled above — remove the old conditional block.
```

This replaces lines 1112-1179 entirely. Key changes:
- `_get_current_branch` uses `_first_repo_path` instead of `self._project_dir`
- The old `git branch -f ... cwd=self._project_dir` block is gone
- `_create_pipeline_branches()` now always runs (handles both single and multi-repo)
- Resume verification checks actual repo paths instead of `self._project_dir`
- The old `if len(self._repos) > 1 and not resume:` conditional at line 1178 is removed (redundant now)
- Also remove the duplicate comment block about "Create pipeline branches" at lines 1175-1179

- [ ] **Step 4: Run existing daemon tests to check for regressions**

Run: `python -m pytest forge/core/daemon_test.py -xvs`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon.py forge/core/daemon_test.py
git commit -m "fix(daemon): pipeline branch creation uses repo paths, not project_dir

Removed the single-repo git branch -f block that used project_dir.
_create_pipeline_branches() now always runs for all repos. Resume
verification checks repo paths instead of project_dir."
```

---

### Task 4: Fix integration health checks for multi-repo

**Files:**
- Modify: `forge/core/integration.py:138` (_temp_health_worktree)
- Modify: `forge/core/daemon_executor.py:1860-1865` (run_post_merge_check call)
- Modify: `forge/core/daemon.py:1218-1221` (capture_baseline call)
- Modify: `forge/core/daemon.py:1306-1309` (run_final_gate call)

The integration functions already accept a `project_dir` parameter — they just need to receive a repo path instead of the workspace dir.

- [ ] **Step 1: Fix `capture_baseline` call in daemon.py**

In `forge/core/daemon.py`, around line 1218-1221, `capture_baseline` is called with `self._project_dir`. For multi-repo, integration checks should run in the first repo (or a user-specified repo — but for now, first repo is the safe default since integration checks are disabled by default and most users configure them for a single primary repo).

Change:

```python
            baseline_exit = await capture_baseline(
                baseline_cfg,
                self._project_dir,
                base_branch,
            )
```

to:

```python
            # Use first repo path for integration checks (integration commands
            # run in a worktree of a real git repo, not the workspace wrapper).
            _integration_repo_path = next(iter(self._repos.values())).path
            baseline_exit = await capture_baseline(
                baseline_cfg,
                _integration_repo_path,
                base_branch,
            )
```

- [ ] **Step 2: Fix `run_post_merge_check` call in daemon_executor.py**

In `forge/core/daemon_executor.py`, around line 1860-1862, change:

```python
                check_result = await run_post_merge_check(
                    integration_config.post_merge,
                    self._project_dir,
                    actual_pb,
```

to:

```python
                # Use the repo path for this task's repo, not the workspace dir
                _pm_repo_path = self._repos.get(
                    repo_id, next(iter(self._repos.values()))
                ).path
                check_result = await run_post_merge_check(
                    integration_config.post_merge,
                    _pm_repo_path,
                    actual_pb,
```

(The `repo_id` variable is already in scope in `_execute_task`.)

- [ ] **Step 3: Fix `run_final_gate` call in daemon.py**

In `forge/core/daemon.py`, around line 1306-1309, change:

```python
            fg_result = await run_final_gate(
                self._integration_config.final_gate,
                self._project_dir,
                pipeline_branch,
            )
```

to:

```python
            _fg_repo_path = next(iter(self._repos.values())).path
            fg_result = await run_final_gate(
                self._integration_config.final_gate,
                _fg_repo_path,
                pipeline_branch,
            )
```

- [ ] **Step 4: Run integration tests**

Run: `python -m pytest forge/core/integration_test.py -xvs`
Expected: ALL PASS (integration tests use their own `git_repo` fixture, not affected).

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon.py forge/core/daemon_executor.py
git commit -m "fix(integration): health checks use repo path instead of project_dir

capture_baseline, run_post_merge_check, and run_final_gate now receive
the actual repo path instead of the workspace wrapper directory."
```

---

### Task 5: Fix `forge clean` for super-repo

**Files:**
- Modify: `forge/cli/clean.py:25-55` (_remove_worktrees, _prune_worktrees, _list_forge_branches)

- [ ] **Step 1: Understand the current behavior**

`forge clean` already has `_discover_repo_paths()` which correctly finds sub-repo paths. The issue is that `_remove_worktrees`, `_prune_worktrees`, and `_delete_orphaned_branches` are called with each `repo_path` — if the top-level dir is included but is not a git repo, the git commands fail silently (wrapped in try/except). This is actually safe but noisy.

- [ ] **Step 2: Add git-repo guard to `_remove_worktrees`, `_prune_worktrees`, `_delete_orphaned_branches`**

In `forge/cli/clean.py`, add a helper and guard each function:

```python
def _is_git_repo(path: str) -> bool:
    """Check if a path is a valid git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=path,
            capture_output=True,
        )
        return result.returncode == 0
    except Exception:
        return False
```

Then in `_remove_worktrees` (line 25), add guard after getting names:

```python
def _remove_worktrees(project_dir: str, worktrees_dir: str) -> list[str]:
    """Remove all worktree directories under worktrees_dir. Returns names removed."""
    names = _list_worktree_dirs(worktrees_dir)
    if not names:
        return []
    if not _is_git_repo(project_dir):
        # Not a git repo — remove directories manually instead of git worktree remove
        import shutil as _shutil

        removed = []
        for name in names:
            wt_path = os.path.join(worktrees_dir, name)
            try:
                _shutil.rmtree(wt_path)
                removed.append(name)
            except OSError:
                pass
        return removed
    # ... existing git worktree remove logic ...
```

In `_prune_worktrees` (line 45), add guard:

```python
def _prune_worktrees(project_dir: str) -> None:
    """Run git worktree prune to clean up stale worktree admin files."""
    if not _is_git_repo(project_dir):
        return  # Nothing to prune if not a git repo
    # ... existing logic ...
```

In `_list_forge_branches` (line 58), add guard:

```python
def _list_forge_branches(project_dir: str) -> list[str]:
    """Return all local branch names matching 'forge/*' pattern."""
    if not _is_git_repo(project_dir):
        return []  # No branches in a non-git directory
    # ... existing logic ...
```

- [ ] **Step 3: Run clean tests (if any) or manual verification**

Run: `python -m pytest forge/cli/ -xvs -k clean`
Expected: PASS (or no tests found — that's OK, clean is mostly manual).

- [ ] **Step 4: Commit**

```bash
git add forge/cli/clean.py
git commit -m "fix(clean): skip git operations on non-git wrapper directories

Adds _is_git_repo guard. For non-git super-repo wrappers, removes
worktree dirs with shutil.rmtree instead of git worktree remove, and
skips prune/branch listing."
```

---

### Task 6: Full integration test — end-to-end super-repo pipeline

**Files:**
- Modify: `forge/core/preflight_test.py` (add comprehensive E2E test)

- [ ] **Step 1: Write a comprehensive integration test**

Add to `forge/core/preflight_test.py`:

```python
@pytest.mark.asyncio
async def test_super_repo_single_repo_no_regression(tmp_path):
    """Single-repo mode still works exactly as before (no regression)."""
    import subprocess

    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "test",
        "GIT_COMMITTER_NAME": "test",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    # Normal single repo
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "init"],
        cwd=str(tmp_path),
        capture_output=True,
        env=git_env,
    )

    # No repos dict — single-repo mode
    report = await run_preflight(str(tmp_path), base_branch="main")
    assert report.passed, f"Single-repo preflight should pass: {report.summary()}"

    # With a single "default" repo dict — should also work
    from forge.core.models import RepoConfig

    repos = {"default": RepoConfig(id="default", path=str(tmp_path), base_branch="main")}
    report = await run_preflight(str(tmp_path), repos=repos)
    assert report.passed, f"Single default repo preflight should pass: {report.summary()}"
```

- [ ] **Step 2: Run all tests across affected modules**

Run: `python -m pytest forge/core/preflight_test.py forge/core/daemon_test.py forge/core/integration_test.py forge/cli/ -xvs`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add forge/core/preflight_test.py
git commit -m "test: add single-repo regression test for super-repo changes"
```

---

### Task 7: Grep audit — verify no remaining project_dir git assumptions

- [ ] **Step 1: Grep the entire codebase for git commands using project_dir**

Run: `grep -rn "cwd=self._project_dir" forge/core/daemon.py forge/core/daemon_executor.py | grep -i git`

Verify every remaining `cwd=self._project_dir` with a git command is either:
1. In code that only runs for single-repo (where project_dir IS the repo), or
2. Fixed by this plan

- [ ] **Step 2: Grep for the `len(repos) > 1` pattern**

Run: `grep -rn "len(repos) > 1\|len(self._repos) > 1" forge/`

Verify that all remaining `> 1` checks are correct. The only legitimate `> 1` checks should be for UI display differences (e.g., "Create PR" vs "Create PRs"), not for git operations.

- [ ] **Step 3: Fix any remaining issues found**

If the grep reveals additional `cwd=self._project_dir` git operations that would fail in super-repo, fix them following the same pattern: use `rc.path` from the repos dict.

- [ ] **Step 4: Final commit (if needed)**

```bash
git add -u
git commit -m "fix: remaining project_dir git assumptions caught by audit"
```

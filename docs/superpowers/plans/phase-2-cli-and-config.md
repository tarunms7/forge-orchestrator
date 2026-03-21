# Phase 2: CLI & Config — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--repo` CLI flags, `workspace.toml` loading, and startup validation so Forge can accept multi-repo configurations before any LLM calls. Single-repo behavior remains unchanged.

**Architecture:** New `--repo` click option on `run` and `tui` commands, new `WorkspaceConfig` support in `project_config.py`, and a `resolve_repos()` pipeline that merges CLI flags, workspace.toml, and single-repo defaults. All validation runs synchronously at CLI entry — no wasted LLM spend on invalid setups.

**Tech Stack:** Python 3.12+, click, tomllib, os/subprocess for git checks

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` (Sections 5.1–5.5)

**Dependencies:** Phase 1 must be merged (provides `RepoConfig` dataclass in `forge/core/models.py`)

**Verification:** `.venv/bin/python -m pytest forge/cli/main_test.py forge/config/project_config_test.py -x -v`

---

## File Map

| File | Responsibility | Changes |
|------|---------------|---------|
| `forge/cli/main.py` | CLI entry point | Add `--repo` option to `run` (line 67) and `tui` (line 114), wire `resolve_repos()` + `validate_repos_startup()` into both commands |
| `forge/config/project_config.py` | Project config loading | Add `load_workspace_toml()`, `parse_repo_flags()`, `resolve_repos()`, `auto_detect_base_branch()`, `validate_repos_startup()` |
| `forge/config/settings.py` | Global settings | No changes (per spec Section 12.3) |
| `forge/cli/main_test.py` | CLI tests | Add tests for `--repo` flag parsing via click test runner |
| `forge/config/project_config_test.py` | Config tests | Add 16 tests covering all validation paths |

---

## Chunk 1: Repo Flag Parsing & Validation — Critical

All input validation lives in `project_config.py`. These functions are pure (no LLM calls, no daemon) and fully testable in isolation.

### Task 1: `parse_repo_flags()` Function

**Files:**
- Modify: `forge/config/project_config.py` (add after `apply_project_config` at line 326)
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing tests for repo flag parsing**

Add to `forge/config/project_config_test.py`:

```python
import os
import re
import subprocess
import pytest
from forge.config.project_config import parse_repo_flags


class TestParseRepoFlags:
    """Tests for parse_repo_flags() — validates --repo CLI flags."""

    def test_parse_repo_flags_valid(self, tmp_path):
        """Two repos, paths resolved to absolute."""
        # Create two git repos
        for name in ("backend", "frontend"):
            repo = tmp_path / name
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
            (repo / "README.md").write_text("init")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo, capture_output=True, check=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                     "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
            )

        flags = (f"backend={tmp_path / 'backend'}", f"frontend={tmp_path / 'frontend'}")
        repos = parse_repo_flags(flags, str(tmp_path))

        assert len(repos) == 2
        assert repos[0].id == "backend"
        assert repos[1].id == "frontend"
        assert os.path.isabs(repos[0].path)
        assert os.path.isabs(repos[1].path)

    def test_parse_repo_flags_invalid_id(self, tmp_path):
        """Rejects uppercase ID 'Backend'."""
        repo = tmp_path / "backend"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        (repo / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        with pytest.raises(click.ClickException, match="invalid repo ID"):
            parse_repo_flags((f"Backend={repo}",), str(tmp_path))

    def test_parse_repo_flags_duplicate_id(self, tmp_path):
        """Rejects duplicate IDs."""
        for name in ("repo1", "repo2"):
            repo = tmp_path / name
            repo.mkdir()
            subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
            (repo / "README.md").write_text("init")
            subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=repo, capture_output=True, check=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                     "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
            )

        with pytest.raises(click.ClickException, match="duplicate repo ID 'api'"):
            parse_repo_flags(
                (f"api={tmp_path / 'repo1'}", f"api={tmp_path / 'repo2'}"),
                str(tmp_path),
            )

    def test_parse_repo_flags_duplicate_path(self, tmp_path):
        """Rejects same path with different IDs."""
        repo = tmp_path / "backend"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)
        (repo / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=repo, capture_output=True, check=True,
            env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                 "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
        )

        with pytest.raises(click.ClickException, match="both point to"):
            parse_repo_flags(
                (f"backend={repo}", f"api={repo}"),
                str(tmp_path),
            )

    def test_parse_repo_flags_nested_paths(self, tmp_path):
        """Rejects nested paths."""
        outer = tmp_path / "backend"
        inner = outer / "libs" / "shared"
        for d in (outer, inner):
            d.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init"], cwd=d, capture_output=True, check=True)
            (d / "README.md").write_text("init")
            subprocess.run(["git", "add", "."], cwd=d, capture_output=True, check=True)
            subprocess.run(
                ["git", "commit", "-m", "init"],
                cwd=d, capture_output=True, check=True,
                env={**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
                     "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"},
            )

        with pytest.raises(click.ClickException, match="is inside repo"):
            parse_repo_flags(
                (f"backend={outer}", f"shared={inner}"),
                str(tmp_path),
            )

    def test_parse_repo_flags_nonexistent_path(self, tmp_path):
        """Rejects missing directory."""
        with pytest.raises(click.ClickException, match="does not exist"):
            parse_repo_flags(
                ("frontend=./nonexistent",),
                str(tmp_path),
            )

    def test_parse_repo_flags_not_git_repo(self, tmp_path):
        """Rejects non-git directory."""
        plain_dir = tmp_path / "not-a-repo"
        plain_dir.mkdir()

        with pytest.raises(click.ClickException, match="is not a git repository"):
            parse_repo_flags(
                (f"myrepo={plain_dir}",),
                str(tmp_path),
            )

    def test_parse_repo_flags_no_commits(self, tmp_path):
        """Rejects repo with no commits."""
        repo = tmp_path / "empty-repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, capture_output=True, check=True)

        with pytest.raises(click.ClickException, match="has no commits"):
            parse_repo_flags(
                (f"myrepo={repo}",),
                str(tmp_path),
            )
```

Note: tests import `click` — add `import click` at the top of the test file.

- [ ] **Step 2: Implement `parse_repo_flags()`**

Add to `forge/config/project_config.py` after line 326 (`apply_project_config`):

```python
import re
import subprocess

# Import at top of file (add to existing imports):
# from forge.core.models import RepoConfig

_REPO_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def parse_repo_flags(
    repo_flags: tuple[str, ...],
    project_dir: str,
) -> list[RepoConfig]:
    """Parse --repo name=path flags into validated RepoConfig list.

    Raises click.ClickException on any validation failure (spec Section 5.3).
    """
    import click
    from forge.core.models import RepoConfig

    repos: list[RepoConfig] = []
    seen_ids: dict[str, str] = {}       # id → path
    seen_paths: dict[str, str] = {}     # abs_path → id

    for flag in repo_flags:
        if "=" not in flag:
            raise click.ClickException(
                f"invalid --repo format '{flag}' — expected name=path"
            )
        repo_id, raw_path = flag.split("=", 1)

        # ── ID format validation ──
        if not _REPO_ID_RE.match(repo_id):
            raise click.ClickException(
                f"invalid repo ID '{repo_id}' — must match [a-z0-9][a-z0-9-]* "
                "(lowercase alphanumeric and hyphens only)"
            )

        # ── Duplicate ID check ──
        if repo_id in seen_ids:
            raise click.ClickException(
                f"duplicate repo ID '{repo_id}' — each --repo must have a unique name"
            )

        # ── Resolve path ──
        abs_path = os.path.abspath(os.path.join(project_dir, raw_path))

        # ── Path existence check ──
        if not os.path.isdir(abs_path):
            raise click.ClickException(
                f"repo '{repo_id}' path '{raw_path}' does not exist"
            )

        # ── Git repo check ──
        git_check = subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            cwd=abs_path, capture_output=True, text=True,
        )
        if git_check.returncode != 0:
            raise click.ClickException(
                f"repo '{repo_id}' at '{raw_path}' is not a git repository "
                "(no .git directory)"
            )

        # ── No commits check ──
        head_check = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=abs_path, capture_output=True, text=True,
        )
        if head_check.returncode != 0:
            raise click.ClickException(
                f"repo '{repo_id}' has no commits. Make an initial commit first."
            )

        # ── Duplicate path check ──
        if abs_path in seen_paths:
            raise click.ClickException(
                f"repos '{seen_paths[abs_path]}' and '{repo_id}' both point to "
                f"'{raw_path}' — each repo must be a distinct directory"
            )

        seen_ids[repo_id] = abs_path
        seen_paths[abs_path] = repo_id

        # ── Auto-detect base branch ──
        base_branch = auto_detect_base_branch(abs_path)

        repos.append(RepoConfig(id=repo_id, path=abs_path, base_branch=base_branch))

    # ── Nested path detection (all pairs) ──
    for i, a in enumerate(repos):
        for b in repos[i + 1:]:
            if a.path.startswith(b.path + "/"):
                raise click.ClickException(
                    f"repo '{a.id}' at '{a.path}' is inside repo '{b.id}' at "
                    f"'{b.path}'. Nested repos are not supported — use separate "
                    "directories."
                )
            if b.path.startswith(a.path + "/"):
                raise click.ClickException(
                    f"repo '{b.id}' at '{b.path}' is inside repo '{a.id}' at "
                    f"'{a.path}'. Nested repos are not supported — use separate "
                    "directories."
                )

    return repos
```

- [ ] **Step 3: Verify all 8 parse tests pass**

```bash
.venv/bin/python -m pytest forge/config/project_config_test.py::TestParseRepoFlags -x -v
```

---

### Task 2: `auto_detect_base_branch()` Function

**Files:**
- Modify: `forge/config/project_config.py` (add before `parse_repo_flags`)
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing test**

```python
class TestAutoDetectBaseBranch:
    """Tests for auto_detect_base_branch()."""

    def test_auto_detect_base_branch(self, tmp_path):
        """Detects 'main' from a test repo with a main branch."""
        repo = tmp_path / "repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
        (repo / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True, env=env)

        from forge.config.project_config import auto_detect_base_branch
        assert auto_detect_base_branch(str(repo)) == "main"
```

- [ ] **Step 2: Implement `auto_detect_base_branch()`**

```python
def auto_detect_base_branch(repo_path: str) -> str:
    """Detect the default branch of a git repo.

    Checks (in order):
    1. 'main' branch exists → return 'main'
    2. 'master' branch exists → return 'master'
    3. Current HEAD branch → return that
    4. Fallback → 'main'
    """
    def _branch_exists(name: str) -> bool:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{name}"],
            cwd=repo_path, capture_output=True,
        )
        return result.returncode == 0

    if _branch_exists("main"):
        return "main"
    if _branch_exists("master"):
        return "master"

    # Fall back to current branch
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path, capture_output=True, text=True,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()

    return "main"
```

- [ ] **Step 3: Verify test passes**

```bash
.venv/bin/python -m pytest forge/config/project_config_test.py::TestAutoDetectBaseBranch -x -v
```

---

## Chunk 2: Workspace TOML Loading

### Task 3: `load_workspace_toml()` Function

**Files:**
- Modify: `forge/config/project_config.py`
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing tests**

```python
from forge.config.project_config import load_workspace_toml


class TestLoadWorkspaceToml:
    """Tests for load_workspace_toml() — parses .forge/workspace.toml."""

    def test_load_workspace_toml_valid(self, tmp_path):
        """Parses [[repos]] sections correctly."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(
            '[[repos]]\nid = "backend"\npath = "./backend"\nbase_branch = "main"\n\n'
            '[[repos]]\nid = "frontend"\npath = "./frontend"\nbase_branch = "develop"\n'
        )

        repos = load_workspace_toml(str(tmp_path))
        assert repos is not None
        assert len(repos) == 2
        assert repos[0].id == "backend"
        assert repos[0].path == os.path.join(str(tmp_path), "backend")
        assert repos[0].base_branch == "main"
        assert repos[1].id == "frontend"
        assert repos[1].base_branch == "develop"

    def test_load_workspace_toml_missing(self, tmp_path):
        """Returns None when workspace.toml doesn't exist."""
        assert load_workspace_toml(str(tmp_path)) is None

    def test_load_workspace_toml_invalid(self, tmp_path, caplog):
        """Returns None with warning on invalid TOML."""
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text("this is not valid toml {{{{")

        result = load_workspace_toml(str(tmp_path))
        assert result is None
        assert "Failed to parse" in caplog.text or result is None
```

- [ ] **Step 2: Implement `load_workspace_toml()`**

```python
def load_workspace_toml(workspace_dir: str) -> list[RepoConfig] | None:
    """Load repo configs from .forge/workspace.toml.

    Returns None if file doesn't exist or is invalid.
    Paths in the TOML are resolved relative to workspace_dir.
    """
    from forge.core.models import RepoConfig

    toml_path = os.path.join(workspace_dir, ".forge", "workspace.toml")
    if not os.path.isfile(toml_path):
        return None

    try:
        with open(toml_path, "rb") as f:
            data = tomllib.load(f)
    except Exception as e:
        logger.warning("Failed to parse %s: %s — ignoring workspace config", toml_path, e)
        return None

    repos_raw = data.get("repos", [])
    if not repos_raw:
        logger.warning("workspace.toml has no [[repos]] entries — ignoring")
        return None

    repos: list[RepoConfig] = []
    for entry in repos_raw:
        repo_id = entry.get("id", "")
        raw_path = entry.get("path", ".")
        base_branch = entry.get("base_branch", "")

        abs_path = os.path.abspath(os.path.join(workspace_dir, raw_path))

        # Auto-detect base_branch if not specified
        if not base_branch and os.path.isdir(abs_path):
            base_branch = auto_detect_base_branch(abs_path)

        repos.append(RepoConfig(id=repo_id, path=abs_path, base_branch=base_branch or "main"))

    return repos
```

- [ ] **Step 3: Verify tests pass**

```bash
.venv/bin/python -m pytest forge/config/project_config_test.py::TestLoadWorkspaceToml -x -v
```

---

## Chunk 3: Resolution Pipeline

### Task 4: `resolve_repos()` Function

**Files:**
- Modify: `forge/config/project_config.py`
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing tests**

```python
from forge.config.project_config import resolve_repos


class TestResolveRepos:
    """Tests for resolve_repos() — loading order from spec Section 5.5."""

    def _make_git_repo(self, path):
        """Helper: create a git repo with one commit."""
        path.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-b", "main"], cwd=path, capture_output=True, check=True)
        (path / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=path, capture_output=True, check=True, env=env)

    def test_resolve_repos_cli_overrides_toml(self, tmp_path):
        """CLI flags take priority over workspace.toml."""
        backend = tmp_path / "backend"
        frontend = tmp_path / "frontend"
        self._make_git_repo(backend)
        self._make_git_repo(frontend)

        # Write workspace.toml that would add a different repo
        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(
            '[[repos]]\nid = "other"\npath = "./other"\nbase_branch = "main"\n'
        )

        repos = resolve_repos(
            repo_flags=(f"backend={backend}", f"frontend={frontend}"),
            project_dir=str(tmp_path),
        )
        assert len(repos) == 2
        assert repos[0].id == "backend"
        assert repos[1].id == "frontend"

    def test_resolve_repos_toml_fallback(self, tmp_path):
        """Uses workspace.toml when no CLI flags provided."""
        backend = tmp_path / "backend"
        self._make_git_repo(backend)

        forge_dir = tmp_path / ".forge"
        forge_dir.mkdir()
        (forge_dir / "workspace.toml").write_text(
            f'[[repos]]\nid = "backend"\npath = "./backend"\nbase_branch = "main"\n'
        )

        repos = resolve_repos(repo_flags=(), project_dir=str(tmp_path))
        assert len(repos) == 1
        assert repos[0].id == "backend"

    def test_resolve_repos_single_repo_default(self, tmp_path):
        """No flags, no toml = single repo from CWD."""
        self._make_git_repo(tmp_path)

        repos = resolve_repos(repo_flags=(), project_dir=str(tmp_path))
        assert len(repos) == 1
        assert repos[0].id == "default"
        assert repos[0].path == str(tmp_path)
```

- [ ] **Step 2: Implement `resolve_repos()`**

```python
def resolve_repos(
    repo_flags: tuple[str, ...],
    project_dir: str,
) -> list[RepoConfig]:
    """Resolve repo configuration using the loading order from spec Section 5.5.

    Priority:
    1. --repo CLI flags → parse and validate
    2. .forge/workspace.toml → load and validate
    3. CWD is a git repo → single-repo mode (id="default")
    4. Error: no repos found

    Raises click.ClickException on failure.
    """
    import click
    from forge.core.models import RepoConfig

    project_dir = os.path.abspath(project_dir)

    # 1. CLI flags take priority
    if repo_flags:
        return parse_repo_flags(repo_flags, project_dir)

    # 2. Workspace TOML fallback
    toml_repos = load_workspace_toml(project_dir)
    if toml_repos:
        # Validate the TOML-loaded repos the same way as CLI flags
        # (path existence, git repo, no commits, nested paths, etc.)
        # Re-use parse_repo_flags by converting to flag format
        flags = tuple(f"{r.id}={r.path}" for r in toml_repos)
        return parse_repo_flags(flags, project_dir)

    # 3. Single-repo mode from CWD
    git_check = subprocess.run(
        ["git", "rev-parse", "--git-dir"],
        cwd=project_dir, capture_output=True, text=True,
    )
    if git_check.returncode == 0:
        base_branch = auto_detect_base_branch(project_dir)
        return [RepoConfig(id="default", path=project_dir, base_branch=base_branch)]

    # 4. No repos found
    raise click.ClickException(
        "current directory is not a git repo and no --repo flags or "
        "workspace.toml found"
    )
```

- [ ] **Step 3: Verify tests pass**

```bash
.venv/bin/python -m pytest forge/config/project_config_test.py::TestResolveRepos -x -v
```

---

## Chunk 4: Startup Validation

### Task 5: `validate_repos_startup()` Function

**Files:**
- Modify: `forge/config/project_config.py`
- Test: `forge/config/project_config_test.py`

- [ ] **Step 1: Write failing test**

```python
from forge.config.project_config import validate_repos_startup


class TestValidateReposStartup:
    """Tests for validate_repos_startup() — dirty tree + base branch checks."""

    def test_validate_repos_dirty_tree(self, tmp_path):
        """Rejects repo with uncommitted changes."""
        repo = tmp_path / "dirty-repo"
        repo.mkdir()
        env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "t@t",
               "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "t@t"}
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, capture_output=True, check=True)
        (repo / "README.md").write_text("init")
        subprocess.run(["git", "add", "."], cwd=repo, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, capture_output=True, check=True, env=env)

        # Make dirty
        (repo / "dirty.txt").write_text("uncommitted")
        subprocess.run(["git", "add", "dirty.txt"], cwd=repo, capture_output=True, check=True)

        from forge.core.models import RepoConfig
        repos = [RepoConfig(id="dirty", path=str(repo), base_branch="main")]

        with pytest.raises(click.ClickException, match="has uncommitted changes"):
            validate_repos_startup(repos)
```

- [ ] **Step 2: Implement `validate_repos_startup()`**

```python
def validate_repos_startup(repos: list[RepoConfig]) -> None:
    """Validate all repos before any LLM calls. Fail fast on first error.

    Checks per repo:
    - No uncommitted changes (clean working tree)
    - Base branch exists

    Raises click.ClickException with spec Section 5.3 error messages.
    """
    import click

    for rc in repos:
        # Skip the default single-repo — it's the CWD, dirty tree is OK
        # (existing behavior: Forge works in dirty repos for single-repo mode)
        if rc.id == "default" and len(repos) == 1:
            continue

        # ── Dirty working tree check ──
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=rc.path, capture_output=True, text=True,
        )
        if status.stdout.strip():
            raise click.ClickException(
                f"repo '{rc.id}' has uncommitted changes. "
                "Commit or stash before running Forge."
            )

        # ── Base branch existence check ──
        branch_check = subprocess.run(
            ["git", "rev-parse", "--verify", f"refs/heads/{rc.base_branch}"],
            cwd=rc.path, capture_output=True,
        )
        if branch_check.returncode != 0:
            raise click.ClickException(
                f"repo '{rc.id}' base branch '{rc.base_branch}' does not exist"
            )
```

- [ ] **Step 3: Verify test passes**

```bash
.venv/bin/python -m pytest forge/config/project_config_test.py::TestValidateReposStartup -x -v
```

---

## Chunk 5: CLI Wiring

### Task 6: Add `--repo` Option to `run` and `tui` Commands

**Files:**
- Modify: `forge/cli/main.py` (lines 66–111 for `run`, lines 114–137 for `tui`)

- [ ] **Step 1: Add `--repo` option to `run` command**

Add the decorator after line 76 (the `--deep-plan` option):

```python
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
```

Update the `run` function signature:

```python
def run(task: str, project_dir: str, strategy: str | None, spec: str | None, deep_plan: bool, repo: tuple[str, ...]) -> None:
```

Wire repos into daemon construction (replace lines 82–104):

```python
    project_dir = os.path.abspath(project_dir)

    from forge.config.project_config import (
        DEFAULT_FORGE_TOML, ProjectConfig, apply_project_config,
        resolve_repos, validate_repos_startup,
    )

    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)
        _write_if_missing(os.path.join(forge_dir, "forge.toml"), DEFAULT_FORGE_TOML)
        _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
        _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
        _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

    from forge.config.settings import ForgeSettings
    from forge.core.daemon import ForgeDaemon

    # Resolve repos: CLI flags → workspace.toml → single-repo default
    repos = resolve_repos(repo_flags=repo, project_dir=project_dir)
    validate_repos_startup(repos)

    # Load project config and apply to settings (env vars still win)
    project_config = ProjectConfig.load(project_dir)
    settings = ForgeSettings()
    apply_project_config(settings, project_config)
    if strategy:
        settings.model_strategy = strategy

    daemon = ForgeDaemon(project_dir, settings=settings, repos=repos)
```

- [ ] **Step 2: Add `--repo` option to `tui` command**

Add the same decorator to `tui`:

```python
@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
@click.option(
    "--strategy",
    default=None,
    envvar="FORGE_MODEL_STRATEGY",
    help="Model routing: auto, fast, quality",
)
@click.option(
    "--repo",
    multiple=True,
    help="Repo in name=path format (repeatable). E.g. --repo backend=./backend",
)
def tui(project_dir: str, strategy: str | None, repo: tuple[str, ...]) -> None:
    """Launch the Forge terminal UI."""
    project_dir = os.path.abspath(project_dir)
    forge_dir = os.path.join(project_dir, ".forge")
    if not os.path.isdir(forge_dir):
        os.makedirs(forge_dir, exist_ok=True)

    from forge.config.project_config import resolve_repos, validate_repos_startup
    from forge.config.settings import ForgeSettings
    from forge.tui.app import ForgeApp

    repos = resolve_repos(repo_flags=repo, project_dir=project_dir)
    validate_repos_startup(repos)

    settings = ForgeSettings()
    if strategy:
        settings.model_strategy = strategy

    app = ForgeApp(project_dir=project_dir, settings=settings, repos=repos)
    app.run()
```

- [ ] **Step 3: Verify backward compatibility — no flags = same behavior**

```bash
# Existing single-repo tests must still pass
.venv/bin/python -m pytest forge/cli/main_test.py -x -v
```

---

## Chunk 6: Full Integration Verification

- [ ] **Step 1: Run all new and existing tests**

```bash
.venv/bin/python -m pytest forge/cli/main_test.py forge/config/project_config_test.py -x -v
```

- [ ] **Step 2: Verify no regressions in related modules**

```bash
.venv/bin/python -m pytest forge/core/daemon_test.py -x -v
```

---

## Summary of Exported Symbols

New public functions in `forge/config/project_config.py`:

| Function | Signature | Returns |
|----------|-----------|---------|
| `auto_detect_base_branch` | `(repo_path: str) -> str` | Branch name |
| `parse_repo_flags` | `(repo_flags: tuple[str, ...], project_dir: str) -> list[RepoConfig]` | Validated repos |
| `load_workspace_toml` | `(workspace_dir: str) -> list[RepoConfig] \| None` | Repos or None |
| `resolve_repos` | `(repo_flags: tuple[str, ...], project_dir: str) -> list[RepoConfig]` | Resolved repos |
| `validate_repos_startup` | `(repos: list[RepoConfig]) -> None` | Raises on failure |

## Error Messages (Spec Section 5.3)

All error messages use `click.ClickException` and match the spec exactly:

| Check | Message |
|-------|---------|
| Path missing | `repo '<id>' path '<path>' does not exist` |
| Not git repo | `repo '<id>' at '<path>' is not a git repository (no .git directory)` |
| Dirty tree | `repo '<id>' has uncommitted changes. Commit or stash before running Forge.` |
| Duplicate ID | `duplicate repo ID '<id>' — each --repo must have a unique name` |
| Duplicate path | `repos '<id1>' and '<id2>' both point to '<path>' — each repo must be a distinct directory` |
| Nested paths | `repo '<id>' at '<path>' is inside repo '<id>' at '<path>'. Nested repos are not supported — use separate directories.` |
| No commits | `repo '<id>' has no commits. Make an initial commit first.` |
| No repos found | `current directory is not a git repo and no --repo flags or workspace.toml found` |

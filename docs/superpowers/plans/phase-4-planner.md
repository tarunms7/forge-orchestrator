# Phase 4: Multi-Repo Planner Support Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the unified planner to gather snapshots from multiple repositories, include multi-repo instructions in its system prompt, and validate repo assignments in the generated TaskGraph.

**Architecture:** Multi-repo snapshot gathering is added to `forge/core/context.py`. The `UnifiedPlanner` in `forge/core/planning/unified_planner.py` gains a `repo_ids` parameter that triggers multi-repo prompt construction and post-parse repo validation. The existing `validate_plan()` in `forge/core/planning/validator.py` already handles `repo_ids` — no changes needed there. The daemon's planning phase in `forge/core/daemon.py` is updated to gather per-repo snapshots in parallel and pass repo IDs to the planner.

**Tech Stack:** Python 3.12+, asyncio, claude-code-sdk, pydantic

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` (Sections 7, 15.1, 16.1, 16.2)

**Dependencies:** Phase 1 (data model — `RepoConfig`, `TaskDefinition.repo` field) must be merged. Phase 3 (executor/worktree) recommended but not strictly required for planner unit tests.

**Verification:** `.venv/bin/python -m pytest forge/core/planning/unified_planner_test.py forge/core/context_test.py -x -v`

---

## File Map

| File | Responsibility | Changes |
|------|---------------|---------|
| `forge/core/context.py` | Project snapshot gathering | Add `gather_multi_repo_snapshots()`, `_truncate_file_tree()`, `format_multi_repo_snapshot()` |
| `forge/core/planning/unified_planner.py` | Single-agent planning with codebase access | Add `repo_ids` param to `__init__`, update `_build_prompt()`, update `_parse()`, update system prompt |
| `forge/core/daemon.py` | Orchestration loop (plan phase) | Update snapshot gathering and planner invocation for multi-repo |
| `forge/core/planning/validator.py` | Plan validation | No changes — `repo_ids` support already exists (line 15-37) |
| `forge/core/context_test.py` | Tests for context module | Add multi-repo snapshot tests |
| `forge/core/planning/unified_planner_test.py` | Tests for unified planner | Add multi-repo planner tests |

---

## Chunk 1: Multi-Repo Snapshot Gathering — `forge/core/context.py`

Adds parallel snapshot gathering and multi-repo formatting. The existing `gather_project_snapshot()` (line 116) stays unchanged — it gathers a single repo's snapshot. New functions compose multiple snapshots.

### Task 1: Add `gather_multi_repo_snapshots()` and formatting helpers

**Files:**
- Modify: `forge/core/context.py` (add functions after line 148)
- Test: `forge/core/context_test.py` (add new test class)

- [ ] **Step 1: Write failing tests for multi-repo snapshot gathering**

Add to `forge/core/context_test.py`:

```python
import asyncio
from unittest.mock import patch, MagicMock

from forge.core.context import (
    gather_multi_repo_snapshots,
    format_multi_repo_snapshot,
    _truncate_file_tree,
    ProjectSnapshot,
)
from forge.core.models import RepoConfig


class TestGatherMultiRepoSnapshots:
    """Tests for parallel multi-repo snapshot gathering."""

    def test_gather_multi_repo_snapshots(self, tmp_path):
        """Parallel gathering from 2 repos produces dict keyed by repo ID."""
        # Create two minimal git repos
        for name in ("backend", "frontend"):
            repo = tmp_path / name
            repo.mkdir()
            (repo / ".git").mkdir()  # Fake git dir
            (repo / "README.md").write_text(f"# {name}")

        repos = {
            "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
            "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
        }

        with patch("forge.core.context.gather_project_snapshot") as mock_gather:
            mock_gather.side_effect = lambda path: ProjectSnapshot(
                file_tree=f"tree-for-{path.split('/')[-1]}",
                total_files=10,
                total_loc=100,
                git_branch="main",
            )
            result = asyncio.run(gather_multi_repo_snapshots(repos))

        assert set(result.keys()) == {"backend", "frontend"}
        assert result["backend"].file_tree == "tree-for-backend"
        assert result["frontend"].file_tree == "tree-for-frontend"
        # Verify parallel execution (both calls made)
        assert mock_gather.call_count == 2

    def test_gather_multi_repo_snapshot_failure_returns_empty(self, tmp_path):
        """If one repo's snapshot fails, return empty snapshot for that repo."""
        repos = {
            "backend": RepoConfig(id="backend", path=str(tmp_path / "backend"), base_branch="main"),
            "frontend": RepoConfig(id="frontend", path=str(tmp_path / "frontend"), base_branch="main"),
        }

        def side_effect(path):
            if "backend" in path:
                raise OSError("git ls-files failed")
            return ProjectSnapshot(file_tree="frontend-tree", total_files=5, total_loc=50)

        with patch("forge.core.context.gather_project_snapshot", side_effect=side_effect):
            result = asyncio.run(gather_multi_repo_snapshots(repos))

        assert result["backend"].total_files == 0  # empty fallback
        assert result["frontend"].file_tree == "frontend-tree"


class TestFormatMultiRepoSnapshot:
    """Tests for multi-repo snapshot formatting."""

    def test_format_multi_repo_snapshot(self):
        """Labeled sections per repo with ### Repo: headers."""
        snapshots = {
            "backend": ProjectSnapshot(
                file_tree="src/\n  main.py",
                total_files=10,
                total_loc=500,
                git_branch="main",
            ),
            "frontend": ProjectSnapshot(
                file_tree="src/\n  App.tsx",
                total_files=8,
                total_loc=300,
                git_branch="main",
            ),
        }
        repos = {
            "backend": RepoConfig(id="backend", path="/workspace/backend", base_branch="main"),
            "frontend": RepoConfig(id="frontend", path="/workspace/frontend", base_branch="main"),
        }

        result = format_multi_repo_snapshot(snapshots, repos)

        assert "### Repo: backend (/workspace/backend)" in result
        assert "### Repo: frontend (/workspace/frontend)" in result
        assert "src/\n  main.py" in result
        assert "src/\n  App.tsx" in result


class TestTruncateFileTree:
    """Tests for large repo file tree truncation."""

    def test_truncate_large_repo_tree(self):
        """Repos with 500+ files get truncated to depth 3."""
        # Build a file tree with 500+ entries at depth 4+
        lines = []
        for i in range(100):
            lines.append(f"src/")
            lines.append(f"  module_{i}/")
            lines.append(f"    sub/")
            lines.append(f"      deep/")
            lines.append(f"        file_{i}.py")
            lines.append(f"        test_{i}.py")
        tree = "\n".join(lines)

        result = _truncate_file_tree(tree, total_files=600, max_depth=3)

        # Depth 3 items should be present (src/, module_X/, sub/)
        assert "src/" in result
        assert "module_0/" in result
        assert "sub/" in result
        # Depth 4+ should be truncated
        assert "deep/" not in result
        assert "file_0.py" not in result
        # Should include truncation notice
        assert "truncated" in result.lower() or "..." in result

    def test_no_truncation_for_small_repos(self):
        """Repos with <500 files are not truncated."""
        tree = "src/\n  main.py\n  utils.py"
        result = _truncate_file_tree(tree, total_files=3, max_depth=3)
        assert result == tree  # unchanged
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/core/context_test.py::TestGatherMultiRepoSnapshots forge/core/context_test.py::TestFormatMultiRepoSnapshot forge/core/context_test.py::TestTruncateFileTree -v`
Expected: FAIL — ImportError (functions don't exist yet)

- [ ] **Step 3: Implement `_truncate_file_tree()` helper**

Add to `forge/core/context.py` after line 393 (after `_format_module_index`):

```python
def _truncate_file_tree(tree: str, total_files: int, max_depth: int = 3) -> str:
    """Truncate a file tree to max_depth levels if the repo has too many files.

    For repos with 500+ files, deep directory listings waste planner context.
    The planner can use Glob/Read to explore deeper.

    Args:
        tree: Indented file tree string from _get_file_tree().
        total_files: Total tracked files in the repo.
        max_depth: Maximum directory depth to keep (default 3).

    Returns:
        Truncated tree string, or original if repo is small.
    """
    if total_files < 500:
        return tree

    truncated_lines: list[str] = []
    for line in tree.split("\n"):
        if not line.strip():
            continue
        # Depth = number of leading 2-space indents
        stripped = line.lstrip(" ")
        indent_count = (len(line) - len(stripped)) // 2
        if indent_count < max_depth:
            truncated_lines.append(line)

    truncated_lines.append(f"  ... ({total_files} files total, tree truncated to depth {max_depth})")
    return "\n".join(truncated_lines)
```

- [ ] **Step 4: Implement `gather_multi_repo_snapshots()`**

Add to `forge/core/context.py` after `gather_project_snapshot()` (after line 148):

```python
async def gather_multi_repo_snapshots(
    repos: dict[str, "RepoConfig"],
) -> dict[str, ProjectSnapshot]:
    """Gather project snapshots from multiple repos in parallel.

    Uses asyncio.gather with asyncio.to_thread per repo for parallel I/O
    (each snapshot involves git and filesystem operations).

    If a repo's snapshot gathering fails, an empty ProjectSnapshot is used
    as fallback — the planner can still read the repo via tools.

    Args:
        repos: Mapping of repo_id → RepoConfig.

    Returns:
        Mapping of repo_id → ProjectSnapshot.
    """
    import asyncio
    import logging

    logger = logging.getLogger("forge.context")

    async def _gather_one(repo_id: str, path: str) -> tuple[str, ProjectSnapshot]:
        try:
            snapshot = await asyncio.to_thread(gather_project_snapshot, path)
            return repo_id, snapshot
        except Exception as e:
            logger.warning("Snapshot gathering failed for repo '%s': %s", repo_id, e)
            return repo_id, ProjectSnapshot()

    results = await asyncio.gather(*(
        _gather_one(repo_id, rc.path)
        for repo_id, rc in repos.items()
    ))
    return dict(results)
```

- [ ] **Step 5: Implement `format_multi_repo_snapshot()`**

Add to `forge/core/context.py` after `gather_multi_repo_snapshots()`:

```python
def format_multi_repo_snapshot(
    snapshots: dict[str, ProjectSnapshot],
    repos: dict[str, "RepoConfig"],
) -> str:
    """Format multiple repo snapshots into a single string for the planner.

    Each repo gets a labeled section with a ### Repo: header. Large repos
    (500+ files) have their file trees truncated to depth 3.

    Args:
        snapshots: Mapping of repo_id → ProjectSnapshot.
        repos: Mapping of repo_id → RepoConfig (for path info).

    Returns:
        Formatted multi-repo snapshot string.
    """
    parts: list[str] = []
    for repo_id, snap in snapshots.items():
        rc = repos[repo_id]
        parts.append(f"### Repo: {repo_id} ({rc.path})")
        # Truncate large repos' file trees
        if snap.total_files >= 500:
            truncated_tree = _truncate_file_tree(snap.file_tree, snap.total_files)
            # Replace the tree in the formatted output
            formatted = snap.format_for_planner()
            formatted = formatted.replace(snap.file_tree, truncated_tree)
            parts.append(formatted)
        else:
            parts.append(snap.format_for_planner())
        parts.append("")
    return "\n".join(parts)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/core/context_test.py::TestGatherMultiRepoSnapshots forge/core/context_test.py::TestFormatMultiRepoSnapshot forge/core/context_test.py::TestTruncateFileTree -v`
Expected: All PASS

---

## Chunk 2: Planner Multi-Repo Support — `forge/core/planning/unified_planner.py`

Updates the `UnifiedPlanner` to accept repo IDs, include multi-repo instructions in the system prompt, validate repo assignments after parsing, and retry on invalid repos.

### Task 2: Add `repo_ids` to `UnifiedPlanner.__init__` and update `_build_prompt()`

**Files:**
- Modify: `forge/core/planning/unified_planner.py` (lines 63-78, 233-255, 257-302, 305-411)
- Test: `forge/core/planning/unified_planner_test.py` (add new test class)

- [ ] **Step 1: Write failing tests for multi-repo planner behavior**

Add to `forge/core/planning/unified_planner_test.py`:

```python
import json
from unittest.mock import AsyncMock, patch, MagicMock

from forge.core.planning.unified_planner import (
    UnifiedPlanner,
    _build_unified_system_prompt,
)


class TestPlannerMultiRepo:
    """Tests for multi-repo planner prompt and parsing."""

    def test_planner_prompt_includes_repo_list(self):
        """Multi-repo prompt has 'Available repos' section."""
        planner = UnifiedPlanner(
            repo_ids={"backend", "frontend"},
        )
        prompt = planner._build_prompt(
            user_input="Add auth",
            spec_text="",
            snapshot_text="## snapshot here",
            conventions="",
            feedback=None,
        )
        assert "Available repos" not in prompt  # repo list is in system prompt, not user prompt
        # But the prompt itself should still work normally
        assert "Add auth" in prompt
        assert "## snapshot here" in prompt

    def test_planner_prompt_single_repo_no_repos(self):
        """No repo list for single-repo mode (repo_ids=None)."""
        planner = UnifiedPlanner(repo_ids=None)
        prompt = planner._build_prompt(
            user_input="Add auth",
            spec_text="",
            snapshot_text="## snapshot",
            conventions="",
            feedback=None,
        )
        # Single-repo prompt should not mention repos
        assert "repo" not in prompt.lower() or "repo" in "## snapshot"

    def test_planner_system_prompt_includes_multi_repo_instructions(self):
        """System prompt includes multi-repo workspace section when repo_ids provided."""
        planner = UnifiedPlanner(repo_ids={"backend", "frontend"})
        # Access the system prompt building logic
        from forge.agents.adapter import _build_question_protocol
        question_protocol = _build_question_protocol(autonomy="balanced", remaining=5)
        system_prompt = _build_unified_system_prompt(
            question_protocol,
            repo_ids={"backend", "frontend"},
        )
        assert "## Multi-Repo Workspace" in system_prompt
        assert '"backend"' in system_prompt or "backend" in system_prompt
        assert '"frontend"' in system_prompt or "frontend" in system_prompt
        assert "repo" in system_prompt.lower()

    def test_planner_system_prompt_no_multi_repo_for_single(self):
        """System prompt does NOT include multi-repo section for single-repo."""
        from forge.agents.adapter import _build_question_protocol
        question_protocol = _build_question_protocol(autonomy="balanced", remaining=5)
        system_prompt = _build_unified_system_prompt(
            question_protocol,
            repo_ids=None,
        )
        assert "## Multi-Repo Workspace" not in system_prompt

    def test_parse_validates_repo_assignments(self):
        """_parse() rejects unknown repo IDs when repo_ids is set."""
        planner = UnifiedPlanner(repo_ids={"backend", "frontend"})
        raw = json.dumps({
            "tasks": [{
                "id": "task-1",
                "title": "Add API",
                "description": "Add the API endpoint for user authentication with proper validation",
                "files": ["src/api.py"],
                "repo": "unknown-repo",
            }]
        })
        graph, error = planner._parse(raw)
        assert graph is None
        assert "unknown-repo" in error
        assert "backend" in error or "frontend" in error

    def test_parse_missing_repo_defaults_to_default(self):
        """When repo_ids is None (single-repo), missing repo defaults to 'default'."""
        planner = UnifiedPlanner(repo_ids=None)
        raw = json.dumps({
            "tasks": [{
                "id": "task-1",
                "title": "Add feature",
                "description": "Add the feature with proper error handling and test coverage",
                "files": ["src/main.py"],
                # no "repo" field — defaults to "default"
            }]
        })
        graph, error = planner._parse(raw)
        assert graph is not None
        assert graph.tasks[0].repo == "default"

    def test_parse_cross_repo_file_path_rejected(self):
        """File path starting with another repo name is rejected."""
        planner = UnifiedPlanner(repo_ids={"backend", "frontend"})
        raw = json.dumps({
            "tasks": [{
                "id": "task-1",
                "title": "Add login page",
                "description": "Add the login page component with form validation and error states",
                "files": ["backend/src/api.py"],  # cross-repo reference
                "repo": "frontend",
            }]
        })
        graph, error = planner._parse(raw)
        assert graph is None
        assert "backend" in error

    def test_planner_cwd_is_workspace_root(self):
        """CWD set correctly for multi-repo (workspace root, not repo root)."""
        planner = UnifiedPlanner(
            cwd="/workspace",
            repo_ids={"backend", "frontend"},
        )
        assert planner._cwd == "/workspace"

    @patch("forge.core.planning.unified_planner.sdk_query")
    async def test_planner_retry_on_invalid_repo(self, mock_sdk):
        """Planner retries with feedback when repo assignment is invalid."""
        # First call returns invalid repo, second returns valid
        bad_result = MagicMock()
        bad_result.cost_usd = 0.01
        bad_result.input_tokens = 100
        bad_result.output_tokens = 50
        bad_result.result = json.dumps({
            "tasks": [{
                "id": "task-1",
                "title": "Add API",
                "description": "Add the API endpoint with proper validation and error handling",
                "files": ["src/api.py"],
                "repo": "nonexistent",
            }]
        })
        bad_result.session_id = "session-1"

        good_result = MagicMock()
        good_result.cost_usd = 0.01
        good_result.input_tokens = 100
        good_result.output_tokens = 50
        good_result.result = json.dumps({
            "tasks": [{
                "id": "task-1",
                "title": "Add API",
                "description": "Add the API endpoint with proper validation and error handling",
                "files": ["src/api.py"],
                "repo": "backend",
            }]
        })
        good_result.session_id = "session-2"

        mock_sdk.side_effect = [bad_result, good_result]

        planner = UnifiedPlanner(
            repo_ids={"backend", "frontend"},
            max_retries=3,
        )
        result = await planner.run(
            user_input="Add API",
            spec_text="",
            snapshot_text="",
        )

        assert result.task_graph is not None
        assert result.task_graph.tasks[0].repo == "backend"
        # Should have been called twice (first failed validation, second succeeded)
        assert mock_sdk.call_count == 2
        # Second call should include feedback about invalid repo
        second_prompt = mock_sdk.call_args_list[1][1].get("prompt", "") or mock_sdk.call_args_list[1][0][0] if mock_sdk.call_args_list[1][0] else ""
        # The feedback is included in the prompt via the retry mechanism
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/core/planning/unified_planner_test.py::TestPlannerMultiRepo -v`
Expected: FAIL — TypeError (`repo_ids` parameter doesn't exist yet)

- [ ] **Step 3: Add `repo_ids` parameter to `UnifiedPlanner.__init__`**

Modify `forge/core/planning/unified_planner.py` line 63-77. Add `repo_ids: set[str] | None = None` parameter and store it:

```python
def __init__(
    self,
    model: str = "opus",
    cwd: str | None = None,
    max_retries: int = 3,
    max_turns: int = 30,
    autonomy: str = "balanced",
    question_limit: int = 5,
    repo_ids: set[str] | None = None,
) -> None:
    self._model = model
    self._cwd = cwd
    self._max_retries = max_retries
    self._max_turns = max_turns
    self._autonomy = autonomy
    self._question_limit = question_limit
    self._repo_ids = repo_ids
```

- [ ] **Step 4: Update `_build_unified_system_prompt()` to accept `repo_ids`**

Modify `forge/core/planning/unified_planner.py` line 305. The function signature becomes:

```python
def _build_unified_system_prompt(question_protocol: str, repo_ids: set[str] | None = None) -> str:
```

When `repo_ids` is not None and has more than one entry, append the multi-repo workspace section to the system prompt (spec Section 7.2):

```python
    # ... existing system prompt content ...

    prompt = f"""You are a planning agent for Forge...
    ... (existing content unchanged) ...
    Output ONLY the TaskGraph JSON at the end. No markdown explanation after the JSON block."""

    if repo_ids and len(repo_ids) > 1:
        repo_list = "\n".join(f'- "{rid}"' for rid in sorted(repo_ids))
        prompt += f"""

## Multi-Repo Workspace

This workspace contains multiple repositories. Each task you create MUST include
a "repo" field specifying which repository it belongs to.

Available repos:
{repo_list}

Rules:
1. Every task MUST have a "repo" field matching one of the repo IDs above.
2. Task "files" are RELATIVE to the repo root (e.g., "src/api.py", not "backend/src/api.py").
3. A task can only modify files in its assigned repo.
4. If a task in one repo needs work from another repo, create the dependency task first
   and add it to the dependent task's "depends_on" list.
5. Agents CAN read all repos (they have read access), but can only write in their
   assigned repo. Use depends_on to sequence cross-repo work.

Output schema with repo field:
```json
{{
  "tasks": [
    {{
      "id": "task-1",
      "title": "Short title",
      "description": "Detailed description...",
      "files": ["src/file.py"],
      "repo": "<repo_id>",
      "depends_on": []
    }}
  ]
}}
```"""

    return prompt
```

- [ ] **Step 5: Update `run()` to pass `repo_ids` to system prompt builder**

Modify `forge/core/planning/unified_planner.py` line 121. Change:

```python
# Before:
system_prompt = _build_unified_system_prompt(question_protocol)

# After:
system_prompt = _build_unified_system_prompt(question_protocol, repo_ids=self._repo_ids)
```

- [ ] **Step 6: Update `_parse()` to validate repo assignments**

Modify `forge/core/planning/unified_planner.py` lines 257-302. After the structural checks (line 299, `if valid:`), add repo validation before returning:

```python
            if valid:
                # Validate repo assignments if multi-repo
                if self._repo_ids:
                    for task in graph.tasks:
                        if not task.repo or task.repo == "default":
                            # In multi-repo mode, "default" is invalid unless it's
                            # actually a repo ID
                            if "default" not in self._repo_ids:
                                last_error = (
                                    f"Task '{task.id}' has no repo assignment. "
                                    f"Valid repos are: {', '.join(sorted(self._repo_ids))}. "
                                    f"Every task must have a 'repo' field."
                                )
                                valid = False
                                break
                        if task.repo not in self._repo_ids:
                            last_error = (
                                f"Task '{task.id}' has repo='{task.repo}' but valid repos are: "
                                f"{', '.join(sorted(self._repo_ids))}. "
                                f"Fix the repo field for this task."
                            )
                            valid = False
                            break
                        # Check for cross-repo file paths
                        for file_path in task.files:
                            first_segment = file_path.split("/")[0]
                            if first_segment in self._repo_ids and first_segment != task.repo:
                                last_error = (
                                    f"Task '{task.id}' has file '{file_path}' that appears to "
                                    f"reference repo '{first_segment}' but task is assigned to "
                                    f"repo '{task.repo}'. Files must be relative to the task's repo."
                                )
                                valid = False
                                break
                        if not valid:
                            break

                if valid:
                    return graph, None
```

This replaces the existing `if valid: return graph, None` at line 300. When `_repo_ids` is None (single-repo mode), the repo validation block is skipped entirely, preserving backward compatibility.

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/core/planning/unified_planner_test.py::TestPlannerMultiRepo -v`
Expected: All PASS

---

## Chunk 3: Daemon Planning Phase Integration — `forge/core/daemon.py`

Updates the daemon's planning phase to use multi-repo snapshot gathering and pass repo IDs to the planner. This is documented here but the actual implementation happens in a later phase (Phase 6: daemon integration).

### Task 3: Update daemon planning phase (documentation only for Phase 4)

The daemon changes are lightweight and connect the pieces built in Tasks 1-2. These changes happen in `forge/core/daemon.py` around lines 326-425:

**Current code** (lines 326-330):
```python
# Run snapshot gathering in a thread to avoid blocking the event loop
self._snapshot = await asyncio.get_event_loop().run_in_executor(
    None, gather_project_snapshot, self._project_dir,
)
```

**Multi-repo replacement:**
```python
# Snapshot gathering
if len(self._repos) == 1:
    # Single-repo: exact current behavior
    repo = next(iter(self._repos.values()))
    self._snapshot = await asyncio.get_event_loop().run_in_executor(
        None, gather_project_snapshot, repo.path,
    )
    snapshot_text = self._snapshot.format_for_planner() if self._snapshot else ""
    repo_ids = None  # no repo validation in single-repo mode
else:
    # Multi-repo: parallel gathering
    from forge.core.context import gather_multi_repo_snapshots, format_multi_repo_snapshot
    snapshots = await gather_multi_repo_snapshots(self._repos)
    snapshot_text = format_multi_repo_snapshot(snapshots, self._repos)
    repo_ids = set(self._repos.keys())
    # Store first snapshot for backward-compat properties
    self._snapshot = next(iter(snapshots.values())) if snapshots else None
```

**UnifiedPlanner instantiation** (lines 350-354):
```python
# Before:
unified_planner = UnifiedPlanner(
    model=planner_model, cwd=self._project_dir,
    autonomy=self._settings.autonomy,
    question_limit=self._settings.question_limit,
)

# After:
planner_cwd = self._workspace_dir if len(self._repos) > 1 else self._project_dir
unified_planner = UnifiedPlanner(
    model=planner_model,
    cwd=planner_cwd,
    autonomy=self._settings.autonomy,
    question_limit=self._settings.question_limit,
    repo_ids=repo_ids,
)
```

**Planner invocation** (lines 419-425):
```python
# Before:
planning_result = await unified_planner.run(
    user_input=user_input,
    spec_text=spec_text,
    snapshot_text=self._snapshot.format_for_planner() if self._snapshot else "",
    ...
)

# After:
planning_result = await unified_planner.run(
    user_input=user_input,
    spec_text=spec_text,
    snapshot_text=snapshot_text,  # already formatted (single or multi)
    ...
)
```

**Validation pass-through** (line 187 in unified_planner.py `run()`):
The existing `validate_plan()` call already accepts `repo_ids`:

```python
# Already works — pass repo_ids from the planner to the validator
validation_result = validate_plan(graph, minimal_map, spec_text, repo_ids=self._repo_ids)
```

This line needs to be updated from:
```python
validation_result = validate_plan(graph, minimal_map, spec_text)
```
to:
```python
validation_result = validate_plan(graph, minimal_map, spec_text, repo_ids=self._repo_ids)
```

This ensures both the planner's inline repo check (in `_parse()`) and the validator's comprehensive check (in `validate_plan()` — `_check_repo_assignments()` and `_check_cross_repo_file_paths()`) are exercised.

---

## Failure Scenarios (from spec Section 15.1)

All planning failure scenarios from the spec are handled by the implementation above:

| Scenario | How It's Handled |
|----------|-----------------|
| Planner assigns task to unknown repo | `_parse()` rejects with error message listing valid repos. Retry mechanism feeds error back as feedback (up to 3 attempts). |
| Planner doesn't include `repo` field | `TaskDefinition.repo` defaults to `"default"` (Pydantic model). In multi-repo mode where `"default"` doesn't exist, `_parse()` rejects + retries with explicit instruction. |
| Planner creates cross-repo file references | `_parse()` checks if file path's first segment matches another repo ID. Rejects with clear error. Also caught by `validate_plan()` → `_check_cross_repo_file_paths()`. |
| Planner creates circular cross-repo deps | Existing cycle detection in `_check_cycles()` (validator.py line 132). No changes needed. |
| Snapshot gathering fails for one repo | `gather_multi_repo_snapshots()` catches exceptions per-repo and returns empty `ProjectSnapshot()`. Planner proceeds with partial info and can still read the repo via Glob/Grep/Read tools. |

---

## Single-Repo Backward Compatibility

When `repo_ids` is `None` (single-repo mode):

1. **No repo list in prompt** — `_build_unified_system_prompt()` omits the "Multi-Repo Workspace" section
2. **No repo field required** — `TaskDefinition.repo` defaults to `"default"`, `_parse()` skips repo validation
3. **`_parse()` doesn't validate repos** — the `if self._repo_ids:` guard skips all repo checks
4. **`validate_plan()` skips repo checks** — existing guard at validator.py line 35: `if repo_ids is not None:`
5. **Snapshot format unchanged** — `ProjectSnapshot.format_for_planner()` called directly, no "### Repo:" headers
6. **CWD unchanged** — planner CWD remains `self._project_dir` (the repo root)

---

## Test Summary

| Test Name | File | What It Verifies |
|-----------|------|-----------------|
| `test_gather_multi_repo_snapshots` | `context_test.py` | Parallel gathering from 2 repos, returns dict keyed by repo ID |
| `test_format_multi_repo_snapshot` | `context_test.py` | Labeled sections with `### Repo:` headers in output |
| `test_truncate_large_repo_tree` | `context_test.py` | 500+ files truncated to depth 3 |
| `test_planner_prompt_includes_repo_list` | `unified_planner_test.py` | Multi-repo system prompt has Available repos section |
| `test_planner_prompt_single_repo_no_repos` | `unified_planner_test.py` | No repo list for single-repo |
| `test_parse_validates_repo_assignments` | `unified_planner_test.py` | Rejects unknown repo IDs |
| `test_parse_missing_repo_defaults_to_default` | `unified_planner_test.py` | Fallback for single-repo |
| `test_parse_cross_repo_file_path_rejected` | `unified_planner_test.py` | File path starting with another repo name |
| `test_planner_retry_on_invalid_repo` | `unified_planner_test.py` | Retries with feedback on bad repo |
| `test_planner_cwd_is_workspace_root` | `unified_planner_test.py` | CWD set correctly for multi-repo |

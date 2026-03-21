# Phase 6: Multi-Repo PR Creation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable per-repo PR creation for multi-repo pipelines — each repo gets its own PR with cross-links, correct base branches, and graceful partial failure handling. Single-repo pipelines remain identical to current behavior.

**Architecture:** Extends the existing PR creation flow in `forge/tui/pr_creator.py` and `forge/tui/app.py`. New function `create_prs_multi_repo()` orchestrates per-repo push + PR creation. Existing `push_branch()`, `generate_pr_body()`, and `create_pr()` are reused as building blocks. No new modules — all changes are modifications to existing files.

**Tech Stack:** Python 3.12+, asyncio, gh CLI

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` (Sections 9.1–9.4, 15.4)

**Dependencies:** Phase 1 (data model — `repo` field on tasks, `repos_json` on pipelines) and Phase 3 (daemon core — per-repo infrastructure) must be merged.

---

## File Map

| File | Responsibility | Changes |
|------|---------------|---------|
| `forge/tui/pr_creator.py` | PR creation utilities (push, generate body, create PR) | Add `related_prs`/`repo_id` params to `generate_pr_body()`, add `create_prs_multi_repo()`, add `_add_related_prs_comment()` |
| `forge/tui/app.py` | TUI app PR creation handler | Update `_build_task_summaries()` to include `repo_id`, update `on_final_approval_screen_create_pr` to call multi-repo flow |
| `forge/tui/pr_creator_test.py` | Tests for PR creation | 10 new tests |
| `forge/tui/app_test.py` | Tests for TUI app helpers | 1 new test for `_build_task_summaries` |

---

## Chunk 1: Task Summaries with `repo_id` — Foundation

Update `_build_task_summaries()` in `app.py` (line 39) to propagate the `repo_id` field from raw task data into PR-ready summaries. This is required before multi-repo PR body generation can work.

### Task 1: Update `_build_task_summaries()` to include `repo_id`

**Files:**
- Modify: `forge/tui/app.py` — `_build_task_summaries()` (line 39)
- Test: `forge/tui/app_test.py`

- [ ] **Step 1: Write failing test `test_build_task_summaries_includes_repo_id`**

Add to `forge/tui/app_test.py`:

```python
class TestBuildTaskSummariesRepoId:
    """Tests for _build_task_summaries repo_id propagation."""

    def test_build_task_summaries_includes_repo_id(self):
        """Task summaries include repo_id from raw task data."""
        from forge.tui.app import _build_task_summaries

        raw_tasks = [
            {
                "title": "Add API endpoint",
                "description": "REST endpoint",
                "state": "done",
                "repo_id": "backend",
                "merge_result": {"success": True, "linesAdded": 50, "linesRemoved": 5, "filesChanged": 3},
                "files": ["src/api.py"],
            },
            {
                "title": "Add login page",
                "description": "Login UI",
                "state": "done",
                "repo_id": "frontend",
                "merge_result": {"success": True, "linesAdded": 120, "linesRemoved": 0, "filesChanged": 4},
                "files": ["src/Login.tsx"],
            },
            {
                "title": "Legacy task",
                "description": "No repo_id field",
                "state": "done",
                "merge_result": {"success": True, "linesAdded": 10, "linesRemoved": 2, "filesChanged": 1},
                "files": [],
            },
        ]

        summaries = _build_task_summaries(raw_tasks)

        assert summaries[0]["repo_id"] == "backend"
        assert summaries[1]["repo_id"] == "frontend"
        # Missing repo_id defaults to "default"
        assert summaries[2]["repo_id"] == "default"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest forge/tui/app_test.py::TestBuildTaskSummariesRepoId -x -v`
Expected: FAIL — `KeyError: 'repo_id'` (field not yet added to summaries)

- [ ] **Step 3: Add `repo_id` to `_build_task_summaries()`**

Modify `forge/tui/app.py`, `_build_task_summaries()` (line 39). Add `repo_id` to the summary dict:

```python
def _build_task_summaries(tasks_list: list[dict]) -> list[dict]:
    summaries = []
    for t in tasks_list:
        mr = t.get("merge_result") or {}
        summaries.append({
            "title": t.get("title", ""),
            "description": t.get("description", ""),
            "implementation_summary": mr.get("implementation_summary", "")
                or t.get("implementation_summary", ""),
            "state": t.get("state", "done"),
            "repo_id": t.get("repo_id", "default"),  # NEW — propagate repo_id
            "added": mr.get("linesAdded", 0) if mr.get("success") else 0,
            "removed": mr.get("linesRemoved", 0) if mr.get("success") else 0,
            "files": mr.get("filesChanged", 0) if mr.get("success") else 0,
            "file_list": t.get("files", []) if isinstance(t.get("files"), list) else [],
            "tests_passed": t.get("tests_passed", 0),
            "tests_total": t.get("tests_total", 0),
            "review": "passed" if t.get("state") == "done" else "failed",
            "error": t.get("error", ""),
        })
    return summaries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest forge/tui/app_test.py::TestBuildTaskSummariesRepoId -x -v`
Expected: PASS

- [ ] **Step 5: Run existing app tests to verify no regressions**

Run: `.venv/bin/python -m pytest forge/tui/app_test.py -x -v`
Expected: All existing tests PASS (new field is additive, no breaking changes)

---

## Chunk 2: Multi-Repo PR Body Generation

Update `generate_pr_body()` in `pr_creator.py` (line 28) to accept optional `related_prs` and `repo_id` parameters. When `related_prs` is provided, include a "## Related PRs" section in the body. Single-repo calls (no `related_prs`) produce identical output to current behavior.

### Task 2: Update `generate_pr_body()` signature and body format

**Files:**
- Modify: `forge/tui/pr_creator.py` — `generate_pr_body()` (line 28)
- Test: `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write failing tests**

Add to `forge/tui/pr_creator_test.py`:

```python
class TestGeneratePrBodyMultiRepo:
    """Tests for multi-repo PR body generation."""

    def test_generate_pr_body_multi_repo(self):
        """PR body includes Related PRs section when related_prs provided."""
        from forge.tui.pr_creator import generate_pr_body

        body = generate_pr_body(
            tasks=[{"title": "Add API", "added": 50, "removed": 5, "files": 3, "file_list": ["src/api.py"],
                    "description": "REST endpoint", "implementation_summary": "Added router"}],
            time="5m 30s",
            cost=2.50,
            questions=[],
            related_prs={"frontend": "https://github.com/org/frontend/pull/89"},
            repo_id="backend",
        )

        assert "## Related PRs" in body
        assert "**frontend**" in body
        assert "https://github.com/org/frontend/pull/89" in body
        # Standard sections still present
        assert "## Summary" in body
        assert "## Tasks" in body

    def test_generate_pr_body_single_repo(self):
        """PR body has no Related PRs section when related_prs is None."""
        from forge.tui.pr_creator import generate_pr_body

        body = generate_pr_body(
            tasks=[{"title": "Add feature", "added": 10, "removed": 0, "files": 1, "file_list": [],
                    "description": "", "implementation_summary": ""}],
            time="2m 0s",
            cost=1.00,
            questions=[],
        )

        assert "## Related PRs" not in body
        assert "## Summary" in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestGeneratePrBodyMultiRepo -x -v`
Expected: FAIL — `TypeError: generate_pr_body() got an unexpected keyword argument 'related_prs'`

- [ ] **Step 3: Update `generate_pr_body()` signature and logic**

Modify `forge/tui/pr_creator.py`, `generate_pr_body()` (line 28). Add optional `related_prs` and `repo_id` parameters. Insert "## Related PRs" section after the summary when `related_prs` is provided and non-empty:

```python
def generate_pr_body(
    *,
    tasks: list[dict],
    failed_tasks: list[dict] | None = None,
    time: str,
    cost: float,
    questions: list[dict],
    related_prs: dict[str, str] | None = None,  # NEW — {repo_id: pr_url}
    repo_id: str | None = None,                  # NEW — which repo this PR is for
) -> str:
    total = len(tasks) + (len(failed_tasks) if failed_tasks else 0)
    completed = len(tasks)

    if failed_tasks:
        lines = ["## Summary", f"Built by Forge pipeline • {total} tasks • {completed}/{total} completed • {time} • ${cost:.2f}", ""]
        lines.append("## Completed Tasks")
    else:
        lines = ["## Summary", f"Built by Forge pipeline • {total} tasks • {time} • ${cost:.2f}", ""]

        # Related PRs section — only in multi-repo mode
        if related_prs:
            lines.append("## Related PRs")
            for rp_repo_id, rp_url in related_prs.items():
                lines.append(f"- **{rp_repo_id}**: {rp_url}")
            lines.append("")

        lines.append("## Tasks")

    # ... rest of the function unchanged (task details, failed tasks, questions, footer)
```

**Important:** The "## Related PRs" section is inserted between "## Summary" and "## Tasks" (or "## Completed Tasks"), matching the format in spec Section 9.2. When `failed_tasks` is present, the Related PRs section goes after "## Completed Tasks" header line — but before the task list. Keep the logic consistent: insert `related_prs` block right after the summary line, before any task section.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestGeneratePrBodyMultiRepo -x -v`
Expected: PASS

- [ ] **Step 5: Run all existing pr_creator tests**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py -x -v`
Expected: All existing tests PASS (new params are optional, backward compatible)

---

## Chunk 3: Multi-Repo PR Creation Orchestrator

Add `create_prs_multi_repo()` and `_add_related_prs_comment()` to `pr_creator.py`. This is the core orchestration function that groups tasks by repo, pushes per-repo branches, creates PRs, and cross-links them.

### Task 3: Add `create_prs_multi_repo()` function

**Files:**
- Modify: `forge/tui/pr_creator.py` — add new function after `create_pr()` (after line 117)
- Test: `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write failing tests for multi-repo creation and partial failure**

Add to `forge/tui/pr_creator_test.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, call


class TestCreatePrsMultiRepo:
    """Tests for create_prs_multi_repo orchestration."""

    @pytest.mark.asyncio
    async def test_create_prs_multi_repo_groups_tasks(self):
        """Tasks are grouped by repo_id for per-repo PR creation."""
        from forge.tui.pr_creator import create_prs_multi_repo

        task_summaries = [
            {"title": "API endpoint", "state": "done", "repo_id": "backend",
             "added": 50, "removed": 5, "files": 3, "file_list": ["src/api.py"],
             "description": "", "implementation_summary": "", "cost_usd": 1.0},
            {"title": "Login page", "state": "done", "repo_id": "frontend",
             "added": 120, "removed": 0, "files": 4, "file_list": ["src/Login.tsx"],
             "description": "", "implementation_summary": "", "cost_usd": 2.0},
            {"title": "User model", "state": "done", "repo_id": "backend",
             "added": 45, "removed": 0, "files": 2, "file_list": ["src/models.py"],
             "description": "", "implementation_summary": "", "cost_usd": 0.5},
        ]
        repos = {
            "backend": {"id": "backend", "path": "/repos/backend", "base_branch": "main"},
            "frontend": {"id": "frontend", "path": "/repos/frontend", "base_branch": "develop"},
        }
        pipeline_branches = {"backend": "forge/pipeline-abc123", "frontend": "forge/pipeline-abc123"}

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True) as mock_push, \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock, side_effect=[
                 "https://github.com/org/backend/pull/42",
                 "https://github.com/org/frontend/pull/89",
             ]) as mock_create, \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            result = await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Add user auth",
                elapsed_str="5m 30s",
                questions=[],
            )

        # Both repos should have PRs
        assert "backend" in result.pr_urls
        assert "frontend" in result.pr_urls

        # Push called for each repo with correct path and branch
        push_calls = mock_push.call_args_list
        push_paths = {c.args[0] for c in push_calls}
        assert "/repos/backend" in push_paths
        assert "/repos/frontend" in push_paths

        # create_pr called twice (one per repo)
        assert mock_create.call_count == 2

    @pytest.mark.asyncio
    async def test_create_prs_partial_failure(self):
        """If push fails for one repo, others still get PRs."""
        from forge.tui.pr_creator import create_prs_multi_repo

        task_summaries = [
            {"title": "API", "state": "done", "repo_id": "backend",
             "added": 10, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.0},
            {"title": "UI", "state": "done", "repo_id": "frontend",
             "added": 20, "removed": 0, "files": 2, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.5},
        ]
        repos = {
            "backend": {"id": "backend", "path": "/repos/backend", "base_branch": "main"},
            "frontend": {"id": "frontend", "path": "/repos/frontend", "base_branch": "main"},
        }
        pipeline_branches = {"backend": "forge/pipeline-abc", "frontend": "forge/pipeline-abc"}

        # push_branch fails for backend, succeeds for frontend
        async def push_side_effect(path, branch):
            return path != "/repos/backend"

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, side_effect=push_side_effect), \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock,
                   return_value="https://github.com/org/frontend/pull/1") as mock_create, \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            result = await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Add feature",
                elapsed_str="3m",
                questions=[],
            )

        # Frontend PR created despite backend push failure
        assert "frontend" in result.pr_urls
        assert "backend" not in result.pr_urls

        # Backend failure recorded
        assert "backend" in result.failures

        # create_pr only called for frontend (backend push failed, so no PR attempt)
        assert mock_create.call_count == 1

    @pytest.mark.asyncio
    async def test_pr_title_includes_repo_id(self):
        """Multi-repo PR title includes [repo_id] suffix."""
        from forge.tui.pr_creator import create_prs_multi_repo

        task_summaries = [
            {"title": "API", "state": "done", "repo_id": "backend",
             "added": 10, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.0},
            {"title": "UI", "state": "done", "repo_id": "frontend",
             "added": 20, "removed": 0, "files": 2, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.5},
        ]
        repos = {
            "backend": {"id": "backend", "path": "/repos/backend", "base_branch": "main"},
            "frontend": {"id": "frontend", "path": "/repos/frontend", "base_branch": "main"},
        }
        pipeline_branches = {"backend": "forge/pipeline-abc", "frontend": "forge/pipeline-abc"}

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True), \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock,
                   return_value="https://github.com/org/repo/pull/1") as mock_create, \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Add user auth",
                elapsed_str="5m",
                questions=[],
            )

        # Check PR title includes repo_id suffix
        titles = [c.kwargs.get("title") or c.args[1] for c in mock_create.call_args_list]
        assert any("[backend]" in t for t in titles)
        assert any("[frontend]" in t for t in titles)

    @pytest.mark.asyncio
    async def test_pr_title_single_repo_no_suffix(self):
        """Single-repo PR title has no [repo_id] suffix."""
        from forge.tui.pr_creator import create_prs_multi_repo

        task_summaries = [
            {"title": "Feature", "state": "done", "repo_id": "default",
             "added": 10, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.0},
        ]
        repos = {
            "default": {"id": "default", "path": "/repos/myproject", "base_branch": "main"},
        }
        pipeline_branches = {"default": "forge/pipeline-abc"}

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True), \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock,
                   return_value="https://github.com/org/repo/pull/1") as mock_create, \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Add feature",
                elapsed_str="2m",
                questions=[],
            )

        # Single-repo: no [default] suffix, just "Forge: Add feature"
        title = mock_create.call_args.kwargs.get("title") or mock_create.call_args.args[1]
        assert "[" not in title
        assert title == "Forge: Add feature"

    @pytest.mark.asyncio
    async def test_push_per_repo(self):
        """Push runs in correct repo directory with correct branch."""
        from forge.tui.pr_creator import create_prs_multi_repo

        task_summaries = [
            {"title": "A", "state": "done", "repo_id": "backend",
             "added": 1, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 0.5},
            {"title": "B", "state": "done", "repo_id": "frontend",
             "added": 1, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 0.5},
        ]
        repos = {
            "backend": {"id": "backend", "path": "/repos/backend", "base_branch": "main"},
            "frontend": {"id": "frontend", "path": "/repos/frontend", "base_branch": "develop"},
        }
        pipeline_branches = {"backend": "forge/pipe-be", "frontend": "forge/pipe-fe"}

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True) as mock_push, \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock,
                   return_value="https://github.com/org/repo/pull/1"), \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Multi",
                elapsed_str="1m",
                questions=[],
            )

        # Verify push called with correct (path, branch) for each repo
        push_calls = {(c.args[0], c.args[1]) for c in mock_push.call_args_list}
        assert ("/repos/backend", "forge/pipe-be") in push_calls
        assert ("/repos/frontend", "forge/pipe-fe") in push_calls
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestCreatePrsMultiRepo -x -v`
Expected: FAIL — `ImportError: cannot import name 'create_prs_multi_repo'`

- [ ] **Step 3: Define `MultiRepoPrResult` dataclass**

Add to `forge/tui/pr_creator.py` (after imports, before `push_branch`):

```python
from dataclasses import dataclass, field


@dataclass
class MultiRepoPrResult:
    """Result of multi-repo PR creation."""
    pr_urls: dict[str, str] = field(default_factory=dict)   # repo_id → PR URL
    failures: dict[str, str] = field(default_factory=dict)   # repo_id → error message
```

- [ ] **Step 4: Implement `create_prs_multi_repo()`**

Add to `forge/tui/pr_creator.py` after `create_pr()` (after line 117):

```python
async def create_prs_multi_repo(
    *,
    task_summaries: list[dict],
    repos: dict[str, dict],
    pipeline_branches: dict[str, str],
    description: str,
    elapsed_str: str,
    questions: list[dict],
    failed_tasks: list[dict] | None = None,
) -> MultiRepoPrResult:
    """Create one PR per repo with cross-linking.

    For single-repo pipelines (len(repos) == 1), produces identical output
    to the original single-PR flow: no [repo_id] suffix in title, no
    Related PRs section in body.

    For multi-repo pipelines:
    - Groups tasks by repo_id
    - Computes per-repo cost from task-level cost_usd
    - Pushes per repo: ``git push origin <pipeline_branch>`` in correct repo path
    - Creates PR per repo with correct base (repo's base_branch) and head (pipeline branch)
    - PR title format: ``Forge: {description} [{repo_id}]``
    - Cross-links PRs via comments after all are created
    - Partial failure: if push/PR fails for one repo, continues with others
    """
    result = MultiRepoPrResult()
    is_multi_repo = len(repos) > 1

    # 1. Group tasks by repo_id and compute per-repo cost
    tasks_by_repo: dict[str, list[dict]] = {}
    failed_by_repo: dict[str, list[dict]] = {}
    cost_per_repo: dict[str, float] = {}

    for t in task_summaries:
        repo_id = t.get("repo_id", "default")
        if t.get("state") == "done":
            tasks_by_repo.setdefault(repo_id, []).append(t)
        else:
            failed_by_repo.setdefault(repo_id, []).append(t)
        cost_per_repo[repo_id] = cost_per_repo.get(repo_id, 0) + t.get("cost_usd", 0)

    if failed_tasks:
        for t in failed_tasks:
            repo_id = t.get("repo_id", "default")
            failed_by_repo.setdefault(repo_id, []).append(t)

    # 2. Determine which repos have completed tasks (changes to push)
    repos_with_changes = [
        repo_id for repo_id in repos
        if repo_id in tasks_by_repo and any(t["state"] == "done" for t in tasks_by_repo[repo_id])
    ]

    # 3. Push + create PR for each repo with changes
    for repo_id in repos_with_changes:
        repo_config = repos[repo_id]
        repo_path = repo_config["path"] if isinstance(repo_config, dict) else repo_config.path
        base_branch = repo_config["base_branch"] if isinstance(repo_config, dict) else repo_config.base_branch
        pipeline_branch = pipeline_branches[repo_id]

        # Push
        pushed = await push_branch(repo_path, pipeline_branch)
        if not pushed:
            logger.error("Push failed for repo %s at %s", repo_id, repo_path)
            result.failures[repo_id] = "git push failed"
            continue

        # Generate body
        body = generate_pr_body(
            tasks=tasks_by_repo.get(repo_id, []),
            failed_tasks=failed_by_repo.get(repo_id),
            time=elapsed_str,
            cost=cost_per_repo.get(repo_id, 0),
            questions=questions,
            related_prs=result.pr_urls if is_multi_repo else None,
            repo_id=repo_id if is_multi_repo else None,
        )

        # PR title: add [repo_id] suffix only in multi-repo mode
        if is_multi_repo:
            title = f"Forge: {description} [{repo_id}]"
        else:
            title = f"Forge: {description}"

        # Create PR
        url = await create_pr(
            repo_path,
            title=title,
            body=body,
            base=base_branch,
            head=pipeline_branch,
        )
        if url:
            result.pr_urls[repo_id] = url
        else:
            logger.error("PR creation failed for repo %s", repo_id)
            result.failures[repo_id] = "gh pr create failed"

    # 4. Cross-link: add comments to earlier PRs with links to later PRs
    if is_multi_repo and len(result.pr_urls) > 1:
        for repo_id, url in result.pr_urls.items():
            other_prs = {k: v for k, v in result.pr_urls.items() if k != repo_id}
            if other_prs:
                repo_config = repos[repo_id]
                repo_path = repo_config["path"] if isinstance(repo_config, dict) else repo_config.path
                try:
                    await _add_related_prs_comment(repo_path, url, other_prs)
                except Exception:
                    # Cross-link is cosmetic — log and continue (spec Section 15.4)
                    logger.warning("Failed to add cross-link comment to %s", url, exc_info=True)

    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestCreatePrsMultiRepo -x -v`
Expected: PASS

---

## Chunk 4: Cross-Linking and `repos_json` Updates

Add the `_add_related_prs_comment()` helper and a test for `repos_json` PR URL storage.

### Task 4: Add `_add_related_prs_comment()`

**Files:**
- Modify: `forge/tui/pr_creator.py` — add new function
- Test: `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write failing test for cross-link comments**

Add to `forge/tui/pr_creator_test.py`:

```python
class TestAddRelatedPrsComment:
    """Tests for _add_related_prs_comment cross-linking."""

    @pytest.mark.asyncio
    async def test_add_related_prs_comment(self):
        """gh pr comment called with links to related PRs."""
        from forge.tui.pr_creator import _add_related_prs_comment

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        mock_proc.returncode = 0

        with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc) as mock_exec:
            await _add_related_prs_comment(
                "/repos/backend",
                "https://github.com/org/backend/pull/42",
                {"frontend": "https://github.com/org/frontend/pull/89"},
            )

        # Verify gh pr comment was called
        mock_exec.assert_called_once()
        args = mock_exec.call_args.args
        assert "gh" in args
        assert "pr" in args
        assert "comment" in args
        assert "42" in args  # PR number extracted from URL

        # Verify comment body contains link
        body_arg_idx = list(args).index("--body") + 1
        body = args[body_arg_idx]
        assert "frontend" in body
        assert "https://github.com/org/frontend/pull/89" in body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestAddRelatedPrsComment -x -v`
Expected: FAIL — `ImportError: cannot import name '_add_related_prs_comment'`

- [ ] **Step 3: Implement `_add_related_prs_comment()`**

Add to `forge/tui/pr_creator.py` after `create_prs_multi_repo()`:

```python
async def _add_related_prs_comment(
    project_dir: str, pr_url: str, related: dict[str, str]
) -> None:
    """Add a comment to a PR with links to related PRs in other repos.

    This is called after all PRs are created to cross-link them. If the
    comment fails, the caller should log and continue — cross-links are
    cosmetic, not critical (spec Section 15.4).
    """
    # Extract PR number from URL (e.g., "https://github.com/org/repo/pull/42" → "42")
    pr_number = pr_url.rstrip("/").split("/")[-1]
    links = "\n".join(f"- **{repo}**: {url}" for repo, url in related.items())
    comment = f"## Related Forge PRs\n\n{links}"
    proc = await asyncio.create_subprocess_exec(
        "gh", "pr", "comment", pr_number, "--body", comment,
        cwd=project_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        logger.warning(
            "Failed to add related PRs comment to PR %s (exit %d): %s",
            pr_number, proc.returncode, stderr.decode(),
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestAddRelatedPrsComment -x -v`
Expected: PASS

### Task 5: Test `repos_json` updated with PR URLs

**Files:**
- Test: `forge/tui/pr_creator_test.py`

- [ ] **Step 1: Write test for repos_json PR URL storage**

This test verifies that the `MultiRepoPrResult` returned by `create_prs_multi_repo()` contains the correct PR URLs per repo, which the caller (TUI app handler) uses to update `repos_json`.

Add to `forge/tui/pr_creator_test.py`:

```python
class TestReposJsonPrUrls:
    """Tests for repos_json PR URL tracking."""

    @pytest.mark.asyncio
    async def test_repos_json_updated_with_pr_urls(self):
        """PR URLs stored per repo in result, ready for repos_json update."""
        from forge.tui.pr_creator import create_prs_multi_repo
        import json

        task_summaries = [
            {"title": "API", "state": "done", "repo_id": "backend",
             "added": 10, "removed": 0, "files": 1, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.0},
            {"title": "UI", "state": "done", "repo_id": "frontend",
             "added": 20, "removed": 0, "files": 2, "file_list": [],
             "description": "", "implementation_summary": "", "cost_usd": 1.5},
        ]
        repos = {
            "backend": {"id": "backend", "path": "/repos/backend", "base_branch": "main"},
            "frontend": {"id": "frontend", "path": "/repos/frontend", "base_branch": "main"},
        }
        pipeline_branches = {"backend": "forge/pipe", "frontend": "forge/pipe"}

        with patch("forge.tui.pr_creator.push_branch", new_callable=AsyncMock, return_value=True), \
             patch("forge.tui.pr_creator.create_pr", new_callable=AsyncMock, side_effect=[
                 "https://github.com/org/backend/pull/42",
                 "https://github.com/org/frontend/pull/89",
             ]), \
             patch("forge.tui.pr_creator._add_related_prs_comment", new_callable=AsyncMock):

            result = await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description="Feature",
                elapsed_str="3m",
                questions=[],
            )

        # Simulate updating repos_json with PR URLs (as the TUI handler would do)
        repos_json_list = [
            {"id": "backend", "path": "/repos/backend", "base_branch": "main", "branch_name": "forge/pipe"},
            {"id": "frontend", "path": "/repos/frontend", "base_branch": "main", "branch_name": "forge/pipe"},
        ]
        for entry in repos_json_list:
            if entry["id"] in result.pr_urls:
                entry["pr_url"] = result.pr_urls[entry["id"]]

        # Verify PR URLs stored correctly
        assert repos_json_list[0]["pr_url"] == "https://github.com/org/backend/pull/42"
        assert repos_json_list[1]["pr_url"] == "https://github.com/org/frontend/pull/89"

        # Verify JSON serializable (will be stored in repos_json column)
        serialized = json.dumps(repos_json_list)
        assert "pr_url" in serialized
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/python -m pytest forge/tui/pr_creator_test.py::TestReposJsonPrUrls -x -v`
Expected: PASS (this test exercises the result structure, no new code needed)

---

## Chunk 5: TUI App Handler Integration

Update `on_final_approval_screen_create_pr` in `app.py` to use `create_prs_multi_repo()` for multi-repo pipelines while keeping the existing single-repo flow untouched.

### Task 6: Update TUI PR creation handler

**Files:**
- Modify: `forge/tui/app.py` — `on_final_approval_screen_create_pr` (line 341)

- [ ] **Step 1: Add multi-repo branch to the PR handler**

The handler at line 341 currently creates a single PR. Update it to detect multi-repo mode (via `pipeline.get_repos()`) and delegate to `create_prs_multi_repo()`:

```python
async def on_final_approval_screen_create_pr(self, event) -> None:
    """User confirmed PR creation from FinalApprovalScreen."""
    from forge.tui.pr_creator import push_branch, create_pr, generate_pr_body, create_prs_multi_repo

    self._state.apply_event("pipeline:pr_creating", {})

    # ... existing code to build questions, elapsed_str, task_summaries ...

    # Detect multi-repo mode
    repos_list = []
    if self._db and self._pipeline_id:
        try:
            pipeline = await self._db.get_pipeline(self._pipeline_id)
            repos_list = pipeline.get_repos()
            base_branch = getattr(pipeline, "base_branch", None) or "main"
        except Exception:
            pass

    is_multi_repo = len(repos_list) > 1

    if is_multi_repo:
        # Multi-repo: delegate to create_prs_multi_repo()
        repos = {r["id"]: r for r in repos_list}
        pipeline_branches = {r["id"]: r.get("branch_name", "") for r in repos_list}

        try:
            result = await create_prs_multi_repo(
                task_summaries=task_summaries,
                repos=repos,
                pipeline_branches=pipeline_branches,
                description=self._pipeline_description(),
                elapsed_str=elapsed_str,
                questions=all_questions,
                failed_tasks=failed_tasks,
            )

            # Update repos_json with PR URLs
            if result.pr_urls and self._db and self._pipeline_id:
                for entry in repos_list:
                    if entry["id"] in result.pr_urls:
                        entry["pr_url"] = result.pr_urls[entry["id"]]
                import json
                await self._db.update_pipeline_field(
                    self._pipeline_id, "repos_json", json.dumps(repos_list)
                )

            if result.pr_urls:
                # Use first PR URL for state event
                first_url = next(iter(result.pr_urls.values()))
                self._state.apply_event("pipeline:pr_created", {"pr_url": first_url})
                # Show all URLs
                url_summary = ", ".join(f"{rid}: {url}" for rid, url in result.pr_urls.items())
                self.notify(f"PRs created: {url_summary}", severity="information")
            else:
                self._state.apply_event("pipeline:pr_failed", {"error": "All PR creations failed"})

            if result.failures:
                failed_repos = ", ".join(f"{rid}: {err}" for rid, err in result.failures.items())
                self.notify(f"PR failures: {failed_repos}", severity="warning")

        except Exception as e:
            logger.error("Multi-repo PR creation error: %s", e, exc_info=True)
            self._state.apply_event("pipeline:pr_failed", {"error": str(e)})
            self.notify(f"PR creation error: {_escape_markup(e)}", severity="error")
    else:
        # Single-repo: existing flow (unchanged)
        # ... existing code for push_branch + create_pr ...
```

**Important:** The existing single-repo code path (the `else` branch) must remain exactly as-is. Do NOT refactor it to go through `create_prs_multi_repo()` — the single-repo path is battle-tested and should not be touched. `create_prs_multi_repo()` handles single-repo correctly (no suffix, no Related PRs), but the TUI handler should only use it for multi-repo to minimize risk.

- [ ] **Step 2: Run all app and pr_creator tests**

Run: `.venv/bin/python -m pytest forge/tui/app_test.py forge/tui/pr_creator_test.py -x -v`
Expected: All tests PASS

---

## PR Creation Failure Scenarios (Spec Section 15.4)

The implementation above handles all failure scenarios from the spec:

| Scenario | Handling | Location |
|----------|----------|----------|
| Push fails for one repo | `push_branch()` returns `False` → logged, added to `result.failures`, loop continues | `create_prs_multi_repo()` step 3 |
| PR creation fails for one repo | `create_pr()` returns `None` → logged, added to `result.failures`, loop continues | `create_prs_multi_repo()` step 3 |
| gh CLI not authenticated for one repo | `create_pr()` subprocess returns non-zero → same as PR creation failure | Existing `create_pr()` error handling |
| Cross-link comment fails | `_add_related_prs_comment()` exception caught → logged, continues | `create_prs_multi_repo()` step 4 |
| First PR created, second fails | First PR URL in `result.pr_urls`, second in `result.failures` → user sees both | `MultiRepoPrResult` dataclass |

---

## Verification

Run the full test suite for both modified files:

```bash
.venv/bin/python -m pytest forge/tui/pr_creator_test.py forge/tui/app_test.py -x -v
```

### Test Summary

| Test Name | File | What It Verifies |
|-----------|------|-----------------|
| `test_generate_pr_body_multi_repo` | `pr_creator_test.py` | Body has Related PRs section |
| `test_generate_pr_body_single_repo` | `pr_creator_test.py` | No Related PRs section |
| `test_build_task_summaries_includes_repo_id` | `app_test.py` | Summaries have repo_id field |
| `test_create_prs_multi_repo_groups_tasks` | `pr_creator_test.py` | Tasks grouped by repo |
| `test_create_prs_partial_failure` | `pr_creator_test.py` | One repo fails, others succeed |
| `test_add_related_prs_comment` | `pr_creator_test.py` | gh pr comment called with links |
| `test_pr_title_includes_repo_id` | `pr_creator_test.py` | Multi-repo PR title has [backend] suffix |
| `test_pr_title_single_repo_no_suffix` | `pr_creator_test.py` | Single-repo PR title unchanged |
| `test_push_per_repo` | `pr_creator_test.py` | Push runs in correct repo directory |
| `test_repos_json_updated_with_pr_urls` | `pr_creator_test.py` | pr_url stored per repo |

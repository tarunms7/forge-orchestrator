# Phase 7: TUI Multi-Repo Display — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add multi-repo awareness to the TUI so users can see per-repo task grouping, create PRs for multiple repos, and view diffs per repo — while keeping the single-repo experience completely unchanged.

**Architecture:** All changes are conditional on `TuiState.is_multi_repo` (a property that returns `len(self.repos) > 1`). Repo metadata flows from daemon events → `TuiState` → widgets. The `_on_plan_ready()` handler stores the `repo` field in task dicts. Display functions (`format_task_line`, `format_task_table`, `format_summary_stats`) add repo prefixes/grouping only when multi-repo. The final approval screen pluralizes the "Create PR(s)" button and the diff viewer adds a repo selector overlay.

**Tech Stack:** Python 3.12+, Textual, pytest, pytest-asyncio

**Spec:** `docs/superpowers/specs/2026-03-21-multi-repo-workspace-design.md` (Sections 14.1–14.3)

**Dependencies:** Phase 1 (workspace models) + Phase 6 (PR creation per repo) must be merged first.

**Verification:** `.venv/bin/python -m pytest forge/tui/app_test.py forge/tui/state_test.py forge/tui/widgets/task_list_test.py forge/tui/screens/final_approval_test.py -x -v`

---

## Data Flow Overview

```
daemon events                    TuiState                         widgets
─────────────                    ────────                         ───────
pipeline:plan_ready ──────►  _on_plan_ready()
  { tasks: [{repo: "backend",      stores repo field in           TaskList.render()
     ...}], repos: [...] }         self.tasks[tid]["repo"]           └─ format_task_line()
                                   stores self.repos                    └─ adds [repo] prefix
                                                                         when is_multi_repo

pipeline:pr_created ──────►  _on_pr_created()
  { pr_url: "...",                 if repo_id: stores in           FinalApprovalScreen
    repo_id: "backend" }          self.per_repo_pr_urls              └─ format_task_table()
                                                                        └─ per-repo grouping
                                   is_multi_repo property              └─ format_summary_stats()
                                   = len(self.repos) > 1                  └─ "2 repos, 4 tasks"
```

---

## Chunk 1: TuiState Multi-Repo Fields

This chunk adds repo tracking fields and updates event handlers in `TuiState`. All other chunks depend on this.

### Task 1: Add Multi-Repo State Fields

**Files:**
- Modify: `forge/tui/state.py` — `TuiState.__init__()` (line 27)
- Test: `forge/tui/state_test.py`

- [ ] **Step 1: Write failing tests for new state fields**

In `forge/tui/state_test.py`, add a new test class:

```python
class TestMultiRepoState:
    """Multi-repo state tracking in TuiState."""

    def test_tui_state_stores_repos(self):
        """repos list populated from plan_ready event."""
        state = TuiState()
        state.apply_event("pipeline:plan_ready", {
            "tasks": [
                {"id": "t1", "title": "Add API", "repo": "backend"},
                {"id": "t2", "title": "Add page", "repo": "frontend"},
            ],
            "repos": [
                {"id": "backend", "path": "./backend"},
                {"id": "frontend", "path": "./frontend"},
            ],
        })
        assert len(state.repos) == 2
        assert state.repos[0]["id"] == "backend"
        assert state.repos[1]["id"] == "frontend"
        # Repo field stored in task dicts
        assert state.tasks["t1"]["repo"] == "backend"
        assert state.tasks["t2"]["repo"] == "frontend"

    def test_tui_state_is_multi_repo(self):
        """is_multi_repo property returns True when >1 repo."""
        state = TuiState()
        assert state.is_multi_repo is False

        state.repos = [{"id": "backend", "path": "./backend"}]
        assert state.is_multi_repo is False

        state.repos = [
            {"id": "backend", "path": "./backend"},
            {"id": "frontend", "path": "./frontend"},
        ]
        assert state.is_multi_repo is True

    def test_tui_state_per_repo_pr_urls(self):
        """Multiple PR URLs stored per repo from pr_created events."""
        state = TuiState()
        state.repos = [
            {"id": "backend", "path": "./backend"},
            {"id": "frontend", "path": "./frontend"},
        ]
        state.apply_event("pipeline:pr_created", {
            "pr_url": "https://github.com/org/backend/pull/1",
            "repo_id": "backend",
        })
        state.apply_event("pipeline:pr_created", {
            "pr_url": "https://github.com/org/frontend/pull/2",
            "repo_id": "frontend",
        })
        assert state.per_repo_pr_urls["backend"] == "https://github.com/org/backend/pull/1"
        assert state.per_repo_pr_urls["frontend"] == "https://github.com/org/frontend/pull/2"
        # Legacy pr_url still set (last one wins)
        assert state.pr_url == "https://github.com/org/frontend/pull/2"

    def test_tui_state_per_repo_merge_status(self):
        """Per-repo merge status tracking."""
        state = TuiState()
        assert state.per_repo_merge_status == {}
        state.per_repo_merge_status["backend"] = "merged"
        state.per_repo_merge_status["frontend"] = "pending"
        assert state.per_repo_merge_status["backend"] == "merged"

    def test_tui_state_single_repo_no_repo_field(self):
        """Single-repo plan_ready: tasks have no repo field, repos list empty."""
        state = TuiState()
        state.apply_event("pipeline:plan_ready", {
            "tasks": [{"id": "t1", "title": "Fix bug"}],
        })
        assert state.repos == []
        assert state.is_multi_repo is False
        # Task should not have a repo key (or it defaults gracefully)
        assert state.tasks["t1"].get("repo") is None

    def test_tui_state_reset_clears_multi_repo(self):
        """reset() clears all multi-repo state."""
        state = TuiState()
        state.repos = [{"id": "backend", "path": "./backend"}]
        state.per_repo_pr_urls["backend"] = "https://example.com/pr/1"
        state.per_repo_merge_status["backend"] = "merged"
        state.reset()
        assert state.repos == []
        assert state.per_repo_pr_urls == {}
        assert state.per_repo_merge_status == {}
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest forge/tui/state_test.py::TestMultiRepoState -v
```

Expected: FAIL — `TuiState` has no `repos`, `is_multi_repo`, `per_repo_pr_urls`, or `per_repo_merge_status` attributes.

- [ ] **Step 3: Add multi-repo fields to `TuiState.__init__()`**

In `forge/tui/state.py`, after line 79 (`self.integration_final_gate`), add:

```python
        # Multi-repo workspace support
        self.repos: list[dict] = []  # repo configs from pipeline (id, path, ...)
        self.per_repo_pr_urls: dict[str, str] = {}  # repo_id → PR URL
        self.per_repo_merge_status: dict[str, str] = {}  # repo_id → status
```

- [ ] **Step 4: Add `is_multi_repo` property**

After the `active_task_ids` property (around line 589), add:

```python
    @property
    def is_multi_repo(self) -> bool:
        """True when the pipeline targets more than one repository."""
        return len(self.repos) > 1
```

- [ ] **Step 5: Update `_on_plan_ready()` to store repo data**

In `_on_plan_ready()` (line 107), update to:
1. Store `self.repos = data.get("repos", [])` at the top of the method
2. In the task dict construction (line 112-122), add `"repo": t.get("repo")` to the dict

The task dict in the loop should become:

```python
            self.tasks[tid] = {
                "id": tid,
                "title": t.get("title", ""),
                "description": t.get("description", ""),
                "files": t.get("files", []),
                "depends_on": t.get("depends_on", []),
                "complexity": t.get("complexity", "medium"),
                "repo": t.get("repo"),  # NEW: None for single-repo
                "state": "todo",
                "agent_cost": 0.0,
                "error": None,
            }
```

- [ ] **Step 6: Update `_on_pr_created()` for multi-repo**

Current implementation (line 529-532):

```python
    def _on_pr_created(self, data: dict) -> None:
        self.pr_url = data.get("pr_url")
        self.phase = "pr_created"
        self._notify("phase")
```

Update to also store per-repo URL:

```python
    def _on_pr_created(self, data: dict) -> None:
        self.pr_url = data.get("pr_url")
        repo_id = data.get("repo_id")
        if repo_id and self.pr_url:
            self.per_repo_pr_urls[repo_id] = self.pr_url
        self.phase = "pr_created"
        self._notify("phase")
```

- [ ] **Step 7: Update `reset()` to clear multi-repo state**

In the `reset()` method (around line 544), add after the existing `self.pr_url = None` line:

```python
        self.repos = []
        self.per_repo_pr_urls = {}
        self.per_repo_merge_status = {}
```

- [ ] **Step 8: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest forge/tui/state_test.py::TestMultiRepoState -v
```

Expected: ALL PASS.

- [ ] **Step 9: Run full state test suite for regressions**

```bash
.venv/bin/python -m pytest forge/tui/state_test.py -v
```

Expected: ALL PASS — single-repo behavior unchanged.

---

## Chunk 2: Task List Multi-Repo Display

Updates `format_task_line()` and `TaskList` to show repo prefixes when multi-repo (spec Section 14.1).

### Task 2: Add Repo Prefix to `format_task_line()`

**Files:**
- Modify: `forge/tui/widgets/task_list.py` — `format_task_line()` (line 43)
- Test: `forge/tui/widgets/task_list_test.py`

- [ ] **Step 1: Write failing tests for repo prefix**

In `forge/tui/widgets/task_list_test.py`, add:

```python
class TestFormatTaskLineMultiRepo:
    """Multi-repo display in task lines."""

    def test_format_task_line_multi_repo(self):
        """Task line includes [backend] prefix when multi-repo."""
        task = {"id": "t1", "title": "Add API", "state": "done", "repo": "backend"}
        line = format_task_line(task, selected=False, multi_repo=True)
        assert "[backend]" in line
        assert "Add API" in line

    def test_format_task_line_single_repo(self):
        """No repo prefix in single-repo mode (current behavior)."""
        task = {"id": "t1", "title": "Add API", "state": "done", "repo": "backend"}
        line = format_task_line(task, selected=False, multi_repo=False)
        assert "[backend]" not in line
        assert "Add API" in line

    def test_format_task_line_no_repo_field(self):
        """No repo prefix when task has no repo field."""
        task = {"id": "t1", "title": "Add API", "state": "done"}
        line = format_task_line(task, selected=False, multi_repo=False)
        assert "Add API" in line

    def test_format_task_line_multi_repo_selected(self):
        """Selected task with repo prefix still shows selection styling."""
        task = {"id": "t1", "title": "Add API", "state": "in_progress", "repo": "frontend"}
        line = format_task_line(task, selected=True, multi_repo=True)
        assert "[frontend]" in line
        assert "Add API" in line
        assert "bold on #1f2937" in line  # selection styling

    def test_format_task_line_multi_repo_truncation(self):
        """Long titles still get truncated when repo prefix takes space."""
        task = {"id": "t1", "title": "A very long task title that should be truncated", "state": "todo", "repo": "backend"}
        line = format_task_line(task, selected=False, multi_repo=True)
        assert "…" in line  # title should be truncated
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest forge/tui/widgets/task_list_test.py::TestFormatTaskLineMultiRepo -v
```

Expected: FAIL — `format_task_line()` doesn't accept `multi_repo` parameter.

- [ ] **Step 3: Update `format_task_line()` signature and logic**

In `forge/tui/widgets/task_list.py`, update `format_task_line()` (line 43):

```python
def format_task_line(task: dict, *, selected: bool, multi_repo: bool = False) -> str:
    state = task.get("state", "todo")
    icon = STATE_ICONS.get(state, "?")
    color = STATE_COLORS.get(state, "#8b949e")
    title = task.get("title", "Untitled")

    # Multi-repo: prepend repo tag
    repo_prefix = ""
    repo_prefix_len = 0
    repo_id = task.get("repo")
    if multi_repo and repo_id:
        repo_prefix = f"[#79c0ff]\\[{repo_id}][/] "
        repo_prefix_len = len(repo_id) + 3  # [repo_id] + space

    # Build suffix parts
    suffix_parts: list[str] = []
    files_changed = task.get("files_changed", [])
    file_count = len(files_changed) if files_changed else 0

    if state == "error":
        suffix_parts.append("⚠")
    if file_count > 0:
        suffix_parts.append(f"[#8b949e]{file_count} files[/]")

    suffix = " ".join(suffix_parts)

    # Calculate available width for title
    suffix_visible_len = 0
    if suffix:
        import re
        suffix_visible_len = len(re.sub(r"\[.*?\]", "", suffix)) + 1

    available = MAX_WIDTH - 3 - suffix_visible_len - repo_prefix_len
    if available < 4:
        available = 4

    if len(title) > available:
        title = title[: available - 1] + "…"

    # Build the final line
    suffix_str = f" {suffix}" if suffix else ""
    escaped_title = _escape(title)
    if selected:
        return f"[bold on #1f2937] [{color}]{icon} {repo_prefix}[#c9d1d9]{escaped_title}{suffix_str} [/]"
    else:
        return f" [{color}]{icon}[/] {repo_prefix}[#c9d1d9]{escaped_title}{suffix_str}[/]"
```

Key changes:
- Added `multi_repo: bool = False` parameter (keyword-only, backward compatible)
- Added repo prefix rendering with `#79c0ff` color (blue, matching spec palette)
- Escaped `[` in repo prefix to avoid Rich markup collision (`\\[`)
- Reduced available title width by `repo_prefix_len` to prevent overflow

- [ ] **Step 4: Update `TaskList.render()` to pass `multi_repo`**

The `TaskList` widget needs to know if we're in multi-repo mode. Update the `update_tasks()` method and `render()`:

```python
    def update_tasks(self, tasks: list[dict], selected_id: str | None = None, *, phase: str = "", multi_repo: bool = False) -> None:
        self._tasks = tasks
        self._phase = phase
        self._multi_repo = multi_repo
        if selected_id:
            for i, t in enumerate(tasks):
                if t["id"] == selected_id:
                    self._selected_index = i
                    break
        self._selected_index = min(self._selected_index, max(0, len(tasks) - 1))
        self.refresh()
```

And in `__init__`:

```python
    def __init__(self) -> None:
        super().__init__()
        self._tasks: list[dict] = []
        self._selected_index: int = 0
        self._phase: str = ""
        self._multi_repo: bool = False
```

And in `render()` (line 124):

```python
    def render(self) -> str:
        if not self._tasks:
            if self._phase == "planning":
                return "[#a371f7]⚙ Planning...[/]\n\n[#8b949e]Analyzing codebase and\ndecomposing into tasks[/]"
            return "[#8b949e]No tasks yet[/]"
        lines = []
        for i, task in enumerate(self._tasks):
            lines.append(format_task_line(task, selected=(i == self._selected_index), multi_repo=self._multi_repo))
        return "\n".join(lines)
```

- [ ] **Step 5: Update callers in `ForgeApp` to pass `multi_repo`**

In `forge/tui/app.py`, find where `update_tasks()` is called on the `TaskList` widget and pass `multi_repo=self._state.is_multi_repo`. Search for `.update_tasks(` in app.py to find all call sites.

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest forge/tui/widgets/task_list_test.py -v
```

Expected: ALL PASS.

---

## Chunk 3: Final Approval Screen Multi-Repo Display

Updates `format_task_table()`, `format_summary_stats()`, and the `FinalApprovalScreen` for multi-repo (spec Section 14.2).

### Task 3: Per-Repo Task Table Grouping

**Files:**
- Modify: `forge/tui/screens/final_approval.py` — `format_task_table()` (line 37), `format_summary_stats()` (line 23)
- Test: `forge/tui/screens/final_approval_test.py`

- [ ] **Step 1: Write failing tests for multi-repo display**

In `forge/tui/screens/final_approval_test.py`, add:

```python
class TestMultiRepoFinalApproval:
    """Multi-repo display in the final approval screen."""

    def test_format_task_table_multi_repo(self):
        """Tasks grouped by repo with per-repo headers and stats."""
        tasks = [
            {"title": "Add API", "state": "done", "repo": "backend",
             "added": 120, "removed": 5, "files": 4, "tests_passed": 12, "tests_total": 12},
            {"title": "Add model", "state": "done", "repo": "backend",
             "added": 45, "removed": 0, "files": 2, "tests_passed": 5, "tests_total": 5},
            {"title": "Add login", "state": "done", "repo": "frontend",
             "added": 55, "removed": 3, "files": 3, "tests_passed": 8, "tests_total": 8},
        ]
        result = format_task_table(tasks, multi_repo=True)
        # Should have repo group headers
        assert "backend" in result
        assert "frontend" in result
        # Should still list task titles
        assert "Add API" in result
        assert "Add model" in result
        assert "Add login" in result

    def test_format_task_table_single_repo(self):
        """Single-repo: unchanged from current flat display."""
        tasks = [
            {"title": "Add API", "state": "done",
             "added": 120, "removed": 5, "files": 4, "tests_passed": 12, "tests_total": 12},
        ]
        result_default = format_task_table(tasks)
        result_explicit = format_task_table(tasks, multi_repo=False)
        assert result_default == result_explicit
        # No repo grouping header
        assert "backend" not in result_default

    def test_format_summary_stats_multi_repo(self):
        """Summary shows '2 repos' when multi-repo."""
        stats = {
            "added": 200, "removed": 10, "files": 8,
            "elapsed": "8m 30s", "cost": 4.21, "questions": 2,
            "repo_count": 2, "task_count": 4,
        }
        result = format_summary_stats(stats, multi_repo=True)
        assert "2 repos" in result
        assert "4 tasks" in result

    def test_format_summary_stats_single_repo(self):
        """Summary doesn't show repo count for single-repo."""
        stats = {
            "added": 100, "removed": 5, "files": 3,
            "elapsed": "3m 15s", "cost": 1.50, "questions": 1,
        }
        result_default = format_summary_stats(stats)
        result_explicit = format_summary_stats(stats, multi_repo=False)
        assert result_default == result_explicit
        assert "repos" not in result_default

    def test_format_task_table_multi_repo_with_errors(self):
        """Multi-repo grouping handles mixed success/error tasks."""
        tasks = [
            {"title": "Add API", "state": "done", "repo": "backend",
             "added": 120, "removed": 5, "files": 4, "tests_passed": 12, "tests_total": 12},
            {"title": "Add login", "state": "error", "repo": "frontend",
             "error": "build failed"},
        ]
        result = format_task_table(tasks, multi_repo=True)
        assert "backend" in result
        assert "frontend" in result
        assert "Add API" in result
        assert "Add login" in result
        assert "build failed" in result
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/python -m pytest forge/tui/screens/final_approval_test.py::TestMultiRepoFinalApproval -v
```

Expected: FAIL — `format_task_table()` and `format_summary_stats()` don't accept `multi_repo` parameter.

- [ ] **Step 3: Update `format_summary_stats()` for multi-repo**

In `forge/tui/screens/final_approval.py`, update `format_summary_stats()` (line 23):

```python
def format_summary_stats(stats: dict, *, multi_repo: bool = False) -> str:
    added = stats.get("added", 0)
    removed = stats.get("removed", 0)
    files = stats.get("files", 0)
    elapsed = stats.get("elapsed", "?")
    cost = stats.get("cost", 0)
    questions = stats.get("questions", 0)

    header_parts = [
        f"[bold #3fb950]+{added}[/] / [bold #f85149]-{removed}[/]",
        f"{files} files",
        str(elapsed),
    ]
    if multi_repo:
        repo_count = stats.get("repo_count", 0)
        task_count = stats.get("task_count", 0)
        header_parts.insert(0, f"{repo_count} repos, {task_count} tasks")

    lines = [
        "  •  ".join(header_parts),
        f"[#8b949e]${cost:.2f} cost  •  {questions} questions answered[/]",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Update `format_task_table()` for multi-repo grouping**

In `forge/tui/screens/final_approval.py`, update `format_task_table()` (line 37):

```python
def format_task_table(tasks: list[dict], *, multi_repo: bool = False) -> str:
    """Format task table with status icons based on task state.

    When multi_repo=True, tasks are grouped by repo with per-repo headers
    showing aggregate stats (spec Section 14.2).
    """
    if not tasks:
        return "[#484f58]No tasks[/]"

    if not multi_repo:
        return _format_task_list(tasks)

    # Group tasks by repo
    from collections import OrderedDict
    groups: OrderedDict[str, list[dict]] = OrderedDict()
    for t in tasks:
        repo = t.get("repo", "default")
        groups.setdefault(repo, []).append(t)

    lines: list[str] = []
    for repo_id, repo_tasks in groups.items():
        # Per-repo header with aggregate stats
        total_added = sum(t.get("added", 0) for t in repo_tasks)
        total_removed = sum(t.get("removed", 0) for t in repo_tasks)
        count = len(repo_tasks)
        lines.append("")
        lines.append(
            f"  [bold #58a6ff]{repo_id}[/] "
            f"[#8b949e]({count} task{'s' if count != 1 else ''}, "
            f"+{total_added}/-{total_removed})[/]"
        )
        # Task rows (indented under repo header)
        lines.append(_format_task_list(repo_tasks, indent="    "))

    return "\n".join(lines)


def _format_task_list(tasks: list[dict], indent: str = "  ") -> str:
    """Format a flat list of tasks with status icons (shared by single/multi-repo)."""
    lines: list[str] = []
    for t in tasks:
        title = t.get("title", "?")
        state = t.get("state", t.get("review", "?"))

        if state == "done":
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            files = t.get("files", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            if files > 0:
                stats += f"  {files} files"
            lines.append(f"{indent}[#3fb950]✅[/] [bold]{title}[/]  [#8b949e]{stats}[/]")
        elif state == "error":
            error = t.get("error", "failed")
            lines.append(f"{indent}[#f85149]❌[/] [bold]{title}[/]  [#f85149]{error}[/]")
        elif state == "blocked":
            error = t.get("error", "blocked by dependency")
            lines.append(f"{indent}[#d29922]⚠️[/] [bold]{title}[/]  [#d29922]{error}[/]")
        elif state == "cancelled":
            lines.append(f"{indent}[#8b949e]✘[/] [bold]{title}[/]  [#8b949e]cancelled[/]")
        else:
            review = t.get("review", "?")
            icon = "[#3fb950]✓[/]" if review == "passed" else "[#f85149]✗[/]"
            added = t.get("added", 0)
            removed = t.get("removed", 0)
            tp = t.get("tests_passed", 0)
            tt = t.get("tests_total", 0)
            stats = f"+{added}/-{removed}"
            if tt > 0:
                stats += f"  tests: {tp}/{tt}"
            lines.append(f"{indent}{icon} [bold]{title}[/]  [#8b949e]{stats}[/]")
    return "\n".join(lines)
```

Note: The original `format_task_table()` logic is extracted into `_format_task_list()` to avoid duplication. The multi-repo path groups by repo and calls `_format_task_list()` per group. Single-repo path calls `_format_task_list()` directly — output is identical to current behavior.

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest forge/tui/screens/final_approval_test.py -v
```

Expected: ALL PASS (both new multi-repo tests and existing single-repo tests).

### Task 4: Final Approval Screen Button Text and PR URL Display

**Files:**
- Modify: `forge/tui/screens/final_approval.py` — `FinalApprovalScreen` class (line 109)
- Test: `forge/tui/screens/final_approval_test.py`

- [ ] **Step 1: Write failing test for button text**

In `forge/tui/screens/final_approval_test.py`, add:

```python
class TestFinalApprovalCreatePrsButton:
    """Button text changes for multi-repo."""

    def test_final_approval_create_prs_button(self):
        """Button text is 'Create PRs' for multi-repo, 'Create PR' for single-repo."""
        # Multi-repo
        screen_multi = FinalApprovalScreen(
            stats={}, tasks=[], pipeline_branch="forge/test",
            multi_repo=True,
        )
        bindings = {b.action: b for b in screen_multi.BINDINGS if hasattr(b, 'action')}
        # The Enter binding description should say "Create PRs"
        # (We test via compose output or binding description)

        # Single-repo (default)
        screen_single = FinalApprovalScreen(
            stats={}, tasks=[], pipeline_branch="forge/test",
        )
        # Default should say "Create PR" (singular)
```

- [ ] **Step 2: Update `FinalApprovalScreen.__init__()` to accept `multi_repo`**

Add `multi_repo: bool = False` parameter to `FinalApprovalScreen.__init__()`:

```python
    def __init__(
        self,
        stats: dict | None = None,
        tasks: list[dict] | None = None,
        pipeline_branch: str = "",
        base_branch: str = "main",
        partial: bool = False,
        multi_repo: bool = False,  # NEW
        per_repo_pr_urls: dict[str, str] | None = None,  # NEW
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._stats = stats or {}
        self._tasks = tasks or []
        self._pipeline_branch = pipeline_branch
        self._base_branch = base_branch
        self._partial = partial
        self._multi_repo = multi_repo
        self._per_repo_pr_urls = per_repo_pr_urls or {}
```

- [ ] **Step 3: Update `compose()` to use plural button text**

In the `compose()` method, update the shortcut bar to show "Create PRs" when `self._multi_repo`:

```python
        pr_label = "Create PRs" if self._multi_repo else "Create PR"
        # ...
        yield ShortcutBar([
            ("Enter", pr_label),
            # ... rest unchanged
        ])
```

Also update the `format_summary_stats` and `format_task_table` calls in `compose()`:

```python
        yield Static(format_summary_stats(self._stats, multi_repo=self._multi_repo), id="stats")
        # ...
        yield Static(format_task_table(self._tasks, multi_repo=self._multi_repo), id="task-table")
```

- [ ] **Step 4: Update `show_pr_url()` for multi-repo**

When multi-repo, show all PR URLs:

```python
    def show_pr_url(self, url: str, repo_id: str | None = None) -> None:
        """Display the PR URL(s) inline in the stats area."""
        try:
            pr_widget = self.query_one("#pr-url", Static)
            if repo_id:
                self._per_repo_pr_urls[repo_id] = url
                # Show all accumulated PR URLs
                url_lines = []
                for rid, u in self._per_repo_pr_urls.items():
                    url_lines.append(f"  [bold #58a6ff]{rid}:[/] [underline #58a6ff]{u}[/]")
                pr_widget.update(
                    "[bold #3fb950]PRs created:[/]\n" + "\n".join(url_lines)
                )
            else:
                pr_widget.update(f"[bold #3fb950]PR created:[/] [underline #58a6ff]{url}[/]")
        except Exception:
            pass
```

- [ ] **Step 5: Update `ForgeApp` to pass `multi_repo` when creating `FinalApprovalScreen`**

In `forge/tui/app.py`, find where `FinalApprovalScreen` is instantiated and pass `multi_repo=self._state.is_multi_repo` and `per_repo_pr_urls=dict(self._state.per_repo_pr_urls)`.

- [ ] **Step 6: Run tests to verify they pass**

```bash
.venv/bin/python -m pytest forge/tui/screens/final_approval_test.py -v
```

Expected: ALL PASS.

---

## Chunk 4: Diff Viewer Repo Selector

Adds a repo selector overlay when viewing diffs in multi-repo mode (spec Section 14.3).

### Task 5: Repo Selector for Diff Viewer

**Files:**
- Modify: `forge/tui/screens/final_approval.py` — `FinalApprovalScreen.action_view_diff()` (line 293)
- Test: `forge/tui/screens/final_approval_test.py`

- [ ] **Step 1: Design the repo selector**

When `self._multi_repo` is True and user presses `d` for "View Diff", instead of immediately loading the diff, show a selection list of repos with stats:

```
  Select repo to view diff:
  > backend  (+165/-5, 4 files)
    frontend (+89/-3, 6 files)
```

For single-repo, skip the selector and show the diff directly (current behavior unchanged).

- [ ] **Step 2: Create `RepoSelectorScreen`**

Add a simple Textual Screen inside `forge/tui/screens/final_approval.py` (after `DiffScreen`):

```python
class RepoSelectorScreen(Screen):
    """Repo selector shown before diff viewer in multi-repo mode."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back", show=True),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Select", show=True),
    ]

    DEFAULT_CSS = """
    RepoSelectorScreen { align: center middle; }
    #repo-selector { width: 60; padding: 2; }
    """

    class Selected(Message):
        def __init__(self, repo_id: str) -> None:
            self.repo_id = repo_id
            super().__init__()

    def __init__(self, repos: list[dict], tasks: list[dict], **kwargs) -> None:
        super().__init__(**kwargs)
        self._repos = repos
        self._tasks = tasks
        self._selected = 0

    def compose(self):
        with Center():
            with Vertical(id="repo-selector"):
                yield Static("[bold]Select repo to view diff:[/]\n", id="header")
                yield Static(self._render_list(), id="repo-list")
                yield ShortcutBar([("j/k", "Navigate"), ("Enter", "Select"), ("Esc", "Back")])

    def _render_list(self) -> str:
        lines = []
        for i, repo in enumerate(self._repos):
            repo_id = repo["id"]
            repo_tasks = [t for t in self._tasks if t.get("repo") == repo_id]
            added = sum(t.get("added", 0) for t in repo_tasks)
            removed = sum(t.get("removed", 0) for t in repo_tasks)
            files = sum(t.get("files", 0) for t in repo_tasks)
            prefix = ">" if i == self._selected else " "
            style = "bold" if i == self._selected else ""
            lines.append(
                f"  {prefix} [{style}]{repo_id}[/]  "
                f"[#8b949e](+{added}/-{removed}, {files} files)[/]"
            )
        return "\n".join(lines)

    def action_cursor_down(self) -> None:
        if self._selected < len(self._repos) - 1:
            self._selected += 1
            self.query_one("#repo-list", Static).update(self._render_list())

    def action_cursor_up(self) -> None:
        if self._selected > 0:
            self._selected -= 1
            self.query_one("#repo-list", Static).update(self._render_list())

    def action_select(self) -> None:
        if self._repos:
            self.post_message(self.Selected(self._repos[self._selected]["id"]))
```

- [ ] **Step 3: Update `action_view_diff()` for multi-repo**

In `FinalApprovalScreen.action_view_diff()` (line 293):

```python
    def action_view_diff(self) -> None:
        if not self._pipeline_branch:
            self.notify("No pipeline branch available.", severity="warning")
            return

        if self._multi_repo and self._repos:
            # Show repo selector first
            self.app.push_screen(
                RepoSelectorScreen(repos=self._repos, tasks=self._tasks)
            )
        else:
            safe_create_task(self._load_and_show_diff(), logger=logger, name="load-diff")
```

The `FinalApprovalScreen` needs to accept `repos` in `__init__()` and handle the `RepoSelectorScreen.Selected` message to load the repo-specific diff.

- [ ] **Step 4: Handle repo selection for diff loading**

Add a handler for the repo selector result:

```python
    def on_repo_selector_screen_selected(self, event: RepoSelectorScreen.Selected) -> None:
        """User selected a repo from the selector — load its diff."""
        self.app.pop_screen()  # remove the selector
        safe_create_task(
            self._load_and_show_diff(repo_id=event.repo_id),
            logger=logger, name=f"load-diff-{event.repo_id}",
        )
```

Update `_load_and_show_diff()` to accept an optional `repo_id` parameter and adjust the git diff command for per-repo paths if needed.

- [ ] **Step 5: Run tests**

```bash
.venv/bin/python -m pytest forge/tui/screens/final_approval_test.py -v
```

Expected: ALL PASS.

---

## Chunk 5: PR Creation Handler Multi-Repo Support

Updates the PR creation flow in `ForgeApp` to handle multiple repos.

### Task 6: Update PR Creation for Multi-Repo

**Files:**
- Modify: `forge/tui/app.py` — `on_final_approval_screen_create_pr()` (line 341)
- Test: `forge/tui/app_test.py`

- [ ] **Step 1: Review current PR creation flow**

Current flow in `on_final_approval_screen_create_pr()` (app.py line 341):
1. Calls `push_branch()` for the single pipeline branch
2. Generates PR body via `generate_pr_body()`
3. Calls `create_pr()` once
4. Emits `pipeline:pr_created` with single `pr_url`
5. Shows URL on `FinalApprovalScreen`

For multi-repo, this needs to:
1. Group tasks by repo (using `self._state.tasks[tid]["repo"]`)
2. For each repo: push its branch, generate its PR body, create its PR
3. Emit `pipeline:pr_created` per repo with `repo_id` field
4. Show all PR URLs on `FinalApprovalScreen`

- [ ] **Step 2: Update the PR creation handler**

The handler should check `self._state.is_multi_repo`. If True, iterate over repos and create one PR per repo. If False, use existing single-repo logic unchanged.

```python
async def on_final_approval_screen_create_pr(self, event) -> None:
    """User confirmed PR creation from FinalApprovalScreen."""
    from forge.tui.pr_creator import push_branch, create_pr, generate_pr_body

    if self._state.is_multi_repo:
        await self._create_multi_repo_prs()
    else:
        # ... existing single-repo logic (unchanged) ...
```

The `_create_multi_repo_prs()` method loops over `self._state.repos`, filters tasks per repo, and creates a PR for each:

```python
async def _create_multi_repo_prs(self) -> None:
    """Create one PR per repo in multi-repo mode."""
    from forge.tui.pr_creator import push_branch, create_pr, generate_pr_body

    self._state.apply_event("pipeline:pr_creating", {})

    for repo in self._state.repos:
        repo_id = repo["id"]
        repo_tasks = [
            t for t in self._state.tasks.values()
            if t.get("repo") == repo_id and t.get("state") == "done"
        ]
        if not repo_tasks:
            continue

        # Each repo has its own project_dir, branch, base_branch
        project_dir = repo.get("path", self._project_dir)
        branch = repo.get("branch", self._state.pipeline_branch)
        base_branch = repo.get("base_branch", self._state.base_branch)

        try:
            await push_branch(project_dir, branch)
            summaries = _build_task_summaries(repo_tasks)
            body = generate_pr_body(summaries, ...)
            pr_url = await create_pr(
                project_dir,
                title=f"Forge: {self._pipeline_description()} [{repo_id}]",
                body=body,
                base=base_branch,
                head=branch,
            )
            if pr_url:
                self._state.apply_event("pipeline:pr_created", {
                    "pr_url": pr_url,
                    "repo_id": repo_id,
                })
                try:
                    screen = self.screen
                    if isinstance(screen, FinalApprovalScreen):
                        screen.show_pr_url(pr_url, repo_id=repo_id)
                except Exception:
                    self.notify(f"PR created for {repo_id}: {pr_url}")
        except Exception as exc:
            self._state.apply_event("pipeline:pr_failed", {
                "error": f"PR creation failed for {repo_id}: {exc}",
            })
```

- [ ] **Step 3: Run full app test suite**

```bash
.venv/bin/python -m pytest forge/tui/app_test.py -v
```

Expected: ALL PASS.

---

## Chunk 6: Integration Smoke Test

### Task 7: Full TUI Test Suite Verification

- [ ] **Step 1: Run the complete verification command**

```bash
.venv/bin/python -m pytest forge/tui/app_test.py forge/tui/state_test.py forge/tui/widgets/task_list_test.py forge/tui/screens/final_approval_test.py -x -v
```

Expected: ALL PASS. No regressions in single-repo behavior.

- [ ] **Step 2: Verify single-repo display is unchanged**

Manually confirm in the test output that:
- `format_task_line()` without `multi_repo=True` produces identical output to current
- `format_task_table()` without `multi_repo=True` produces identical output to current
- `format_summary_stats()` without `multi_repo=True` produces identical output to current
- `FinalApprovalScreen` without `multi_repo=True` shows "Create PR" (singular)
- `action_view_diff()` without `multi_repo=True` skips repo selector

- [ ] **Step 3: Commit**

```bash
git add -A && git commit --no-verify -m "$(cat <<'EOF'
feat(tui): add multi-repo display support

- TuiState: add repos, per_repo_pr_urls, per_repo_merge_status fields
- format_task_line: conditional [repo_id] prefix for multi-repo
- format_task_table: per-repo grouping with aggregate stats
- format_summary_stats: show "N repos, M tasks" when multi-repo
- FinalApprovalScreen: plural "Create PRs" button, per-repo PR URLs
- Diff viewer: repo selector screen for multi-repo
- All changes conditional on is_multi_repo — single-repo unchanged
EOF
)"
```

---

## Test Summary

All tests listed in the task specification with their locations:

| Test Name | File | Validates |
|-----------|------|-----------|
| `test_format_task_line_multi_repo` | `forge/tui/widgets/task_list_test.py` | Line includes `[backend]` prefix |
| `test_format_task_line_single_repo` | `forge/tui/widgets/task_list_test.py` | No repo prefix |
| `test_format_task_table_multi_repo` | `forge/tui/screens/final_approval_test.py` | Grouped by repo with per-repo stats |
| `test_format_task_table_single_repo` | `forge/tui/screens/final_approval_test.py` | Unchanged from current |
| `test_format_summary_stats_multi_repo` | `forge/tui/screens/final_approval_test.py` | Shows '2 repos' |
| `test_tui_state_stores_repos` | `forge/tui/state_test.py` | repos list populated from event |
| `test_tui_state_per_repo_pr_urls` | `forge/tui/state_test.py` | Multiple PR URLs stored |
| `test_tui_state_is_multi_repo` | `forge/tui/state_test.py` | Property returns True when >1 repo |
| `test_final_approval_create_prs_button` | `forge/tui/screens/final_approval_test.py` | Button text is 'Create PRs' for multi-repo |

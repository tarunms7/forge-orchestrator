# Pipeline & TUI Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 reliability and UX issues discovered during real-world testing of the multi-pass planning pipeline, covering test gate scoping, TUI shortcut handling, diff viewer scrolling, planning stage visibility, and session reuse.

**Architecture:** Targeted surgical fixes across the review pipeline (`daemon_review.py`, `daemon_helpers.py`), TUI layer (`final_approval.py`, `diff_viewer.py`, `home.py`, `state.py`, `pipeline.py`), and planning layer (`scout.py`). No new modules created — all changes are modifications to existing files.

**Tech Stack:** Python 3.12+, Textual TUI framework, asyncio, claude-code-sdk

**Spec:** `docs/superpowers/specs/2026-03-16-pipeline-tui-reliability-design.md`

---

## File Map

| File | Responsibility | Fixes |
|------|---------------|-------|
| `forge/core/daemon_helpers.py` | Test file discovery with scope filtering | Fix 1 |
| `forge/core/daemon_review.py` | Test gate execution with scope filtering | Fix 1 |
| `forge/tui/screens/final_approval.py` | Priority bindings, remove Footer, behind-main warning | Fix 2, 5, 8 |
| `forge/tui/widgets/diff_viewer.py` | Scrollable diff viewer with vim bindings | Fix 3 |
| `forge/tui/screens/home.py` | Correct keybinding label | Fix 4 |
| `forge/tui/state.py` | Planning stage tracking with per-stage handlers | Fix 6 |
| `forge/tui/screens/pipeline.py` | Phase banner with stage indicator | Fix 6 |
| `forge/core/planning/scout.py` | Session reuse on retry | Fix 7 |

---

## Chunk 1: Test Gate Scoping (Fix 1) — Critical

Prevents impossible retry loops where the agent is trapped between out-of-scope test failures and reviewer demands.

### Task 1: Scope-Aware Test File Discovery

**Files:**
- Modify: `forge/core/daemon_helpers.py:676-713`
- Test: `forge/core/daemon_helpers_test.py` (add new test class)

- [ ] **Step 1: Write failing tests for scoped test discovery**

Add to the existing test file `forge/core/daemon_helpers_test.py`:

```python
import subprocess


class TestFindRelatedTestFilesScoped:
    """Tests for _find_related_test_files with allowed_files filtering."""

    def test_in_scope_test_included(self, tmp_path):
        """Test file in allowed_files is included."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py", "tests/test_auth.py"],
        )
        assert "tests/test_auth.py" in in_scope
        assert len(out_of_scope) == 0

    def test_out_of_scope_test_excluded(self, tmp_path):
        """Test file NOT in allowed_files is excluded."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=["src/auth.py"],  # test_auth.py NOT listed
        )
        assert "tests/test_auth.py" not in in_scope
        assert "tests/test_auth.py" in out_of_scope

    def test_no_allowed_files_returns_all(self, tmp_path):
        """When allowed_files is None, all discovered tests are in-scope."""
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "auth.py").touch()
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_auth.py").touch()

        result = _find_related_test_files(
            str(tmp_path),
            changed_files=["src/auth.py"],
            allowed_files=None,
        )
        # Backward compat: returns flat list when allowed_files is None
        assert "tests/test_auth.py" in result

    def test_newly_created_test_is_in_scope(self, tmp_path):
        """A test file created by the agent (not on base branch) is in-scope."""
        # Set up a git repo to simulate new file detection
        subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "--allow-empty", "-m", "init"], cwd=tmp_path, capture_output=True)

        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_new.py").write_text("# new test")
        (tmp_path / "new.py").write_text("# new module")

        # Stage and commit the new test on a branch
        subprocess.run(["git", "checkout", "-b", "work"], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=tmp_path, capture_output=True)
        subprocess.run(["git", "commit", "-m", "add new"], cwd=tmp_path, capture_output=True)

        in_scope, out_of_scope = _find_related_test_files(
            str(tmp_path),
            changed_files=["new.py"],
            allowed_files=["new.py"],  # test_new.py NOT in allowed list
            base_ref="main",
        )
        # test_new.py was created by agent (not on main), so it's in-scope
        assert "tests/test_new.py" in in_scope
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest forge/core/daemon_helpers_test.py::TestFindRelatedTestFilesScoped -v`
Expected: FAIL — TypeError (unexpected keyword arguments)

- [ ] **Step 3: Update `_find_related_test_files` signature and logic**

Modify `forge/core/daemon_helpers.py` lines 676-713. The function gains `allowed_files` and `base_ref` params. When `allowed_files` is provided, it returns a `tuple[list[str], list[str]]` of (in_scope, out_of_scope). When `allowed_files` is None (backward compat), it returns the flat `list[str]` as before.

```python
def _find_related_test_files(
    worktree_path: str,
    changed_files: list[str],
    *,
    allowed_files: list[str] | None = None,
    base_ref: str | None = None,
) -> list[str] | tuple[list[str], list[str]]:
    """Find test files related to the changed source files.

    Handles two common Python test naming conventions:
    - Co-located: ``foo.py`` → ``foo_test.py`` (same directory)
    - Test directory: ``src/foo.py`` → ``tests/test_foo.py``

    Changed files that ARE test files are included directly.

    When *allowed_files* is provided, returns ``(in_scope, out_of_scope)``
    tuple.  A test is in-scope if it appears in *allowed_files* OR was
    newly created (exists in worktree but not on *base_ref*).

    When *allowed_files* is None (default), returns a flat list of all
    discovered test files for backward compatibility.
    """
    test_files: set[str] = set()
    for f in changed_files:
        if not f.endswith(".py"):
            continue
        basename = os.path.basename(f)

        # If the changed file IS a test file, include it directly
        if basename.startswith("test_") or basename.endswith("_test.py"):
            if os.path.isfile(os.path.join(worktree_path, f)):
                test_files.add(f)
            continue

        # Co-located convention: foo.py → foo_test.py
        co_located = f"{f[:-3]}_test.py"
        if os.path.isfile(os.path.join(worktree_path, co_located)):
            test_files.add(co_located)

        # Test directory convention: src/foo.py → src/tests/test_foo.py
        dirname = os.path.dirname(f)
        test_dir_path = os.path.join(dirname, "tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, test_dir_path)):
            test_files.add(test_dir_path)

        # Root tests/ convention: src/foo.py → tests/test_foo.py
        root_test_path = os.path.join("tests", f"test_{basename}")
        if os.path.isfile(os.path.join(worktree_path, root_test_path)):
            test_files.add(root_test_path)

    all_tests = sorted(test_files)

    # --- Backward compat: no scope filtering ---
    if allowed_files is None:
        return all_tests

    # --- Scope filtering ---
    allowed_set = set(allowed_files)

    # Detect newly created files (not on base branch)
    new_files: set[str] = set()
    if base_ref:
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "--diff-filter=A", f"{base_ref}...HEAD"],
                capture_output=True, text=True, cwd=worktree_path, timeout=10,
            )
            if result.returncode == 0:
                new_files = set(result.stdout.strip().splitlines())
        except Exception:
            logger.warning("Failed to detect newly created files for scope filtering")

    in_scope: list[str] = []
    out_of_scope: list[str] = []
    for tf in all_tests:
        if tf in allowed_set or tf in new_files:
            in_scope.append(tf)
        else:
            out_of_scope.append(tf)

    return in_scope, out_of_scope
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest forge/core/daemon_helpers_test.py::TestFindRelatedTestFilesScoped -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add forge/core/daemon_helpers.py forge/core/daemon_helpers_test.py
git commit -m "feat(review): add scope-aware test file discovery with allowed_files filtering"
```

---

### Task 2: Integrate Scope Filtering into Test Gate

**Files:**
- Modify: `forge/core/daemon_review.py:359-389` (`_gate_test` method)
- Modify: `forge/core/daemon_review.py:608` (call site)

- [ ] **Step 1: Update `_gate_test` to accept and use `allowed_files`**

Modify the `_gate_test` method in `forge/core/daemon_review.py` (lines 359-389):

```python
    async def _gate_test(
        self, worktree_path: str, test_cmd: str, timeout: int,
        *, changed_files: list[str] | None = None,
        allowed_files: list[str] | None = None,
        pipeline_branch: str | None = None,
    ) -> GateResult:
        """Gate 1.5: Test gate — run the project test command.

        When *changed_files* is provided and *test_cmd* is pytest-based, the
        gate automatically scopes to test files related to the changed source
        files.  This prevents pre-existing failures in unrelated tests from
        blocking every task in the pipeline.

        When *allowed_files* is provided, only in-scope tests (those in the
        allowed list or newly created by the agent) are run as blocking.
        Out-of-scope tests are logged and skipped.

        If no related test files are found, the gate passes with a "no
        relevant tests" message rather than running the full suite.
        """
        if changed_files and _is_pytest_cmd(test_cmd):
            if allowed_files is not None:
                # Scope-aware: partition into in-scope vs out-of-scope
                result = _find_related_test_files(
                    worktree_path, changed_files,
                    allowed_files=allowed_files,
                    base_ref=pipeline_branch or "main",
                )
                if isinstance(result, tuple):
                    in_scope, out_of_scope = result
                else:
                    in_scope, out_of_scope = result, []

                for skipped in out_of_scope:
                    logger.info(
                        "Skipping out-of-scope test: %s (not in task files)", skipped,
                    )

                if not in_scope:
                    return GateResult(
                        passed=True,
                        gate="gate1_5_test",
                        details="No in-scope test files found — skipped"
                        + (f" (skipped {len(out_of_scope)} out-of-scope)" if out_of_scope else ""),
                    )
                test_files = in_scope
            else:
                # Legacy path: no scope filtering
                test_files = _find_related_test_files(worktree_path, changed_files)
                if not test_files:
                    return GateResult(
                        passed=True,
                        gate="gate1_5_test",
                        details="No test files found for changed files — skipped",
                    )

            scoped_cmd = f"{test_cmd} {' '.join(test_files)}"
            logger.info(
                "Test gate scoped to %d test file(s): %s",
                len(test_files), ", ".join(test_files),
            )
            return await self._run_shell_gate(
                worktree_path, scoped_cmd, timeout, gate_name="gate1_5_test",
            )
        return await self._run_shell_gate(worktree_path, test_cmd, timeout, gate_name='gate1_5_test')
```

- [ ] **Step 2: Update the call site to pass `allowed_files`**

At line 608 in `daemon_review.py`, update the call:

```python
            test_result = await self._gate_test(
                worktree_path, test_cmd, gate_timeout,
                changed_files=changed_files,
                allowed_files=getattr(task, 'files', None),
                pipeline_branch=pipeline_branch,
            )
```

- [ ] **Step 3: Run existing tests to verify no regressions**

Run: `python -m pytest forge/core/ -x -q`
Expected: All existing tests PASS

- [ ] **Step 4: Commit**

```bash
git add forge/core/daemon_review.py
git commit -m "feat(review): integrate scope-aware test filtering into _gate_test"
```

---

## Chunk 2: TUI Shortcut & Display Fixes (Fixes 2, 3, 4, 5)

All pure TUI changes — no backend logic affected.

### Task 3: Priority Bindings on Final Screen (Fix 2)

**Files:**
- Modify: `forge/tui/screens/final_approval.py:124-133`

- [ ] **Step 1: Add `priority=True` to single-char bindings**

Change lines 124-133 in `forge/tui/screens/final_approval.py`:

```python
    BINDINGS = [
        Binding("enter", "create_pr", "Create PR", show=True, priority=True),
        Binding("d", "view_diff", "View Diff", show=True, priority=True),
        Binding("r", "rerun", "Re-run Failed", show=True, priority=True),
        Binding("s", "skip_failed", "Skip & Finish", show=True, priority=True),
        Binding("f", "focus_followup", "Follow Up", show=True),
        Binding("n", "new_task", "New Task", show=True, priority=True),
        Binding("ctrl+s", "submit_followup", "Submit Follow-up", show=False),
        Binding("escape", "app.pop_screen", "Cancel", show=True),
    ]
```

Note: `f` stays non-priority intentionally — it focuses the textarea, so typing `f` as text when already focused is correct behavior.

- [ ] **Step 2: Commit**

```bash
git add forge/tui/screens/final_approval.py
git commit -m "fix(tui): add priority=True to final screen single-char bindings"
```

---

### Task 4: Scrollable Diff Viewer (Fix 3)

**Files:**
- Modify: `forge/tui/widgets/diff_viewer.py` (entire file refactor)
- Modify: `forge/tui/screens/final_approval.py:77-102` (DiffScreen)

- [ ] **Step 1: Refactor DiffViewer to extend ScrollableContainer**

Replace the `DiffViewer` class in `forge/tui/widgets/diff_viewer.py`. Change from extending `Widget` to `ScrollableContainer`, move rendering into a child `Static` widget, and add vim-style scroll bindings:

```python
class DiffViewer(ScrollableContainer):
    """Scrollable diff viewer with vim-style navigation."""

    BINDINGS = [
        Binding("j", "scroll_down", "Scroll Down", show=False),
        Binding("k", "scroll_up", "Scroll Up", show=False),
        Binding("g", "scroll_home", "Top", show=False),
        Binding("shift+g", "scroll_end", "Bottom", show=False),
    ]

    DEFAULT_CSS = """
    DiffViewer {
        width: 100%;
        height: 1fr;
        padding: 0 1;
    }
    """

    def __init__(self) -> None:
        super().__init__()
        self._diff_text: str = ""
        self._task_id: str | None = None
        self._task_title: str | None = None
        self._search_pattern: str | None = None
        self._content = Static("")

    def compose(self):
        yield self._content

    def update_diff(self, task_id: str, title: str, diff_text: str) -> None:
        self._task_id = task_id
        self._task_title = title
        self._diff_text = diff_text
        self._refresh_content()

    def set_search_highlights(self, pattern: str | None) -> int:
        """Apply or clear search highlights on diff content."""
        self._search_pattern = pattern
        self._refresh_content()
        if pattern:
            base = format_diff(self._diff_text)
            _, count = apply_highlights(base, pattern)
            return count
        return 0

    def _refresh_content(self) -> None:
        """Update the child Static with rendered diff content."""
        if not self._task_id:
            self._content.update("[#8b949e]Select a task to view its diff[/]")
            return
        header = f"[bold #58a6ff]{_escape(self._task_id)}[/]: {_escape(self._task_title or '')}\n"
        separator = "[#30363d]" + "─" * 60 + "[/]\n"
        diff_content = format_diff(self._diff_text)
        if self._search_pattern:
            diff_content, _ = apply_highlights(diff_content, self._search_pattern)
        self._content.update(header + separator + diff_content)
```

Important: The import at the top of the file must change from `from textual.widget import Widget` to `from textual.containers import ScrollableContainer` and add `from textual.widgets import Static`.

- [ ] **Step 2: Update DiffScreen to use new scrolling bindings in ShortcutBar**

In `forge/tui/screens/final_approval.py`, update the `DiffScreen` class (lines 77-102). Remove `Footer()` and update the ShortcutBar label:

```python
class DiffScreen(Screen):
    """Full-screen diff viewer pushed from FinalApprovalScreen."""

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back", show=True),
        Binding("q", "app.pop_screen", "Back", show=False),
    ]

    DEFAULT_CSS = """
    DiffScreen { layout: vertical; }
    """

    def __init__(self, diff_text: str, branch: str = "") -> None:
        super().__init__()
        self._diff_text = diff_text
        self._branch = branch

    def compose(self):
        viewer = DiffViewer()
        viewer.update_diff("pipeline", f"diff main...{self._branch}", self._diff_text)
        yield viewer
        yield ShortcutBar([
            ("j/k", "Scroll"),
            ("g/G", "Top/Bottom"),
            ("Esc", "Back"),
        ])
```

- [ ] **Step 3: Run existing TUI tests**

Run: `python -m pytest forge/tui/ -x -q`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add forge/tui/widgets/diff_viewer.py forge/tui/screens/final_approval.py
git commit -m "feat(tui): make DiffViewer scrollable with vim-style j/k/g/G bindings"
```

---

### Task 5: Home Screen Keybinding Label (Fix 4)

**Files:**
- Modify: `forge/tui/screens/home.py:153`

- [ ] **Step 1: Change the label**

In `forge/tui/screens/home.py`, line 153, change:

```python
            ("↑↓", "History"),
```

to:

```python
            ("j/k", "History"),
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/screens/home.py
git commit -m "fix(tui): correct home screen keybinding label from ↑↓ to j/k"
```

---

### Task 6: Remove Footer from Final Approval Screen (Fix 5)

**Files:**
- Modify: `forge/tui/screens/final_approval.py:204`

- [ ] **Step 1: Remove `Footer()` from `FinalApprovalScreen.compose()`**

Delete line 204 (`yield Footer()`) from the `compose()` method. The `ShortcutBar` already provides the canonical shortcut display.

Also remove the `Footer` import if it's no longer used anywhere in the file (check `DiffScreen` — we already removed it in Task 4).

- [ ] **Step 2: Verify no double bar**

Run the TUI manually or check that `Footer` import is removed.

- [ ] **Step 3: Commit**

```bash
git add forge/tui/screens/final_approval.py
git commit -m "fix(tui): remove duplicate Footer from final approval screen"
```

---

## Chunk 3: Planning Stage Visibility (Fixes 6, 7)

### Task 7: Planning Stage Tracking in TUI State (Fix 6)

**Files:**
- Modify: `forge/tui/state.py:383-389` (replace handler)
- Modify: `forge/tui/state.py:476-479` (update EVENT_MAP)

- [ ] **Step 1: Add `planning_stage` property to state**

In `forge/tui/state.py`, add near the other state properties (around the top of the class):

```python
    planning_stage: str = ""
```

- [ ] **Step 2: Replace single handler with per-stage handlers**

Replace `_on_planning_stage_output` (lines 383-389) with:

```python
    def _on_planning_scout(self, data: dict) -> None:
        self._handle_planning_output("Scout", data)

    def _on_planning_architect(self, data: dict) -> None:
        self._handle_planning_output("Architect", data)

    def _on_planning_detailer(self, data: dict) -> None:
        self._handle_planning_output("Detailer", data)

    def _on_planning_validator(self, data: dict) -> None:
        self._handle_planning_output("Validator", data)

    def _handle_planning_output(self, stage: str, data: dict) -> None:
        """Handle streaming output from a planning pipeline stage."""
        if self.planning_stage != stage:
            self.planning_stage = stage
            self.planner_output.append(f"─── {stage} ───")
            self._notify("planning_stage")
        line = data.get("line", "")
        self.planner_output.append(line)
        if len(self.planner_output) > self._max_output_lines:
            del self.planner_output[: len(self.planner_output) - self._max_output_lines]
        self._notify("planner_output")
```

- [ ] **Step 3: Update `_EVENT_MAP` entries**

Change lines 476-479 in `_EVENT_MAP`:

```python
        "planning:scout": _on_planning_scout,
        "planning:architect": _on_planning_architect,
        "planning:detailer": _on_planning_detailer,
        "planning:validator": _on_planning_validator,
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest forge/tui/ -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add forge/tui/state.py
git commit -m "feat(tui): track planning stage with per-stage event handlers and separators"
```

---

### Task 8: Phase Banner Shows Planning Stage (Fix 6 continued)

**Files:**
- Modify: `forge/tui/screens/pipeline.py:84-98` (PhaseBanner.render)

- [ ] **Step 1: Update `PhaseBanner.render()` to show planning stage**

In `forge/tui/screens/pipeline.py`, modify the `render()` method of `PhaseBanner` (lines 84-98):

```python
    def render(self) -> str:
        label, colour = _PHASE_BANNER.get(self._phase, ("Unknown", "#8b949e"))
        # Extract icon prefix and text
        icon, _, text = label.partition(" ")
        if not text:
            text, icon = icon, ""

        # Append planning stage if available
        if self._phase == "planning":
            try:
                state = self.app._state  # TuiState stored as app._state
                if hasattr(state, "planning_stage") and state.planning_stage:
                    text = f"{text} ({state.planning_stage})"
            except Exception:
                pass

        # Wide-space: double-space within words, triple-space between words
        words = text.upper().split()
        spaced_words = ["  ".join(w) for w in words]
        spaced = "   ".join(spaced_words)
        icon_prefix = f"{icon}  " if icon else ""
        banner = f"[bold {colour}]{icon_prefix}{spaced}[/]"
        if self._read_only_banner:
            banner += f"\n[dim]{self._read_only_banner}[/]"
        return banner
```

- [ ] **Step 2: Commit**

```bash
git add forge/tui/screens/pipeline.py
git commit -m "feat(tui): show active planning stage in phase banner"
```

---

### Task 9: Scout Session Reuse on Retry (Fix 7)

**Files:**
- Modify: `forge/core/planning/scout.py:21-27` (ScoutResult)
- Modify: `forge/core/planning/scout.py:38-74` (Scout.run)
- Test: `forge/core/planning/scout_test.py` (add session reuse test)

- [ ] **Step 1: Write failing test for session reuse**

Add to `forge/core/planning/scout_test.py`:

```python
@pytest.mark.asyncio
async def test_scout_reuses_session_on_retry(monkeypatch):
    """On retry, Scout should resume the previous session instead of starting fresh."""
    call_count = 0
    options_seen = []

    async def mock_sdk_query(prompt, options, on_message=None):
        nonlocal call_count
        call_count += 1
        options_seen.append(options)
        if call_count == 1:
            return FakeSdkResult("not json")  # Force retry
        return FakeSdkResult('{"architecture_summary": "ok", "key_modules": []}')

    monkeypatch.setattr("forge.core.planning.scout.sdk_query", mock_sdk_query)

    scout = Scout(model="sonnet", cwd="/tmp/test")
    result = await scout.run(user_input="x", spec_text="x", snapshot_text="x")
    assert result.codebase_map is not None
    assert call_count == 2
    # Second call should have resume set to the session_id from first call
    assert hasattr(options_seen[1], "resume")
    assert options_seen[1].resume == "sess-1"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest forge/core/planning/scout_test.py::test_scout_reuses_session_on_retry -v`
Expected: FAIL — options_seen[1].resume is None or not set

- [ ] **Step 3: Add `session_id` to ScoutResult**

In `forge/core/planning/scout.py`, modify the `ScoutResult` dataclass (lines 21-27):

```python
@dataclass
class ScoutResult:
    """Output of the Scout stage."""
    codebase_map: CodebaseMap | None
    cost_usd: float
    input_tokens: int
    output_tokens: int
    session_id: str | None = None
```

- [ ] **Step 4: Update Scout.run() to track and reuse session_id**

Replace the `run()` method body (lines 38-74):

```python
    async def run(self, *, user_input: str, spec_text: str, snapshot_text: str, on_message: Callable | None = None) -> ScoutResult:
        total_cost = 0.0
        total_input = 0
        total_output = 0
        feedback: str | None = None
        session_id: str | None = None

        for attempt in range(self._max_retries):
            logger.info("Scout attempt %d/%d", attempt + 1, self._max_retries)

            if session_id:
                # Resume previous session — it already has file cache
                prompt = f"Previous attempt feedback: {feedback}\n\nProduce ONLY the CodebaseMap JSON."
            else:
                prompt = self._build_prompt(user_input, spec_text, snapshot_text, feedback)

            options = ClaudeCodeOptions(
                system_prompt=SCOUT_SYSTEM_PROMPT,
                max_turns=30, model=self._model,
                allowed_tools=["Read", "Glob", "Grep", "Bash"],
                permission_mode="acceptEdits",
            )
            if self._cwd:
                options.cwd = self._cwd
            if session_id:
                options.resume = session_id

            try:
                result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
            except Exception as e:
                logger.warning("Scout SDK error on attempt %d: %s", attempt + 1, e)
                feedback = f"SDK error: {e}"
                session_id = None  # Can't resume after SDK error
                continue

            if result:
                total_cost += result.cost_usd
                total_input += result.input_tokens
                total_output += result.output_tokens
                session_id = result.session_id  # Cache for retry

                raw = result.result or ""
                codebase_map, error = self._parse(raw)
                if codebase_map is not None:
                    return ScoutResult(
                        codebase_map=codebase_map,
                        cost_usd=total_cost,
                        input_tokens=total_input,
                        output_tokens=total_output,
                        session_id=session_id,
                    )
                feedback = f"Invalid output: {error}"
                logger.warning("Scout attempt %d parse failed: %s", attempt + 1, error)

        return ScoutResult(
            codebase_map=None,
            cost_usd=total_cost,
            input_tokens=total_input,
            output_tokens=total_output,
            session_id=session_id,
        )
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest forge/core/planning/scout_test.py -v`
Expected: All tests PASS (including the new session reuse test)

- [ ] **Step 6: Commit**

```bash
git add forge/core/planning/scout.py forge/core/planning/scout_test.py
git commit -m "feat(planning): reuse Scout session on retry to avoid redundant file reads"
```

---

## Chunk 4: Behind-Main Warning (Fix 8)

### Task 10: Behind-Main Warning on Final Screen

**Files:**
- Modify: `forge/tui/screens/final_approval.py` (add warning in `on_mount`)

- [ ] **Step 1: Add behind-main check method**

Add a new method to `FinalApprovalScreen` in `forge/tui/screens/final_approval.py`:

```python
    async def _check_behind_main(self) -> None:
        """Check if pipeline branch is behind origin/main and show warning."""
        project_dir = self._get_project_dir()
        if not project_dir:
            return
        try:
            # Fetch latest main
            fetch = await asyncio.create_subprocess_exec(
                "git", "fetch", "origin", "main", "--quiet",
                cwd=project_dir,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(fetch.wait(), timeout=15)

            # Count commits behind
            proc = await asyncio.create_subprocess_exec(
                "git", "rev-list", "--count", "HEAD..origin/main",
                cwd=project_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            count = int(stdout.decode().strip()) if stdout else 0

            if count > 0:
                warning = self.query_one("#behind-main-warning", Static)
                warning.update(
                    f"[bold #d29922]⚠ Branch is {count} commit{'s' if count != 1 else ''} "
                    f"behind main. PR may have merge conflicts.[/]"
                )
        except Exception:
            pass  # Non-critical — silently skip if git fails
```

- [ ] **Step 2: Add warning placeholder in compose()**

In the `compose()` method of `FinalApprovalScreen`, add a warning Static right after the header (around line 174):

```python
                    yield Static(f"[bold #58a6ff]{header}[/]\n", id="header")
                    yield Static("", id="behind-main-warning")  # ADD THIS LINE
                    yield Static(format_summary_stats(self._stats), id="stats")
```

- [ ] **Step 3: Call the check in on_mount()**

Add or update the `on_mount()` method in `FinalApprovalScreen`:

```python
    def on_mount(self) -> None:
        asyncio.create_task(self._check_behind_main())
```

Make sure `import asyncio` is at the top of the file.

- [ ] **Step 4: Run TUI tests**

Run: `python -m pytest forge/tui/ -x -q`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add forge/tui/screens/final_approval.py
git commit -m "feat(tui): show behind-main warning on final approval screen"
```

---

## Verification

### Automated Tests

```bash
# Test gate scoping
python -m pytest forge/core/daemon_helpers_test.py::TestFindRelatedTestFilesScoped -v

# Scout session reuse
python -m pytest forge/core/planning/scout_test.py -v

# Full regression
python -m pytest forge/ -x -q
```

### Manual Testing Checklist

1. **Fix 1 (Test gate scoping):** Run a pipeline task where an out-of-scope test would fail → verify it's skipped with INFO log
2. **Fix 2 (Priority bindings):** On final screen with textarea focused, press `r` → rerun triggers; press `d` → diff opens
3. **Fix 3 (Scrollable diff):** Open diff with >100 lines → scrollbar visible, `j`/`k` scroll, `g` to top, `G` to bottom
4. **Fix 4 (Home label):** Home screen bottom bar shows `[j/k] History` instead of `[↑↓] History`
5. **Fix 5 (No double bar):** Final approval screen has exactly one bottom bar (no Footer)
6. **Fix 6 (Stage indicator):** During deep planning, phase banner shows `Planning (Scout)`, output shows `─── Scout ───` separators
7. **Fix 7 (Session reuse):** Scout retries don't re-read files (check logs for resumed session)
8. **Fix 8 (Behind-main warning):** Create pipeline while main has advanced → warning shows commit count

# Forge v5: UX Polish Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Reduce redundant codebase scanning via shared project snapshots, add real-time planning visibility, move activity logs into per-task cards, and update the README.

**Architecture:** New `forge/core/context.py` gathers a rich project snapshot once per pipeline. The snapshot flows through the planner, agents, and reviewer via prompt injection. Planner streaming reuses the existing `on_message` callback pattern. Frontend adds a PlannerCard component and per-task activity sections while removing the bottom TimelinePanel.

**Tech Stack:** Python 3.12+, claude-code-sdk, FastAPI WebSocket, Next.js 14, Zustand, Tailwind CSS v4

---

## Phase 1: Project Snapshot (Backend)

### Task 1: Create `forge/core/context.py` — ProjectSnapshot

**Files:**
- Create: `forge/core/context.py`
- Test: `forge/core/context_test.py`

**Step 1: Write the failing test**

```python
# forge/core/context_test.py
"""Tests for project snapshot gathering."""
import os
import subprocess
import tempfile

import pytest

from forge.core.context import ProjectSnapshot, gather_project_snapshot


@pytest.fixture
def git_repo(tmp_path):
    """Create a minimal git repo with some files."""
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)

    # Create some files
    (tmp_path / "README.md").write_text("# Test Project\nThis is a test.\n")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "test"\nversion = "0.1.0"\n')

    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text('"""Source package."""\n')
    (src / "main.py").write_text("def hello():\n    return 'world'\n")
    (src / "utils.py").write_text("def add(a, b):\n    return a + b\n")

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_main.py").write_text("def test_hello():\n    pass\n")

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, capture_output=True, check=True)
    return tmp_path


def test_snapshot_returns_dataclass(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert isinstance(snap, ProjectSnapshot)


def test_snapshot_file_tree_contains_tracked_files(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "src/main.py" in snap.file_tree
    assert "README.md" in snap.file_tree


def test_snapshot_total_files(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    # README.md, pyproject.toml, src/__init__.py, src/main.py, src/utils.py, tests/test_main.py
    assert snap.total_files == 6


def test_snapshot_languages(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert snap.languages.get(".py", 0) >= 4


def test_snapshot_readme_excerpt(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "Test Project" in snap.readme_excerpt


def test_snapshot_config_summary(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "test" in snap.config_summary
    assert "0.1.0" in snap.config_summary


def test_snapshot_recent_commits(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    assert "init" in snap.recent_commits


def test_snapshot_git_branch(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    # Default branch varies (main or master)
    assert snap.git_branch in ("main", "master")


def test_snapshot_no_readme(git_repo):
    os.remove(git_repo / "README.md")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "rm readme"], cwd=git_repo, capture_output=True, check=True)
    snap = gather_project_snapshot(str(git_repo))
    assert snap.readme_excerpt == ""


def test_format_for_planner(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_planner()
    assert "File Tree" in text or "file_tree" in text.lower() or "src/main.py" in text
    assert "README" in text or "Test Project" in text


def test_format_for_agent(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_agent()
    assert "src/main.py" in text
    # Agent format should NOT include full README
    assert len(text) < len(snap.format_for_planner())


def test_format_for_reviewer(git_repo):
    snap = gather_project_snapshot(str(git_repo))
    text = snap.format_for_reviewer()
    assert "src/main.py" in text
```

**Step 2: Run test to verify it fails**

Run: `pytest forge/core/context_test.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'forge.core.context'`

**Step 3: Write the implementation**

```python
# forge/core/context.py
"""Project snapshot. Gathers rich project context once per pipeline."""

import os
import subprocess
from collections import Counter
from dataclasses import dataclass, field


@dataclass
class ProjectSnapshot:
    """Immutable snapshot of project state, computed once per pipeline."""

    file_tree: str = ""
    total_files: int = 0
    total_loc: int = 0
    languages: dict[str, int] = field(default_factory=dict)
    readme_excerpt: str = ""
    config_summary: str = ""
    module_index: str = ""
    recent_commits: str = ""
    git_branch: str = ""

    def format_for_planner(self) -> str:
        """Full context for the planner — tree, README, config, modules, commits."""
        sections = [
            f"=== PROJECT SNAPSHOT ===",
            f"Branch: {self.git_branch} | Files: {self.total_files} | LOC: {self.total_loc}",
            "",
            f"## File Tree\n{self.file_tree}",
        ]
        if self.readme_excerpt:
            sections.append(f"\n## README (excerpt)\n{self.readme_excerpt}")
        if self.config_summary:
            sections.append(f"\n## Project Config\n{self.config_summary}")
        if self.module_index:
            sections.append(f"\n## Module Index\n{self.module_index}")
        if self.recent_commits:
            sections.append(f"\n## Recent Commits\n{self.recent_commits}")
        sections.append("=== END SNAPSHOT ===")
        return "\n".join(sections)

    def format_for_agent(self) -> str:
        """Condensed context for agents — tree + config + modules only."""
        sections = [
            f"=== PROJECT CONTEXT ===",
            f"Branch: {self.git_branch} | Files: {self.total_files}",
            "",
            f"File Tree:\n{self.file_tree}",
        ]
        if self.config_summary:
            sections.append(f"\nConfig:\n{self.config_summary}")
        if self.module_index:
            sections.append(f"\nModules:\n{self.module_index}")
        sections.append("=== END CONTEXT ===")
        return "\n".join(sections)

    def format_for_reviewer(self) -> str:
        """Context for L2 reviewer — tree + modules for architectural awareness."""
        sections = [
            f"=== PROJECT CONTEXT ===",
            f"Files: {self.total_files} | LOC: {self.total_loc}",
            "",
            f"File Tree:\n{self.file_tree}",
        ]
        if self.module_index:
            sections.append(f"\nModules:\n{self.module_index}")
        sections.append("=== END CONTEXT ===")
        return "\n".join(sections)


def gather_project_snapshot(project_dir: str) -> ProjectSnapshot:
    """Gather a rich project snapshot from a git repo. All operations are local."""
    file_tree = _get_file_tree(project_dir)
    files = _get_tracked_files(project_dir)
    total_files = len(files)
    languages = _count_languages(files)
    total_loc = _count_loc(project_dir, files)
    readme_excerpt = _read_readme(project_dir)
    config_summary = _read_config(project_dir)
    module_index = _build_module_index(project_dir, files)
    recent_commits = _get_recent_commits(project_dir)
    git_branch = _get_branch(project_dir)

    return ProjectSnapshot(
        file_tree=file_tree,
        total_files=total_files,
        total_loc=total_loc,
        languages=languages,
        readme_excerpt=readme_excerpt,
        config_summary=config_summary,
        module_index=module_index,
        recent_commits=recent_commits,
        git_branch=git_branch,
    )


def _get_tracked_files(project_dir: str) -> list[str]:
    """Get list of all git-tracked files."""
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=project_dir, capture_output=True, text=True,
    )
    return [f for f in result.stdout.strip().split("\n") if f]


def _get_file_tree(project_dir: str) -> str:
    """Build a tree-like view from git ls-files."""
    files = _get_tracked_files(project_dir)
    # Simple indented tree
    tree_lines: list[str] = []
    prev_parts: list[str] = []
    for f in sorted(files):
        parts = f.split("/")
        # Find common prefix length with previous
        common = 0
        for i, (a, b) in enumerate(zip(prev_parts, parts)):
            if a == b:
                common = i + 1
            else:
                break
        # Print new directory levels
        for i in range(common, len(parts) - 1):
            tree_lines.append("  " * i + parts[i] + "/")
        # Print file
        tree_lines.append("  " * (len(parts) - 1) + parts[-1])
        prev_parts = parts
    return "\n".join(tree_lines)


def _count_languages(files: list[str]) -> dict[str, int]:
    """Count files by extension."""
    counter: Counter[str] = Counter()
    for f in files:
        _, ext = os.path.splitext(f)
        if ext:
            counter[ext] += 1
    return dict(counter.most_common())


def _count_loc(project_dir: str, files: list[str]) -> int:
    """Count non-empty lines across all tracked files. Fast approximation."""
    total = 0
    for f in files:
        path = os.path.join(project_dir, f)
        try:
            with open(path, "r", errors="ignore") as fh:
                total += sum(1 for line in fh if line.strip())
        except (OSError, UnicodeDecodeError):
            pass
    return total


def _read_readme(project_dir: str, max_lines: int = 200) -> str:
    """Read first N lines of README.md if it exists."""
    for name in ("README.md", "readme.md", "README.rst", "README"):
        path = os.path.join(project_dir, name)
        if os.path.isfile(path):
            try:
                with open(path) as fh:
                    lines = []
                    for i, line in enumerate(fh):
                        if i >= max_lines:
                            break
                        lines.append(line)
                    return "".join(lines).strip()
            except OSError:
                pass
    return ""


def _read_config(project_dir: str) -> str:
    """Extract key project config from pyproject.toml or setup.py."""
    pyproject = os.path.join(project_dir, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject) as fh:
                content = fh.read()
            # Extract [project] section basics
            lines = []
            in_project = False
            for line in content.split("\n"):
                if line.strip() == "[project]":
                    in_project = True
                    continue
                if in_project:
                    if line.startswith("[") and line.strip() != "[project]":
                        break
                    if line.strip():
                        lines.append(line.strip())
            return "\n".join(lines[:15]) if lines else content[:500]
        except OSError:
            pass
    return ""


def _build_module_index(project_dir: str, files: list[str]) -> str:
    """List top-level Python packages with their __init__.py docstrings."""
    packages: dict[str, str] = {}
    for f in files:
        parts = f.split("/")
        if len(parts) >= 2 and parts[-1] == "__init__.py":
            pkg = parts[0]
            if pkg not in packages:
                init_path = os.path.join(project_dir, f)
                doc = _extract_docstring(init_path)
                packages[pkg] = doc
    lines = []
    for pkg, doc in sorted(packages.items()):
        if doc:
            lines.append(f"  {pkg}/ — {doc}")
        else:
            lines.append(f"  {pkg}/")
    return "\n".join(lines)


def _extract_docstring(filepath: str) -> str:
    """Extract the module docstring from a Python file (first triple-quoted string)."""
    try:
        with open(filepath) as fh:
            content = fh.read(2000)  # Only read start
        # Simple extraction: find first triple-quoted string
        for quote in ('"""', "'''"):
            start = content.find(quote)
            if start != -1:
                end = content.find(quote, start + 3)
                if end != -1:
                    return content[start + 3 : end].strip().split("\n")[0]
    except OSError:
        pass
    return ""


def _get_recent_commits(project_dir: str, count: int = 10) -> str:
    """Get the last N commits (oneline format)."""
    result = subprocess.run(
        ["git", "log", f"--oneline", f"-{count}"],
        cwd=project_dir, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _get_branch(project_dir: str) -> str:
    """Get current git branch name."""
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=project_dir, capture_output=True, text=True,
    )
    return result.stdout.strip() or "main"
```

**Step 4: Run test to verify it passes**

Run: `pytest forge/core/context_test.py -v`
Expected: All tests PASS

**Step 5: Commit**

```bash
git add forge/core/context.py forge/core/context_test.py
git commit -m "feat: add ProjectSnapshot for shared project context gathering"
```

---

### Task 2: Inject snapshot into daemon, planner, agents, and reviewer

**Files:**
- Modify: `forge/core/daemon.py:54-65` (add `_snapshot` field to __init__)
- Modify: `forge/core/daemon.py:128-169` (inject snapshot in plan())
- Modify: `forge/core/daemon.py:360-371` (inject snapshot into agent prompt)
- Modify: `forge/core/daemon.py:703-713` (replace `_gather_context()`)
- Modify: `forge/agents/adapter.py:12-23` (update system prompt template)
- Modify: `forge/agents/adapter.py:57-77` (`_build_options` accepts snapshot)
- Modify: `forge/agents/adapter.py:79-91` (`run` accepts snapshot)
- Modify: `forge/review/llm_review.py:32-75` (accept snapshot in gate2)
- Modify: `forge/review/llm_review.py:78-98` (inject snapshot in review prompt)

**Step 1: Update `daemon.py` — store snapshot on instance, compute in `plan()`**

In `__init__` (line 60), add after `self._strategy = ...`:
```python
self._snapshot: ProjectSnapshot | None = None
```

Add import at top of file:
```python
from forge.core.context import ProjectSnapshot, gather_project_snapshot
```

In `plan()` (line 148), replace:
```python
graph = await planner.plan(user_input, context=self._gather_context())
```
with:
```python
self._snapshot = gather_project_snapshot(self._project_dir)
graph = await planner.plan(user_input, context=self._snapshot.format_for_planner())
```

**Step 2: Update `adapter.py` — accept and inject snapshot into agent system prompt**

Update `AGENT_SYSTEM_PROMPT_TEMPLATE` (line 12) to add a `{project_context}` placeholder:
```python
AGENT_SYSTEM_PROMPT_TEMPLATE = """You are a coding agent working on a specific task within the Forge orchestration system.

Your working directory is {cwd}. Do NOT read, write, or execute anything outside this directory{extra_dirs_clause}.

You have access to a git worktree isolated to your task. Write clean, tested code.

{project_context}

Rules:
- Only modify files listed in your task specification
- Follow existing code style and patterns
- Write tests for any new functionality
- Commit your changes with a clear commit message when done
- If you encounter an error, fix it rather than giving up"""
```

Update `_build_options` (line 57) to accept `project_context: str = ""`:
```python
def _build_options(
    self, worktree_path: str, allowed_dirs: list[str], model: str = "sonnet",
    project_context: str = "",
) -> ClaudeCodeOptions:
```

In `_build_options`, update the `format()` call:
```python
system_prompt = AGENT_SYSTEM_PROMPT_TEMPLATE.format(
    cwd=worktree_path, extra_dirs_clause=extra_dirs_clause,
    project_context=project_context,
)
```

Update `run()` (line 79) signature to accept `project_context: str = ""`:
```python
async def run(
    self,
    task_prompt: str,
    worktree_path: str,
    allowed_files: list[str],
    timeout_seconds: int,
    allowed_dirs: list[str] | None = None,
    model: str = "sonnet",
    on_message: Callable | None = None,
    project_context: str = "",
) -> AgentResult:
    options = self._build_options(
        worktree_path, allowed_dirs or [], model=model,
        project_context=project_context,
    )
```

Also update the abstract `AgentAdapter.run()` signature to match.

**Step 3: Inject snapshot in `daemon.py:_execute_task()`**

In `_execute_task()`, before the `runtime.run_task()` call (around line 394), add snapshot context:
```python
snapshot_context = self._snapshot.format_for_agent() if self._snapshot else ""
```

Thread it through to the agent. The `AgentRuntime.run_task()` likely wraps `ClaudeAdapter.run()` — check if it passes kwargs through. If so, add `project_context=snapshot_context`. If not, update `AgentRuntime.run_task()` to accept and forward the parameter.

**Step 4: Inject snapshot in `llm_review.py`**

Update `gate2_llm_review()` signature (line 32) to accept `project_context: str = ""`:
```python
async def gate2_llm_review(
    task_title: str,
    task_description: str,
    diff: str,
    worktree_path: str | None = None,
    model: str = "sonnet",
    prior_feedback: str | None = None,
    project_context: str = "",
) -> GateResult:
```

Update `_build_review_prompt()` (line 78) to accept and inject context:
```python
def _build_review_prompt(
    title: str, description: str, diff: str,
    prior_feedback: str | None = None,
    project_context: str = "",
) -> str:
    parts = []
    if project_context:
        parts.append(f"{project_context}\n\n")
    parts.extend([
        f"Task: {title}\n",
        f"Description: {description}\n\n",
        f"Git diff of changes:\n```diff\n{diff}\n```\n\n",
    ])
```

In `daemon.py:_run_review()`, pass the snapshot when calling gate2:
```python
snapshot_context = self._snapshot.format_for_reviewer() if self._snapshot else ""
gate2_result = await gate2_llm_review(
    task.title, task.description, diff, worktree_path,
    model=reviewer_model, prior_feedback=prior_feedback,
    project_context=snapshot_context,
)
```

**Step 5: Remove old `_gather_context()`**

Delete or replace the `_gather_context()` method at lines 703-713. It's no longer called.

**Step 6: Run tests**

Run: `pytest forge/ -q`
Expected: All existing tests PASS (may need to update mocks for new signatures)

**Step 7: Commit**

```bash
git add forge/core/daemon.py forge/agents/adapter.py forge/review/llm_review.py
git commit -m "feat: inject project snapshot into planner, agents, and reviewer"
```

---

## Phase 2: Planner Streaming (Backend)

### Task 3: Thread `on_message` through planner pipeline

**Files:**
- Modify: `forge/core/planner.py:16-21` (PlannerLLM abstract method signature)
- Modify: `forge/core/planner.py:31-50` (Planner.plan() accepts and forwards on_message)
- Modify: `forge/core/claude_planner.py:48-78` (generate_plan accepts on_message, passes to sdk_query)
- Modify: `forge/core/daemon.py:128-169` (plan() creates streaming callback)

**Step 1: Update `PlannerLLM` abstract interface**

In `forge/core/planner.py` (line 20), update signature:
```python
class PlannerLLM(ABC):
    """Interface for the LLM that generates plans."""

    @abstractmethod
    async def generate_plan(
        self, user_input: str, context: str, feedback: str | None = None,
        on_message: Callable | None = None,
    ) -> str:
        """Generate a TaskGraph JSON string from user input."""
```

Add import: `from collections.abc import Callable`

**Step 2: Update `Planner.plan()` to forward on_message**

In `forge/core/planner.py` (line 31), update signature:
```python
async def plan(self, user_input: str, context: str = "", on_message: Callable | None = None) -> TaskGraph:
```

In the retry loop (line 36), forward it:
```python
raw = await self._llm.generate_plan(user_input, context, feedback, on_message=on_message)
```

**Step 3: Update `ClaudePlannerLLM.generate_plan()` to accept and use on_message**

In `forge/core/claude_planner.py` (line 48), update signature:
```python
async def generate_plan(
    self, user_input: str, context: str, feedback: str | None = None,
    on_message: Callable | None = None,
) -> str:
```

In the sdk_query call (line 65), pass it:
```python
result = await sdk_query(prompt=prompt, options=options, on_message=on_message)
```

**Step 4: Create planner streaming callback in `daemon.plan()`**

In `forge/core/daemon.py`, inside `plan()` (after line 146), add the streaming callback:

```python
async def _on_planner_msg(msg):
    text = _extract_text(msg)
    if text:
        if pipeline_id:
            await self._emit("planner:output", {"line": text}, db=db, pipeline_id=pipeline_id)
        else:
            await self._events.emit("planner:output", {"line": text})

planner_llm = ClaudePlannerLLM(model=planner_model, cwd=self._project_dir)
planner = Planner(planner_llm, max_retries=self._settings.max_retries)

graph = await planner.plan(
    user_input,
    context=self._snapshot.format_for_planner() if self._snapshot else self._gather_context(),
    on_message=_on_planner_msg,
)
```

**Step 5: Run tests**

Run: `pytest forge/core/planner_test.py forge/core/claude_planner_test.py -v`
Expected: Tests pass (mocked SDK, on_message=None by default)

**Step 6: Commit**

```bash
git add forge/core/planner.py forge/core/claude_planner.py forge/core/daemon.py
git commit -m "feat: stream planner output via on_message callback"
```

---

## Phase 3: Planning UI (Frontend)

### Task 4: Add PlannerCard component

**Files:**
- Create: `web/src/components/task/PlannerCard.tsx`

**Step 1: Create the PlannerCard component**

```tsx
// web/src/components/task/PlannerCard.tsx
"use client";

import { useEffect, useRef } from "react";
import { useTaskStore } from "@/stores/taskStore";

export default function PlannerCard() {
  const plannerOutput = useTaskStore((s) => s.plannerOutput);
  const phase = useTaskStore((s) => s.phase);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [plannerOutput]);

  if (phase !== "planning" && plannerOutput.length === 0) return null;

  const isActive = phase === "planning";

  return (
    <div className="mb-8 rounded-xl border border-zinc-800 bg-zinc-900 overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 border-b border-zinc-800">
        <div className="flex items-center gap-2">
          {isActive ? (
            <div className="h-2.5 w-2.5 rounded-full bg-blue-500 animate-pulse" />
          ) : (
            <svg className="h-4 w-4 text-green-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
            </svg>
          )}
          <h3 className="text-sm font-semibold text-zinc-200">
            {isActive ? "Planning..." : "Planning Complete"}
          </h3>
        </div>
      </div>

      {/* Streaming output */}
      {plannerOutput.length > 0 && (
        <div
          ref={scrollRef}
          className="max-h-48 overflow-y-auto p-3 font-mono text-xs leading-relaxed"
        >
          {plannerOutput.map((line, i) => (
            <div key={i} className="whitespace-pre-wrap text-zinc-400">
              {line}
            </div>
          ))}
        </div>
      )}

      {/* Loading indicator when no output yet */}
      {isActive && plannerOutput.length === 0 && (
        <div className="flex items-center gap-2 px-4 py-3 text-sm text-zinc-400">
          <svg className="h-4 w-4 animate-spin" fill="none" viewBox="0 0 24 24">
            <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
            <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
          </svg>
          Analyzing project and decomposing task...
        </div>
      )}
    </div>
  );
}
```

**Step 2: Commit**

```bash
git add web/src/components/task/PlannerCard.tsx
git commit -m "feat: add PlannerCard component for planning visibility"
```

---

### Task 5: Wire PlannerCard into page and remove "Connecting..." placeholder

**Files:**
- Modify: `web/src/app/tasks/view/page.tsx:15` (import PlannerCard)
- Modify: `web/src/app/tasks/view/page.tsx:316-350` (add PlannerCard, update placeholder)

**Step 1: Add import**

At line 15 of `page.tsx`, add:
```typescript
import PlannerCard from "@/components/task/PlannerCard";
```

**Step 2: Add PlannerCard after Pipeline Progress (line 315)**

After the PipelineProgress section and before the Plan Panel, insert:
```tsx
{/* Planner Card — shown during planning phase */}
<PlannerCard />
```

**Step 3: Update the empty state placeholder (lines 334-350)**

Replace the current placeholder block:
```tsx
!hasTasks &&
phase !== "planned" && (
  <div className="flex h-64 items-center justify-center ...">
    ...
    {phase === "idle" ? "Connecting..." : "Planning tasks..."}
    ...
  </div>
)
```
with just the idle case (PlannerCard handles the planning case now):
```tsx
!hasTasks &&
phase === "idle" && (
  <div className="flex h-64 items-center justify-center rounded-xl border border-zinc-800 bg-zinc-900">
    <div className="text-center">
      <div className="mb-2 text-lg text-zinc-400">
        Waiting for pipeline to start...
      </div>
      <div className="h-1.5 w-48 overflow-hidden rounded-full bg-zinc-800">
        <div className="h-full w-1/3 animate-pulse rounded-full bg-blue-600" />
      </div>
    </div>
  </div>
)
```

**Step 4: Commit**

```bash
git add web/src/app/tasks/view/page.tsx
git commit -m "feat: render PlannerCard during planning phase"
```

---

## Phase 4: Timeline Rework (Frontend)

### Task 6: Add per-task activity section to AgentCard

**Files:**
- Modify: `web/src/components/task/AgentCard.tsx:160-337`

**Step 1: Add activity section to AgentCard**

In `AgentCard.tsx`, add a new section after the agent output section (after line 253) and before review gates (line 255).

First, at the top of the component (around line 164), add timeline access:
```typescript
const timeline = useTaskStore((s) => s.timeline);
const taskTimeline = timeline.filter(e => e.taskId === task.id);
```

Then add the activity section between agent output and review gates:
```tsx
{/* Per-task activity log */}
{taskTimeline.length > 0 && (
  <div>
    <p className="mb-1 text-xs font-medium text-zinc-500">
      Activity ({taskTimeline.length})
    </p>
    <div className="space-y-0.5 max-h-32 overflow-y-auto">
      {taskTimeline.map((ev, i) => (
        <div key={i} className="flex items-start gap-2 text-xs">
          <span className="shrink-0 font-mono text-zinc-600">
            {formatActivityTime(ev.timestamp)}
          </span>
          <span className={activityColor(ev.type)}>
            {activityLabel(ev)}
          </span>
        </div>
      ))}
    </div>
  </div>
)}
```

Add helper functions before the AgentCard component (after the `FormattedLine` component, around line 156):

```typescript
function formatActivityTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  } catch {
    return "";
  }
}

function activityColor(type: string): string {
  const colors: Record<string, string> = {
    "task:state_changed": "text-zinc-300",
    "task:review_update": "text-yellow-400",
    "task:merge_result": "text-green-400",
    "task:cost_update": "text-zinc-500",
    "task:files_changed": "text-blue-400",
  };
  return colors[type] || "text-zinc-400";
}

function activityLabel(ev: { type: string; payload: Record<string, unknown> }): string {
  const p = ev.payload;
  switch (ev.type) {
    case "task:state_changed":
      return `State: ${p.state}`;
    case "task:review_update":
      return `${p.gate} ${p.passed ? "passed" : "failed"}`;
    case "task:merge_result":
      return p.success ? "Merged successfully" : `Merge failed: ${p.error || "unknown"}`;
    case "task:cost_update":
      return `Cost: $${(p.cost_usd as number)?.toFixed(4)}`;
    case "task:files_changed":
      return `${(p.files as string[])?.length || 0} files changed`;
    default:
      return ev.type.split(":")[1] || ev.type;
  }
}
```

**Step 2: Commit**

```bash
git add web/src/components/task/AgentCard.tsx
git commit -m "feat: add per-task activity log to AgentCard"
```

---

### Task 7: Add activity log to TaskDetailPanel

**Files:**
- Modify: `web/src/components/task/TaskDetailPanel.tsx:1-150`

**Step 1: Import taskStore and add timeline access**

At top of `TaskDetailPanel.tsx`, add:
```typescript
import { useTaskStore } from "@/stores/taskStore";
```

Inside the component, before the return statement (line 21):
```typescript
const timeline = useTaskStore((s) => s.timeline);
const taskTimeline = timeline.filter(e => e.taskId === task.id);
```

**Step 2: Add activity log section after Agent Output (after line 76)**

```tsx
{/* Activity Log */}
{taskTimeline.length > 0 && (
  <section className="mb-6">
    <h3 className="mb-2 text-sm font-semibold text-zinc-300">
      Activity ({taskTimeline.length} events)
    </h3>
    <div className="space-y-1.5 max-h-48 overflow-y-auto rounded-lg border border-zinc-800 bg-zinc-900 p-3">
      {taskTimeline.map((ev, i) => (
        <div key={i} className="flex items-start gap-2 text-xs">
          <span className="shrink-0 font-mono text-zinc-600">
            {new Date(ev.timestamp).toLocaleTimeString()}
          </span>
          <span className="text-zinc-400">
            {ev.type.split(":")[1] || ev.type}
          </span>
          <span className="text-zinc-500 truncate">
            {JSON.stringify(ev.payload).slice(0, 100)}
          </span>
        </div>
      ))}
    </div>
  </section>
)}
```

**Step 3: Commit**

```bash
git add web/src/components/task/TaskDetailPanel.tsx
git commit -m "feat: add activity log section to TaskDetailPanel"
```

---

### Task 8: Add pipeline status banner and remove TimelinePanel

**Files:**
- Modify: `web/src/app/tasks/view/page.tsx:15` (remove TimelinePanel import)
- Modify: `web/src/app/tasks/view/page.tsx:352-357` (remove TimelinePanel render)
- Modify: `web/src/app/tasks/view/page.tsx:326-333` (add pipeline banner before agent grid)
- Delete: `web/src/components/task/TimelinePanel.tsx`

**Step 1: Remove TimelinePanel import (line 15)**

Remove this line:
```typescript
import TimelinePanel from "@/components/task/TimelinePanel";
```

Also remove the `timeline` store selector (line 208):
```typescript
const timeline = useTaskStore((s) => s.timeline);
```

**Step 2: Remove the TimelinePanel render block (lines 352-357)**

Delete this block entirely:
```tsx
{/* Timeline */}
{timeline.length > 0 && (
  <div className="mt-6">
    <TimelinePanel events={timeline} />
  </div>
)}
```

**Step 3: Add pipeline status banner before agent grid**

After the PlanPanel and before the agent cards grid (between lines 325 and 327), add:

```tsx
{/* Pipeline Status Banner */}
{showAgentCards && (
  <div className="mb-4 flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900 px-4 py-2.5">
    <div className="flex items-center gap-2 text-sm text-zinc-400">
      <span>
        {phase === "executing" ? "Executing" : phase === "reviewing" ? "Reviewing" : "Complete"}
      </span>
      <span className="text-zinc-600">|</span>
      <span>
        {taskList.filter(t => t.state === "done").length}/{taskList.length} tasks complete
      </span>
    </div>
  </div>
)}
```

**Step 4: Delete TimelinePanel.tsx**

Run: `rm web/src/components/task/TimelinePanel.tsx`

**Step 5: Verify the build still compiles**

Run: `cd web && npm run build` (or `npx next build`)
Expected: Build succeeds with no import errors

**Step 6: Commit**

```bash
git rm web/src/components/task/TimelinePanel.tsx
git add web/src/app/tasks/view/page.tsx
git commit -m "feat: remove bottom timeline, add pipeline status banner"
```

---

## Phase 5: README Update

### Task 9: Comprehensive README rewrite

**Files:**
- Modify: `README.md`

**Step 1: Update the README**

Key changes to make (refer to design doc section 4 for full details):

1. **Line 28**: Change `"merged code on your \`main\` branch"` → `"Reviewed code delivered via pull request"`

2. **Lines 74 (pipeline step 5)**: Update merge description to mention PR creation

3. **Lines 92-114 ("Where Does the Code Go?")**: Rewrite to explain PR-based workflow:
   - Code goes to a feature branch
   - Auto-PR created when all tasks pass review
   - Merge via PR review (not direct to main)

4. **Lines 119-129 ("Claude Sessions" table)**:
   - Planning row: `max-turns 1` → `max-turns 10`, `None` → `Read, Glob, Grep` (planner reads files)
   - Review row: `max-turns 1` → `max-turns 2`

5. **Lines 349-362 ("Testing" section)**: Update test count from `117` to `421+`

6. **Lines 366-372 ("Limitations" section)**: Remove `"No streaming output"` bullet

7. **Lines 376-382 ("Project Status" section)**: Update:
   - `117 unit tests` → `421+ unit tests across 30+ modules`
   - Add: `Web UI with real-time dashboard (forge serve)`
   - Add: `PR-based merge workflow with auto-PR creation`

8. **Add new section after Quick Start: "Web UI"**:
   ```markdown
   ## Web UI

   Forge includes a real-time web dashboard:

   ```bash
   # Set JWT secret for auth
   export FORGE_JWT_SECRET="your-secret-key"

   # Start backend (port 8000) + frontend (port 3000)
   forge serve
   ```

   Features:
   - Live pipeline progress with WebSocket streaming
   - Agent output streaming (watch code being written)
   - Review gate results and merge status per task
   - One-click task retry and pipeline resume
   - Auto-PR creation when all tasks pass
   ```

9. **Update Architecture module map (lines 210-246)**: Add web/ modules and new core modules:
   ```
   forge/
     api/
       routes/tasks.py         REST API + WebSocket endpoints
       ws/manager.py           WebSocket connection manager
     core/
       context.py              Project snapshot gathering
       events.py               Async event emitter (pub/sub)
       model_router.py         Strategy-based model selection
   web/
     src/
       app/tasks/view/         Pipeline execution view
       components/task/        AgentCard, PlannerCard, PipelineProgress
       stores/taskStore.ts     Zustand state management
       hooks/useWebSocket.ts   WebSocket connection hook
   ```

10. **Add "Model Routing" section after Configuration**:
    ```markdown
    ## Model Routing

    Control cost vs quality with model routing strategies:

    | Strategy | Planner | Agent | Reviewer |
    |----------|---------|-------|----------|
    | `cost-optimized` | haiku | sonnet | haiku |
    | `balanced` (default) | sonnet | sonnet | haiku |
    | `quality-first` | opus | opus | sonnet |

    Set via environment variable:
    ```bash
    FORGE_MODEL_STRATEGY=quality-first forge run "Build authentication system"
    ```
    ```

**Step 2: Commit**

```bash
git add README.md
git commit -m "docs: comprehensive README update — Web UI, PR workflow, 421+ tests"
```

---

## Phase 6: Final Verification

### Task 10: Run full test suite and verify build

**Step 1: Run backend tests**

Run: `pytest forge/ -q`
Expected: 421+ tests pass (allow for the 1 pre-existing diff_test flake)

**Step 2: Run frontend build**

Run: `cd web && npm run build`
Expected: Build succeeds

**Step 3: Verify no import errors**

Run: `python -c "from forge.core.context import gather_project_snapshot; print('OK')"`
Expected: `OK`

**Step 4: Push branch and create PR**

```bash
git push -u origin feat/v5-design-plan
gh pr create --title "feat: v5 UX polish — shared context, planner UI, timeline rework" --body "$(cat <<'EOF'
## Summary
- **Project Snapshot**: Rich context gathered once per pipeline, injected into planner/agents/reviewer — eliminates redundant codebase scanning
- **Planner UI**: Real-time planner output streaming via PlannerCard component
- **Timeline Rework**: Per-task activity logs in AgentCard, removed bottom timeline
- **README**: Updated to reflect Web UI, PR workflow, 421+ tests, model routing

## Design Doc
See `docs/plans/2026-03-01-forge-v5-ux-polish-design.md`

## Test plan
- [ ] All 421+ backend tests pass
- [ ] Frontend builds without errors
- [ ] E2E: Planning shows streaming output in PlannerCard
- [ ] E2E: Agent cards show per-task activity sections
- [ ] E2E: Bottom timeline is gone
- [ ] README sections are accurate

Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

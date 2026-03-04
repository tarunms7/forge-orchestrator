# Design: Configurable Build & Test Verification Gates

## Data Flow Diagram

```
Agent completes work in worktree
            |
            v
  +---------------------+
  |  Gate 0: BUILD      |  <-- FORGE_BUILD_CMD or per-pipeline build_cmd
  |  (pre-review)       |      e.g. "npm run build", "cargo build"
  |                     |      Skip (auto-pass) if no command configured
  +--------+------------+
           | pass
           v
  +---------------------+
  |  Gate 1: LINT       |  <-- Existing _gate1 (ruff check on .py files)
  |  (auto-check)       |      No changes to this gate
  +--------+------------+
           | pass
           v
  +---------------------+
  |  Gate 1.5: TEST     |  <-- FORGE_TEST_CMD or per-pipeline test_cmd
  |  (test suite)       |      e.g. "pytest", "npm test"
  |                     |      Skip (auto-pass) if no command configured
  |                     |      Captures stdout+stderr (truncated 5000 chars)
  +--------+------------+
           | pass
           v
  +---------------------+
  |  Gate 2: LLM REVIEW |  <-- Existing gate2_llm_review (no changes)
  +--------+------------+
           | pass
           v
  +---------------------+
  |  Gate 3: MERGE      |  <-- Auto-pass (future plugin hook)
  |  (readiness)        |
  +--------+------------+
           | pass
           v
      Merge to pipeline branch

  On ANY gate failure --> agent retries with gate output as feedback
```

### Gate naming convention

| Gate ID   | Event name | Label (UI hover) | Description              |
|-----------|------------|------------------|--------------------------|
| `Build`   | `Build`    | "Build"          | User-configured build    |
| `L1`      | `L1`       | "Lint"           | Ruff lint (existing)     |
| `Test`    | `Test`     | "Test"           | User-configured tests    |
| `L2`      | `L2`       | "Review"         | LLM code review          |

---

## 1. Settings / Env Var Additions

### File: `forge/config/settings.py`

**Before:**
```python
class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Model routing strategy
    model_strategy: str = "auto"

    # Agent limits
    max_agents: int = 4
    agent_timeout_seconds: int = 600
    context_rotation_tokens: int = 80_000
    max_retries: int = 3

    # Agent sandboxing
    allowed_dirs: list[str] = []

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Polling
    scheduler_poll_interval: float = 1.0
```

**After:**
```python
class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Model routing strategy
    model_strategy: str = "auto"

    # Agent limits
    max_agents: int = 4
    agent_timeout_seconds: int = 600
    context_rotation_tokens: int = 80_000
    max_retries: int = 3

    # Agent sandboxing
    allowed_dirs: list[str] = []

    # Build & test verification
    build_cmd: str | None = None   # e.g. "npm run build", "cargo build"
    test_cmd: str | None = None    # e.g. "pytest", "npm test"

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Polling
    scheduler_poll_interval: float = 1.0
```

**New env vars:**
- `FORGE_BUILD_CMD` — global build command (default: `None` = skip gate)
- `FORGE_TEST_CMD` — global test command (default: `None` = skip gate)

---

## 2. DB Schema Changes

### File: `forge/storage/db.py` — `PipelineRow`

**Before:**
```python
class PipelineRow(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    project_dir: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="planning")
    model_strategy: Mapped[str] = mapped_column(String, default="auto")
    task_graph_json: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    base_branch: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    branch_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    cancelled_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

**After:**
```python
class PipelineRow(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    project_dir: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="planning")
    model_strategy: Mapped[str] = mapped_column(String, default="auto")
    task_graph_json: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    pr_url: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    base_branch: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    branch_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    cancelled_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    build_cmd: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    test_cmd: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
```

**New columns:** `build_cmd` (nullable String), `test_cmd` (nullable String).

The existing `_add_missing_columns` migration helper (used at `Database.initialize()`) will auto-add these columns to existing databases via `ALTER TABLE`.

### `Database.create_pipeline` signature update

**Before:**
```python
async def create_pipeline(
    self, id: str, description: str, project_dir: str,
    model_strategy: str = "auto", user_id: str | None = None,
    base_branch: str | None = None, branch_name: str | None = None,
) -> None:
```

**After:**
```python
async def create_pipeline(
    self, id: str, description: str, project_dir: str,
    model_strategy: str = "auto", user_id: str | None = None,
    base_branch: str | None = None, branch_name: str | None = None,
    build_cmd: str | None = None, test_cmd: str | None = None,
) -> None:
```

Pass `build_cmd` and `test_cmd` through to the `PipelineRow(...)` constructor.

---

## 3. API Schema Changes

### File: `forge/api/models/schemas.py` — `CreateTaskRequest`

**Before:**
```python
class CreateTaskRequest(BaseModel):
    """Request body for creating a new pipeline task."""

    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(default_factory=list, description="Base64-encoded image data URIs")
    branch_name: str | None = None
```

**After:**
```python
class CreateTaskRequest(BaseModel):
    """Request body for creating a new pipeline task."""

    description: str
    project_path: str
    extra_dirs: list[str] = Field(default_factory=list)
    model_strategy: str = "auto"
    images: list[str] = Field(default_factory=list, description="Base64-encoded image data URIs")
    branch_name: str | None = None
    build_cmd: str | None = Field(
        default=None,
        description="Build command to run in worktree before review (e.g. 'npm run build'). "
                    "Overrides FORGE_BUILD_CMD for this pipeline.",
    )
    test_cmd: str | None = Field(
        default=None,
        description="Test command to run in worktree after lint (e.g. 'pytest'). "
                    "Overrides FORGE_TEST_CMD for this pipeline.",
    )
```

### File: `forge/api/routes/tasks.py` — `create_task` endpoint

In the `create_task` handler, pass the new fields through to `create_pipeline`:

```python
await forge_db.create_pipeline(
    id=pipeline_id,
    description=description,
    project_dir=body.project_path,
    model_strategy=body.model_strategy,
    user_id=user_id,
    branch_name=body.branch_name,
    build_cmd=body.build_cmd,      # NEW
    test_cmd=body.test_cmd,        # NEW
)
```

### Command resolution order

When the daemon runs gates, it resolves the effective command:

```
per-pipeline (from PipelineRow) > global env (from ForgeSettings) > None (skip gate)
```

---

## 4. Review Pipeline Changes

### File: `forge/review/pipeline.py`

No structural changes needed. The `ReviewPipeline` class is generic — it takes a list of `GateFunc` callables. However, `_run_review` in `daemon_review.py` runs gates inline rather than using `ReviewPipeline`, so no changes to `pipeline.py` are required.

### File: `forge/core/daemon_review.py` — `ReviewMixin`

**Before:**
```python
class ReviewMixin:
    async def _run_review(
        self, task, worktree_path: str, diff: str, *, db, pipeline_id: str,
    ) -> tuple[bool, str | None]:
        feedback_parts: list[str] = []

        # L1: lint only the changed files
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        gate1_result = await self._gate1(worktree_path)
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L1", "passed": gate1_result.passed,
            "details": gate1_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate1_result.passed:
            console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
            feedback_parts.append(f"L1 (lint) FAILED:\n{gate1_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L1 passed[/green]")

        # L2: LLM review
        # ...existing L2 code...

        # Gate 3: skip for now
        console.print("[green]  Gate 3 (merge readiness): auto-pass[/green]")
        return True, None
```

**After:**
```python
import asyncio

class ReviewMixin:
    def _resolve_build_cmd(self) -> str | None:
        """Resolve effective build command: per-pipeline > env > None."""
        per_pipeline = getattr(self, "_pipeline_build_cmd", None)
        if per_pipeline:
            return per_pipeline
        return self._settings.build_cmd

    def _resolve_test_cmd(self) -> str | None:
        """Resolve effective test command: per-pipeline > env > None."""
        per_pipeline = getattr(self, "_pipeline_test_cmd", None)
        if per_pipeline:
            return per_pipeline
        return self._settings.test_cmd

    async def _run_review(
        self, task, worktree_path: str, diff: str, *, db, pipeline_id: str,
    ) -> tuple[bool, str | None]:
        feedback_parts: list[str] = []
        gate_timeout = self._settings.agent_timeout_seconds // 2

        # Gate 0: BUILD (pre-review)
        build_cmd = self._resolve_build_cmd()
        if build_cmd:
            console.print(f"[blue]  Build: Running '{build_cmd}' for {task.id}...[/blue]")
            build_result = await self._gate_build(worktree_path, build_cmd, gate_timeout)
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Build", "passed": build_result.passed,
                "details": build_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not build_result.passed:
                console.print(f"[red]  Build failed: {build_result.details[:200]}[/red]")
                feedback_parts.append(f"Build FAILED:\n{build_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Build passed[/green]")

        # L1: lint only the changed files (existing, unchanged)
        console.print(f"[blue]  L1 (general): Auto-checks for {task.id}...[/blue]")
        gate1_result = await self._gate1(worktree_path)
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L1", "passed": gate1_result.passed,
            "details": gate1_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate1_result.passed:
            console.print(f"[red]  L1 failed: {gate1_result.details}[/red]")
            feedback_parts.append(f"L1 (lint) FAILED:\n{gate1_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L1 passed[/green]")

        # Gate 1.5: TEST (between lint and LLM review)
        test_cmd = self._resolve_test_cmd()
        if test_cmd:
            console.print(f"[blue]  Test: Running '{test_cmd}' for {task.id}...[/blue]")
            test_result = await self._gate_test(worktree_path, test_cmd, gate_timeout)
            await self._emit("task:review_update", {
                "task_id": task.id, "gate": "Test", "passed": test_result.passed,
                "details": test_result.details,
            }, db=db, pipeline_id=pipeline_id)
            if not test_result.passed:
                console.print(f"[red]  Test failed: {test_result.details[:200]}[/red]")
                feedback_parts.append(f"Test FAILED:\n{test_result.details}")
                return False, "\n\n".join(feedback_parts)
            console.print("[green]  Test passed[/green]")

        # L2: LLM review (existing, completely unchanged)
        prior_feedback = getattr(task, "review_feedback", None) if task.retry_count > 0 else None
        console.print(
            f"[blue]  L2 (LLM): Code review for {task.id}"
            f"{'  (re-review)' if prior_feedback else ''}...[/blue]"
        )
        reviewer_model = select_model(self._strategy, "reviewer", task.complexity or "medium")
        gate2_result = await gate2_llm_review(
            task.title, task.description, diff, worktree_path,
            model=reviewer_model,
            prior_feedback=prior_feedback,
            project_context=self._snapshot.format_for_reviewer() if self._snapshot else "",
        )
        await self._emit("task:review_update", {
            "task_id": task.id, "gate": "L2", "passed": gate2_result.passed,
            "details": gate2_result.details,
        }, db=db, pipeline_id=pipeline_id)
        if not gate2_result.passed:
            console.print(f"[red]  L2 failed: {gate2_result.details}[/red]")
            feedback_parts.append(f"L2 (LLM code review) FAILED:\n{gate2_result.details}")
            return False, "\n\n".join(feedback_parts)
        console.print("[green]  L2 passed[/green]")

        # Gate 3: skip for now -- merge check is handled by merge_worker
        console.print("[green]  Gate 3 (merge readiness): auto-pass[/green]")
        return True, None

    async def _gate_build(
        self, worktree_path: str, build_cmd: str, timeout: int,
    ) -> GateResult:
        """Gate 0: Run user-configured build command in the worktree."""
        return await self._run_shell_gate(
            worktree_path, build_cmd, timeout, gate_name="gate0_build",
        )

    async def _gate_test(
        self, worktree_path: str, test_cmd: str, timeout: int,
    ) -> GateResult:
        """Gate 1.5: Run user-configured test command in the worktree."""
        return await self._run_shell_gate(
            worktree_path, test_cmd, timeout, gate_name="gate1_5_test",
        )

    async def _run_shell_gate(
        self, worktree_path: str, cmd: str, timeout: int, gate_name: str,
    ) -> GateResult:
        """Execute a shell command as a review gate.

        Runs cmd via subprocess in the worktree. Captures combined
        stdout+stderr, truncated to last 5000 chars. Returns GateResult.
        """
        try:
            proc = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run,
                    cmd,
                    shell=True,
                    cwd=worktree_path,
                    capture_output=True,
                    text=True,
                ),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return GateResult(
                passed=False,
                gate=gate_name,
                details=f"Command timed out after {timeout}s: {cmd}",
            )

        output = (proc.stdout or "") + (proc.stderr or "")
        output = output[-5000:]  # Keep last 5000 chars (tail, most useful)

        if proc.returncode == 0:
            return GateResult(passed=True, gate=gate_name, details=output or "OK")

        return GateResult(
            passed=False,
            gate=gate_name,
            details=f"Exit code {proc.returncode}:\n{output}",
        )

    # existing _gate1 method remains unchanged
```

### Wiring per-pipeline commands into the daemon

In `forge/core/daemon_executor.py`, the `_attempt_merge` method calls `self._run_review`. Before that call, the daemon must set per-pipeline overrides from the DB.

**File: `forge/core/daemon_executor.py` — `_attempt_merge` (lines 143-174)**

Add per-pipeline command resolution before the `_run_review` call:

```python
async def _attempt_merge(
    self, db, merge_worker, worktree_mgr, task,
    task_id: str, worktree_path: str, agent_model: str, pid: str,
) -> None:
    """Review then merge; handles Tier 1 + Tier 2 conflict resolution."""
    diff = _get_diff_vs_main(worktree_path)
    await db.update_task_state(task_id, TaskState.IN_REVIEW.value)
    await self._emit("task:state_changed", {"task_id": task_id, "state": "in_review"}, db=db, pipeline_id=pid)

    # Load per-pipeline build/test commands (if set via web UI)
    pipeline = await db.get_pipeline(pid) if pid else None
    self._pipeline_build_cmd = getattr(pipeline, "build_cmd", None) if pipeline else None
    self._pipeline_test_cmd = getattr(pipeline, "test_cmd", None) if pipeline else None

    passed, feedback = await self._run_review(task, worktree_path, diff, db=db, pipeline_id=pid)
    # ...rest of method unchanged...
```

---

## 5. Frontend Changes

### 5a. TaskForm.tsx — Add Build & Test Command Inputs

**File: `web/src/components/task/TaskForm.tsx`**

Add `buildCmd` and `testCmd` to the `TaskFormData` interface and add two new text input fields.

**Interface change (line 12-18):**
```typescript
// BEFORE
export interface TaskFormData {
  description: string;
  priority: Priority;
  additionalContext: string;
  images: ImageAttachment[];
  branchName: string;
}

// AFTER
export interface TaskFormData {
  description: string;
  priority: Priority;
  additionalContext: string;
  images: ImageAttachment[];
  branchName: string;
  buildCmd: string;
  testCmd: string;
}
```

**New UI fields** — insert between the branch name input (line 417) and the additional context textarea (line 419):

```tsx
{/* Build command input */}
<div>
  <label htmlFor="build-cmd" className="block text-sm font-medium text-text-secondary">
    Build command <span className="text-text-dim">(optional)</span>
  </label>
  <input
    id="build-cmd"
    type="text"
    value={value.buildCmd}
    onChange={(e) => onChange({ ...value, buildCmd: e.target.value })}
    placeholder="npm run build"
    className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent font-mono text-sm"
  />
  <p className="mt-1 text-xs text-text-dim">
    Runs before lint check. Overrides FORGE_BUILD_CMD env var for this pipeline.
  </p>
</div>

{/* Test command input */}
<div>
  <label htmlFor="test-cmd" className="block text-sm font-medium text-text-secondary">
    Test command <span className="text-text-dim">(optional)</span>
  </label>
  <input
    id="test-cmd"
    type="text"
    value={value.testCmd}
    onChange={(e) => onChange({ ...value, testCmd: e.target.value })}
    placeholder="pytest"
    className="mt-1 block w-full rounded-lg border border-border-color bg-surface-3 px-4 py-2 text-text-primary placeholder:text-text-dim focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent font-mono text-sm"
  />
  <p className="mt-1 text-xs text-text-dim">
    Runs after lint, before LLM review. Overrides FORGE_TEST_CMD env var for this pipeline.
  </p>
</div>
```

**Caller update:** Wherever `TaskFormData` is initialized (in the task creation page), add default values:

```typescript
const [formData, setFormData] = useState<TaskFormData>({
  description: "",
  priority: "medium",
  additionalContext: "",
  images: [],
  branchName: "",
  buildCmd: "",
  testCmd: "",
});
```

**Submit handler update:** When posting to `/api/tasks`, include the new fields:

```typescript
const body = {
  description: formData.description,
  project_path: projectPath,
  model_strategy: "auto",
  images: encodedImages,
  branch_name: formData.branchName || null,
  build_cmd: formData.buildCmd || null,   // null if empty string
  test_cmd: formData.testCmd || null,     // null if empty string
};
```

### 5b. History Page — Show build/test commands

**File: `web/src/app/history/page.tsx`**

Add `build_cmd` and `test_cmd` to `HistoryItem` interface (line 8-15):

```typescript
// BEFORE
interface HistoryItem {
  pipeline_id: string;
  description: string;
  phase: string;
  created_at: string;
  duration: number | null;
  task_count: number;
}

// AFTER
interface HistoryItem {
  pipeline_id: string;
  description: string;
  phase: string;
  created_at: string;
  duration: number | null;
  task_count: number;
  build_cmd: string | null;
  test_cmd: string | null;
}
```

Show as a small badge in the existing Pipeline cell (line 177-179), rather than adding new columns to the already-dense table:

```tsx
<td>
  <div className="history-pipeline-cell">
    <span className="history-pipeline-title">{item.description}</span>
    <span className="history-id">{item.pipeline_id.slice(0, 8)}</span>
    {(item.build_cmd || item.test_cmd) && (
      <span
        className="history-gates-badge"
        style={{
          fontSize: 10,
          padding: "1px 5px",
          borderRadius: 4,
          background: "var(--bg-surface-3)",
          border: "1px solid var(--border)",
          color: "var(--text-dim)",
          marginLeft: 6,
        }}
        title={
          [item.build_cmd && `Build: ${item.build_cmd}`,
           item.test_cmd && `Test: ${item.test_cmd}`]
            .filter(Boolean).join("\n")
        }
      >
        {item.build_cmd ? "B" : ""}{item.test_cmd ? "T" : ""}
      </span>
    )}
  </div>
</td>
```

The corresponding backend `/api/history` endpoint must include `build_cmd` and `test_cmd` from `PipelineRow` in the response.

### 5c. AgentCard.tsx — Update Review Gate Dots

**File: `web/src/components/task/AgentCard.tsx`**

Update the gate label mapping in the review gates detail section (line 186-189):

**Before:**
```tsx
const label =
  gate.gate === "L1" ? "L1 (general)" :
  gate.gate === "L2" ? "L2 (LLM)" :
  String(gate.gate);
```

**After:**
```tsx
const GATE_LABELS: Record<string, string> = {
  Build: "Build",
  L1: "Lint",
  Test: "Test",
  L2: "Review",
};
// ...inside the map callback:
const label = GATE_LABELS[gate.gate] || String(gate.gate);
```

Define `GATE_LABELS` as a module-level constant (outside the component, near line 20):

```tsx
const GATE_LABELS: Record<string, string> = {
  Build: "Build",
  L1: "Lint",
  Test: "Test",
  L2: "Review",
};
```

Update the footer dots to show gate names on hover (line 207-213):

**Before:**
```tsx
<div className="review-gates-mini">
  {task.reviewGates.map((gate, i) => (
    <div
      key={`dot-${gate.gate}-${i}`}
      className={`gate-dot ${gate.result === "pass" ? "pass" : gate.result === "fail" ? "fail" : "pending-gate"}`}
    />
  ))}
</div>
```

**After:**
```tsx
<div className="review-gates-mini">
  {task.reviewGates.map((gate, i) => (
    <div
      key={`dot-${gate.gate}-${i}`}
      className={`gate-dot ${gate.result === "pass" ? "pass" : gate.result === "fail" ? "fail" : "pending-gate"}`}
      title={`${GATE_LABELS[gate.gate] || gate.gate}: ${gate.result}`}
    />
  ))}
</div>
```

### 5d. taskStore.ts — No Changes Needed

The existing `task:review_update` event handler in `taskStore.ts` is generic — it appends `{ gate, result, details }` to `reviewGates[]` regardless of the gate name. It already handles `"Build"` and `"Test"` gates without modification.

The `state_changed` handler already clears `reviewGates` on retry (`newState === "working"`), so new gates will be cleared and re-populated correctly on retries.

---

## 6. Error Handling

### Timeout

- Gate timeout = `agent_timeout_seconds // 2` (default: 300s with current `agent_timeout_seconds=600`)
- On timeout, `_run_shell_gate` returns `GateResult(passed=False, details="Command timed out after {timeout}s: {cmd}")`
- The timeout feedback is passed to the agent on retry so it can optimize (e.g., reduce test scope)

### Command not found / permission error

- `subprocess.run` with `shell=True` will return non-zero exit code
- Captured in stderr, included in `GateResult.details`
- Agent sees the error on retry and can attempt to fix (e.g., install missing dependencies)

### Output truncation

- Combined stdout+stderr is truncated to **last 5000 chars** (tail) since the most relevant errors are typically at the end
- This prevents massive test outputs from bloating the DB, WebSocket payloads, and UI

### Retry flow

When a gate fails:
1. `_run_review` returns `(False, feedback_string)`
2. `_attempt_merge` calls `_handle_retry` with `review_feedback=feedback`
3. On the next attempt, `_build_retry_prompt` includes the gate failure output
4. The agent sees exactly what failed and can fix it
5. On retry, `state_changed -> "working"` clears `reviewGates[]` in the frontend, so only the current attempt's gates are shown

---

## 7. Backward Compatibility

All changes are fully backward compatible:

| Scenario | Behavior |
|----------|----------|
| No `FORGE_BUILD_CMD` set, no per-pipeline `build_cmd` | Gate 0 (Build) skipped silently — not emitted to frontend, not shown in UI |
| No `FORGE_TEST_CMD` set, no per-pipeline `test_cmd` | Gate 1.5 (Test) skipped silently — not emitted, not shown |
| Per-pipeline `build_cmd` set | Overrides `FORGE_BUILD_CMD` for that pipeline only |
| `FORGE_BUILD_CMD` set, no per-pipeline override | All pipelines use the global build command |
| Existing DB without `build_cmd`/`test_cmd` columns | `_add_missing_columns` migration adds them as nullable — existing rows get `NULL` (= skip) |
| CLI `forge run` (no web UI) | Uses `ForgeSettings` env vars only — no per-pipeline override |
| Frontend with old `reviewGates` data | Gate dots render with the generic `String(gate.gate)` fallback label |

---

## 8. Gate 3 Plugin System (Future)

Gate 3 currently auto-passes. A future plugin system could allow users to register custom gates:

```toml
# Future: .forge/gates.toml
[gates.gate3]
name = "Security Scan"
command = "trivy fs --severity HIGH,CRITICAL ."
timeout = 120
required = true  # false = advisory only (warn but don't block)
```

The `_run_shell_gate` helper introduced by this feature is designed to be reusable for this purpose. Gate 3 would simply call `_run_shell_gate` with the configured command, using the same timeout and output truncation logic.

To implement this in the future:
1. Add a `custom_gates` field to `ForgeSettings` (list of gate configs)
2. In `_run_review`, after L2 and before the auto-pass, iterate over custom gates
3. Each custom gate emits `task:review_update` with a unique gate name
4. The frontend's generic gate rendering handles any gate name automatically

---

## 9. Files Changed Summary

| File | Change |
|------|--------|
| `forge/config/settings.py` | Add `build_cmd` and `test_cmd` fields |
| `forge/storage/db.py` | Add `build_cmd`, `test_cmd` to `PipelineRow`; update `create_pipeline` signature |
| `forge/api/models/schemas.py` | Add `build_cmd`, `test_cmd` to `CreateTaskRequest` |
| `forge/api/routes/tasks.py` | Pass `build_cmd`, `test_cmd` to `create_pipeline` |
| `forge/core/daemon_review.py` | Add `_gate_build`, `_gate_test`, `_run_shell_gate`, `_resolve_build_cmd`, `_resolve_test_cmd`; update `_run_review` |
| `forge/core/daemon_executor.py` | Load per-pipeline cmds from DB before `_run_review` call |
| `web/src/components/task/TaskForm.tsx` | Add `buildCmd`, `testCmd` to `TaskFormData` + two new input fields |
| `web/src/components/task/AgentCard.tsx` | Add `GATE_LABELS` constant, update gate label rendering and dot tooltips |
| `web/src/app/history/page.tsx` | Add `build_cmd`/`test_cmd` to `HistoryItem` + inline badge |
| `forge/api/routes/history.py` | Include `build_cmd`/`test_cmd` in history list response |

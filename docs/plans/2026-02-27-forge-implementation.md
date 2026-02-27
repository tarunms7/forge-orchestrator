# Forge Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build Forge, a hybrid multi-agent orchestration engine where LLMs propose and code disposes.

**Architecture:** Deterministic orchestration engine validates LLM-generated TaskGraphs, schedules work across isolated agent subprocesses, enforces coding standards programmatically, and gates all merges through a mandatory 3-gate review pipeline.

**Tech Stack:** Python 3.12+, claude_agent_sdk, SQLAlchemy 2.0 async, Pydantic v2, psutil, click, Rich/Textual, pytest

**Design Doc:** `docs/plans/2026-02-27-forge-design.md`

**Cross-Session Continuity:** After completing each phase, update `.forge/build-log.md` and `.forge/session-handoff.md`. Every new session reads these files first.

---

## Phase 1: Foundation (Models, Config, Errors, DB)

Everything else depends on this. Establishes project structure, Pydantic models, error hierarchy, configuration, and database layer.

### Task 1.1: Project Scaffold & Dependencies

**Files:**
- Create: `pyproject.toml`
- Create: `forge/__init__.py`
- Create: `forge/core/__init__.py`
- Create: `forge/agents/__init__.py`
- Create: `forge/review/__init__.py`
- Create: `forge/merge/__init__.py`
- Create: `forge/registry/__init__.py`
- Create: `forge/storage/__init__.py`
- Create: `forge/cli/__init__.py`
- Create: `forge/tui/__init__.py`
- Create: `forge/config/__init__.py`
- Create: `STANDARDS.md`
- Create: `.forge/build-log.md`
- Create: `.forge/decisions.md`

**Step 1: Create pyproject.toml**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "forge-orchestrator"
version = "0.1.0"
description = "Hybrid multi-agent orchestration engine"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.0",
    "sqlalchemy[asyncio]>=2.0",
    "aiosqlite>=0.20",
    "psutil>=5.9",
    "click>=8.1",
    "rich>=13.0",
    "textual>=0.50",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "ruff>=0.3",
]
postgres = [
    "asyncpg>=0.29",
]

[project.scripts]
forge = "forge.cli.main:cli"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["forge"]
python_files = ["*_test.py"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

**Step 2: Create all `__init__.py` files (empty) and package directories**

```bash
mkdir -p forge/{core,agents,review,merge,registry,storage,cli,tui,config}
touch forge/__init__.py forge/{core,agents,review,merge,registry,storage,cli,tui,config}/__init__.py
```

**Step 3: Create STANDARDS.md**

Write `STANDARDS.md` at project root with the coding standards from the design doc (architecture, reuse-first, modularity, error handling, testing sections).

**Step 4: Create .forge/ continuity files**

```bash
mkdir -p .forge
```

`.forge/build-log.md`:
```markdown
# Forge Build Log

## Completed
- [ ] Phase 1: Foundation
- [ ] Phase 2: TaskGraph Validation
- [ ] Phase 3: State Machine
- [ ] Phase 4: Resource Monitor
- [ ] Phase 5: Scheduler
- [ ] Phase 6: Git Worktree Management
- [ ] Phase 7: Agent Runtime
- [ ] Phase 8: Planner
- [ ] Phase 9: Review Pipeline
- [ ] Phase 10: Merge Pipeline
- [ ] Phase 11: Module Registry
- [ ] Phase 12: CLI
- [ ] Phase 13: Orchestration Engine
- [ ] Phase 14: Cross-Session Continuity
- [ ] Phase 15: TUI Dashboard
```

`.forge/decisions.md`:
```markdown
# Architectural Decisions

## 2026-02-27: Initial Decisions
- Hybrid orchestration: LLM plans, code enforces
- Claude Code primary backend, adapter interface for others
- SQLite default, Postgres optional (SQLAlchemy abstracts)
- psutil for resource monitoring, dynamic throttle
- Mandatory 3-gate review pipeline
- Max ~4 concurrent agents (research-backed)
- TDD throughout, pytest + pytest-asyncio
```

**Step 5: Install dependencies and verify**

```bash
pip install -e ".[dev]"
```

**Step 6: Commit**

```bash
git add -A
git commit -m "feat: project scaffold with dependencies and standards"
```

---

### Task 1.2: Error Hierarchy

**Files:**
- Create: `forge/core/errors.py`
- Create: `forge/core/errors_test.py`

**Step 1: Write the failing test**

```python
# forge/core/errors_test.py
from forge.core.errors import (
    ForgeError,
    ValidationError,
    CyclicDependencyError,
    FileConflictError,
    SchedulerError,
    ResourceExhaustedError,
    AgentError,
    AgentTimeoutError,
    ReviewError,
    MergeError,
    MergeConflictError,
)


def test_forge_error_is_base():
    err = ForgeError("something broke")
    assert isinstance(err, Exception)
    assert str(err) == "something broke"


def test_validation_error_inherits_forge():
    err = ValidationError("bad graph")
    assert isinstance(err, ForgeError)


def test_cyclic_dependency_carries_cycle():
    err = CyclicDependencyError(cycle=["task-1", "task-2", "task-1"])
    assert isinstance(err, ValidationError)
    assert err.cycle == ["task-1", "task-2", "task-1"]
    assert "task-1" in str(err)


def test_file_conflict_carries_details():
    err = FileConflictError(
        file_path="src/main.py",
        task_a="task-1",
        task_b="task-2",
    )
    assert isinstance(err, ValidationError)
    assert err.file_path == "src/main.py"
    assert "src/main.py" in str(err)


def test_resource_exhausted_carries_metric():
    err = ResourceExhaustedError(metric="cpu", value=95.0, threshold=80.0)
    assert isinstance(err, SchedulerError)
    assert err.metric == "cpu"


def test_agent_timeout_carries_seconds():
    err = AgentTimeoutError(agent_id="agent-1", timeout_seconds=1800)
    assert isinstance(err, AgentError)
    assert err.agent_id == "agent-1"


def test_merge_conflict_carries_files():
    err = MergeConflictError(conflicting_files=["a.py", "b.py"])
    assert isinstance(err, MergeError)
    assert err.conflicting_files == ["a.py", "b.py"]
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/errors_test.py -v
```
Expected: FAIL — cannot import errors module

**Step 3: Write minimal implementation**

```python
# forge/core/errors.py
"""Forge error hierarchy. Every error carries context: what failed, why, what to do."""


class ForgeError(Exception):
    """Base error for all Forge operations."""


class ValidationError(ForgeError):
    """TaskGraph validation failed."""


class CyclicDependencyError(ValidationError):
    """TaskGraph contains a dependency cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Cyclic dependency detected: {' -> '.join(cycle)}")


class FileConflictError(ValidationError):
    """Two tasks claim ownership of the same file."""

    def __init__(self, file_path: str, task_a: str, task_b: str) -> None:
        self.file_path = file_path
        self.task_a = task_a
        self.task_b = task_b
        super().__init__(
            f"File conflict: '{file_path}' claimed by both '{task_a}' and '{task_b}'"
        )


class SchedulerError(ForgeError):
    """Scheduler operation failed."""


class ResourceExhaustedError(SchedulerError):
    """System resources exceeded threshold."""

    def __init__(self, metric: str, value: float, threshold: float) -> None:
        self.metric = metric
        self.value = value
        self.threshold = threshold
        super().__init__(
            f"Resource exhausted: {metric} at {value:.1f}% (threshold: {threshold:.1f}%)"
        )


class AgentError(ForgeError):
    """Agent operation failed."""


class AgentTimeoutError(AgentError):
    """Agent exceeded its time limit."""

    def __init__(self, agent_id: str, timeout_seconds: int) -> None:
        self.agent_id = agent_id
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Agent '{agent_id}' timed out after {timeout_seconds}s"
        )


class ReviewError(ForgeError):
    """Review pipeline failed."""


class MergeError(ForgeError):
    """Merge operation failed."""


class MergeConflictError(MergeError):
    """Merge produced conflicts."""

    def __init__(self, conflicting_files: list[str]) -> None:
        self.conflicting_files = conflicting_files
        super().__init__(
            f"Merge conflicts in: {', '.join(conflicting_files)}"
        )
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/errors_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/errors.py forge/core/errors_test.py
git commit -m "feat: error hierarchy with contextual error classes"
```

---

### Task 1.3: Pydantic Models

**Files:**
- Create: `forge/core/models.py`
- Create: `forge/core/models_test.py`

**Step 1: Write the failing test**

```python
# forge/core/models_test.py
import pytest
from pydantic import ValidationError as PydanticValidationError

from forge.core.models import (
    Complexity,
    TaskDefinition,
    TaskGraph,
    TaskState,
    TaskRecord,
    AgentRecord,
    AgentState,
)


class TestTaskDefinition:
    def test_valid_task(self):
        task = TaskDefinition(
            id="task-1",
            title="Create user model",
            description="Build the user data model",
            files=["src/models/user.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        )
        assert task.id == "task-1"
        assert task.files == ["src/models/user.py"]

    def test_empty_id_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskDefinition(
                id="",
                title="Bad",
                description="No",
                files=[],
                depends_on=[],
                complexity=Complexity.LOW,
            )

    def test_empty_title_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskDefinition(
                id="task-1",
                title="",
                description="No",
                files=[],
                depends_on=[],
                complexity=Complexity.LOW,
            )

    def test_depends_on_defaults_empty(self):
        task = TaskDefinition(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
        )
        assert task.depends_on == []
        assert task.complexity == Complexity.MEDIUM


class TestTaskGraph:
    def test_valid_graph(self):
        graph = TaskGraph(
            tasks=[
                TaskDefinition(
                    id="task-1",
                    title="First",
                    description="Do first",
                    files=["a.py"],
                ),
                TaskDefinition(
                    id="task-2",
                    title="Second",
                    description="Do second",
                    files=["b.py"],
                    depends_on=["task-1"],
                ),
            ]
        )
        assert len(graph.tasks) == 2

    def test_empty_tasks_rejected(self):
        with pytest.raises(PydanticValidationError):
            TaskGraph(tasks=[])


class TestTaskRecord:
    def test_default_state_is_todo(self):
        record = TaskRecord(
            id="task-1",
            title="Something",
            description="Desc",
            files=["a.py"],
            depends_on=[],
            complexity=Complexity.LOW,
        )
        assert record.state == TaskState.TODO
        assert record.retry_count == 0
        assert record.assigned_agent is None


class TestAgentRecord:
    def test_default_state_is_idle(self):
        agent = AgentRecord(id="agent-1")
        assert agent.state == AgentState.IDLE
        assert agent.current_task is None
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/models_test.py -v
```
Expected: FAIL — cannot import models

**Step 3: Write minimal implementation**

```python
# forge/core/models.py
"""Pydantic models for Forge. All data flows through typed schemas."""

from enum import Enum

from pydantic import BaseModel, Field, field_validator


class Complexity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TaskState(str, Enum):
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    IN_REVIEW = "in_review"
    MERGING = "merging"
    DONE = "done"
    CANCELLED = "cancelled"
    ERROR = "error"


class AgentState(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"


class TaskDefinition(BaseModel):
    """A task as defined by the planner. Immutable spec."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str
    files: list[str]
    depends_on: list[str] = Field(default_factory=list)
    complexity: Complexity = Complexity.MEDIUM

    @field_validator("files")
    @classmethod
    def files_not_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("Task must declare at least one file")
        return v


class TaskGraph(BaseModel):
    """The planner's output: a validated set of tasks with dependencies."""

    tasks: list[TaskDefinition] = Field(min_length=1)


class TaskRecord(BaseModel):
    """Runtime state of a task. Mutable projection."""

    id: str
    title: str
    description: str
    files: list[str]
    depends_on: list[str]
    complexity: Complexity
    state: TaskState = TaskState.TODO
    assigned_agent: str | None = None
    retry_count: int = 0
    branch_name: str | None = None
    worktree_path: str | None = None

    @classmethod
    def from_definition(cls, defn: TaskDefinition) -> "TaskRecord":
        return cls(
            id=defn.id,
            title=defn.title,
            description=defn.description,
            files=list(defn.files),
            depends_on=list(defn.depends_on),
            complexity=defn.complexity,
        )


class AgentRecord(BaseModel):
    """Runtime state of an agent."""

    id: str
    state: AgentState = AgentState.IDLE
    current_task: str | None = None
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/models_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/models.py forge/core/models_test.py
git commit -m "feat: Pydantic models for TaskGraph, TaskRecord, AgentRecord"
```

---

### Task 1.4: Configuration

**Files:**
- Create: `forge/config/settings.py`
- Create: `forge/config/settings_test.py`

**Step 1: Write the failing test**

```python
# forge/config/settings_test.py
from forge.config.settings import ForgeSettings


def test_default_settings():
    s = ForgeSettings()
    assert s.max_agents == 4
    assert s.cpu_threshold == 80.0
    assert s.memory_threshold_pct == 20.0
    assert s.agent_timeout_seconds == 1800
    assert s.max_retries == 3
    assert s.db_url == "sqlite+aiosqlite:///forge.db"
    assert s.context_rotation_tokens == 80_000


def test_override_via_constructor():
    s = ForgeSettings(max_agents=2, cpu_threshold=90.0)
    assert s.max_agents == 2
    assert s.cpu_threshold == 90.0


def test_postgres_url():
    s = ForgeSettings(db_url="postgresql+asyncpg://localhost/forge")
    assert "postgresql" in s.db_url
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/config/settings_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/config/settings.py
"""Forge configuration. All settings in one place with sensible defaults."""

from pydantic_settings import BaseSettings


class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Agent limits
    max_agents: int = 4
    agent_timeout_seconds: int = 1800
    context_rotation_tokens: int = 80_000
    max_retries: int = 3

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 20.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Polling
    scheduler_poll_interval: float = 1.0
```

Note: This requires `pydantic-settings` — add it to `pyproject.toml` dependencies.

**Step 4: Run test to verify it passes**

```bash
pytest forge/config/settings_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/config/settings.py forge/config/settings_test.py pyproject.toml
git commit -m "feat: configuration with sensible defaults and env override"
```

---

### Task 1.5: Database Layer

**Files:**
- Create: `forge/storage/db.py`
- Create: `forge/storage/db_test.py`

**Step 1: Write the failing test**

```python
# forge/storage/db_test.py
import pytest
from forge.storage.db import (
    Database,
    TaskRow,
    AgentRow,
)


@pytest.fixture
async def db():
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.initialize()
    yield database
    await database.close()


async def test_create_and_get_task(db: Database):
    await db.create_task(
        id="task-1",
        title="Test task",
        description="A test",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    task = await db.get_task("task-1")
    assert task is not None
    assert task.title == "Test task"
    assert task.state == "todo"


async def test_get_nonexistent_task(db: Database):
    task = await db.get_task("nope")
    assert task is None


async def test_update_task_state(db: Database):
    await db.create_task(
        id="task-1",
        title="Test",
        description="A test",
        files=["a.py"],
        depends_on=[],
        complexity="low",
    )
    await db.update_task_state("task-1", "in_progress")
    task = await db.get_task("task-1")
    assert task.state == "in_progress"


async def test_list_tasks_by_state(db: Database):
    await db.create_task(
        id="t1", title="T1", description="D", files=["a.py"],
        depends_on=[], complexity="low",
    )
    await db.create_task(
        id="t2", title="T2", description="D", files=["b.py"],
        depends_on=[], complexity="low",
    )
    await db.update_task_state("t1", "in_progress")
    in_progress = await db.list_tasks(state="in_progress")
    assert len(in_progress) == 1
    assert in_progress[0].id == "t1"


async def test_create_and_get_agent(db: Database):
    await db.create_agent(id="agent-1")
    agent = await db.get_agent("agent-1")
    assert agent is not None
    assert agent.state == "idle"


async def test_assign_task_to_agent(db: Database):
    await db.create_task(
        id="task-1", title="T", description="D", files=["a.py"],
        depends_on=[], complexity="low",
    )
    await db.create_agent(id="agent-1")
    await db.assign_task("task-1", "agent-1")
    task = await db.get_task("task-1")
    assert task.assigned_agent == "agent-1"
    agent = await db.get_agent("agent-1")
    assert agent.current_task == "task-1"
    assert agent.state == "working"
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/storage/db_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/storage/db.py
"""Database layer. SQLAlchemy 2.0 async. SQLite default, Postgres optional."""

from dataclasses import dataclass

from sqlalchemy import String, JSON, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class TaskRow(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    title: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String)
    files: Mapped[list] = mapped_column(JSON)
    depends_on: Mapped[list] = mapped_column(JSON)
    complexity: Mapped[str] = mapped_column(String)
    state: Mapped[str] = mapped_column(String, default="todo")
    assigned_agent: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(default=0)
    branch_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    worktree_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class AgentRow(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="idle")
    current_task: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class Database:
    """Async database interface. Thin wrapper over SQLAlchemy."""

    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self) -> None:
        await self._engine.dispose()

    async def create_task(
        self,
        id: str,
        title: str,
        description: str,
        files: list[str],
        depends_on: list[str],
        complexity: str,
    ) -> None:
        async with self._session_factory() as session:
            row = TaskRow(
                id=id, title=title, description=description,
                files=files, depends_on=depends_on, complexity=complexity,
            )
            session.add(row)
            await session.commit()

    async def get_task(self, task_id: str) -> TaskRow | None:
        async with self._session_factory() as session:
            return await session.get(TaskRow, task_id)

    async def update_task_state(self, task_id: str, state: str) -> None:
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.state = state
                await session.commit()

    async def list_tasks(self, state: str | None = None) -> list[TaskRow]:
        async with self._session_factory() as session:
            stmt = select(TaskRow)
            if state:
                stmt = stmt.where(TaskRow.state == state)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def create_agent(self, id: str) -> None:
        async with self._session_factory() as session:
            row = AgentRow(id=id)
            session.add(row)
            await session.commit()

    async def get_agent(self, agent_id: str) -> AgentRow | None:
        async with self._session_factory() as session:
            return await session.get(AgentRow, agent_id)

    async def assign_task(self, task_id: str, agent_id: str) -> None:
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            agent = await session.get(AgentRow, agent_id)
            if task and agent:
                task.assigned_agent = agent_id
                agent.current_task = task_id
                agent.state = "working"
                await session.commit()
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/storage/db_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/storage/db.py forge/storage/db_test.py
git commit -m "feat: async database layer with SQLAlchemy 2.0"
```

---

## Phase 2: TaskGraph Validation

The core of "code enforces." Validates planner output before any work begins.

### Task 2.1: Cycle Detection

**Files:**
- Create: `forge/core/validator.py`
- Create: `forge/core/validator_test.py`

**Step 1: Write the failing test**

```python
# forge/core/validator_test.py
import pytest

from forge.core.errors import CyclicDependencyError, FileConflictError, ValidationError
from forge.core.models import TaskDefinition, TaskGraph, Complexity
from forge.core.validator import validate_task_graph


def _task(id: str, files: list[str], depends_on: list[str] | None = None) -> TaskDefinition:
    """Helper to create a TaskDefinition with minimal boilerplate."""
    return TaskDefinition(
        id=id,
        title=f"Task {id}",
        description=f"Description for {id}",
        files=files,
        depends_on=depends_on or [],
        complexity=Complexity.LOW,
    )


class TestCycleDetection:
    def test_no_cycles_passes(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["b"]),
        ])
        validate_task_graph(graph)  # Should not raise

    def test_self_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["a"]),
        ])
        with pytest.raises(CyclicDependencyError) as exc_info:
            validate_task_graph(graph)
        assert "a" in exc_info.value.cycle

    def test_two_node_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["b"]),
            _task("b", ["b.py"], depends_on=["a"]),
        ])
        with pytest.raises(CyclicDependencyError):
            validate_task_graph(graph)

    def test_three_node_cycle_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["c"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["b"]),
        ])
        with pytest.raises(CyclicDependencyError):
            validate_task_graph(graph)

    def test_diamond_no_cycle(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"], depends_on=["a"]),
            _task("c", ["c.py"], depends_on=["a"]),
            _task("d", ["d.py"], depends_on=["b", "c"]),
        ])
        validate_task_graph(graph)  # Should not raise


class TestFileConflictDetection:
    def test_no_conflicts_passes(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("b", ["b.py"]),
        ])
        validate_task_graph(graph)  # Should not raise

    def test_same_file_two_tasks_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["shared.py"]),
            _task("b", ["shared.py"]),
        ])
        with pytest.raises(FileConflictError) as exc_info:
            validate_task_graph(graph)
        assert exc_info.value.file_path == "shared.py"

    def test_partial_overlap_detected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py", "shared.py"]),
            _task("b", ["b.py", "shared.py"]),
        ])
        with pytest.raises(FileConflictError):
            validate_task_graph(graph)


class TestDependencyRefValidation:
    def test_unknown_dependency_rejected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"], depends_on=["nonexistent"]),
        ])
        with pytest.raises(ValidationError, match="nonexistent"):
            validate_task_graph(graph)

    def test_duplicate_task_ids_rejected(self):
        graph = TaskGraph(tasks=[
            _task("a", ["a.py"]),
            _task("a", ["b.py"]),
        ])
        with pytest.raises(ValidationError, match="Duplicate"):
            validate_task_graph(graph)
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/validator_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/validator.py
"""TaskGraph validation. Enforces structural correctness before any work begins."""

from forge.core.errors import CyclicDependencyError, FileConflictError, ValidationError
from forge.core.models import TaskGraph


def validate_task_graph(graph: TaskGraph) -> None:
    """Validate a TaskGraph. Raises on first error found."""
    _check_duplicate_ids(graph)
    _check_dependency_refs(graph)
    _check_cycles(graph)
    _check_file_conflicts(graph)


def _check_duplicate_ids(graph: TaskGraph) -> None:
    seen: set[str] = set()
    for task in graph.tasks:
        if task.id in seen:
            raise ValidationError(f"Duplicate task id: '{task.id}'")
        seen.add(task.id)


def _check_dependency_refs(graph: TaskGraph) -> None:
    valid_ids = {t.id for t in graph.tasks}
    for task in graph.tasks:
        for dep in task.depends_on:
            if dep not in valid_ids:
                raise ValidationError(
                    f"Task '{task.id}' depends on unknown task '{dep}'"
                )


def _check_cycles(graph: TaskGraph) -> None:
    adjacency = {t.id: list(t.depends_on) for t in graph.tasks}
    visited: set[str] = set()
    in_stack: set[str] = set()
    stack_path: list[str] = []

    def dfs(node: str) -> None:
        visited.add(node)
        in_stack.add(node)
        stack_path.append(node)
        for neighbor in adjacency.get(node, []):
            if neighbor in in_stack:
                cycle_start = stack_path.index(neighbor)
                cycle = stack_path[cycle_start:] + [neighbor]
                raise CyclicDependencyError(cycle=cycle)
            if neighbor not in visited:
                dfs(neighbor)
        stack_path.pop()
        in_stack.remove(node)

    for task_id in adjacency:
        if task_id not in visited:
            dfs(task_id)


def _check_file_conflicts(graph: TaskGraph) -> None:
    file_owners: dict[str, str] = {}
    for task in graph.tasks:
        for file_path in task.files:
            if file_path in file_owners:
                raise FileConflictError(
                    file_path=file_path,
                    task_a=file_owners[file_path],
                    task_b=task.id,
                )
            file_owners[file_path] = task.id
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/validator_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/validator.py forge/core/validator_test.py
git commit -m "feat: TaskGraph validation with cycle and file conflict detection"
```

---

## Phase 3: State Machine

### Task 3.1: Task State Machine

**Files:**
- Create: `forge/core/state.py`
- Create: `forge/core/state_test.py`

**Step 1: Write the failing test**

```python
# forge/core/state_test.py
import pytest

from forge.core.errors import ForgeError
from forge.core.models import TaskState
from forge.core.state import TaskStateMachine


class TestValidTransitions:
    def test_todo_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.TODO, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_in_progress_to_in_review(self):
        assert TaskStateMachine.transition(TaskState.IN_PROGRESS, TaskState.IN_REVIEW) == TaskState.IN_REVIEW

    def test_in_review_to_merging(self):
        assert TaskStateMachine.transition(TaskState.IN_REVIEW, TaskState.MERGING) == TaskState.MERGING

    def test_merging_to_done(self):
        assert TaskStateMachine.transition(TaskState.MERGING, TaskState.DONE) == TaskState.DONE

    def test_in_review_rejected_back_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.IN_REVIEW, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_merging_rejected_back_to_in_progress(self):
        assert TaskStateMachine.transition(TaskState.MERGING, TaskState.IN_PROGRESS) == TaskState.IN_PROGRESS

    def test_any_to_cancelled(self):
        for state in [TaskState.TODO, TaskState.IN_PROGRESS, TaskState.IN_REVIEW]:
            assert TaskStateMachine.transition(state, TaskState.CANCELLED) == TaskState.CANCELLED

    def test_any_to_error(self):
        for state in [TaskState.TODO, TaskState.IN_PROGRESS, TaskState.IN_REVIEW, TaskState.MERGING]:
            assert TaskStateMachine.transition(state, TaskState.ERROR) == TaskState.ERROR


class TestInvalidTransitions:
    def test_done_to_anything_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.DONE, TaskState.TODO)

    def test_cancelled_to_anything_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.CANCELLED, TaskState.TODO)

    def test_todo_to_done_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.TODO, TaskState.DONE)

    def test_todo_to_merging_rejected(self):
        with pytest.raises(ForgeError, match="Invalid transition"):
            TaskStateMachine.transition(TaskState.TODO, TaskState.MERGING)


class TestCanTransition:
    def test_valid_returns_true(self):
        assert TaskStateMachine.can_transition(TaskState.TODO, TaskState.IN_PROGRESS) is True

    def test_invalid_returns_false(self):
        assert TaskStateMachine.can_transition(TaskState.DONE, TaskState.TODO) is False
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/state_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/state.py
"""Task state machine. Enforces valid transitions deterministically."""

from forge.core.errors import ForgeError
from forge.core.models import TaskState

# Map of current_state -> set of allowed next states
_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.TODO: {TaskState.IN_PROGRESS, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.IN_PROGRESS: {TaskState.IN_REVIEW, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.IN_REVIEW: {TaskState.MERGING, TaskState.IN_PROGRESS, TaskState.CANCELLED, TaskState.ERROR},
    TaskState.MERGING: {TaskState.DONE, TaskState.IN_PROGRESS, TaskState.ERROR},
    TaskState.DONE: set(),
    TaskState.CANCELLED: set(),
    TaskState.ERROR: set(),
}


class TaskStateMachine:
    """Deterministic task state transitions. No LLM involved."""

    @staticmethod
    def can_transition(current: TaskState, target: TaskState) -> bool:
        return target in _TRANSITIONS.get(current, set())

    @staticmethod
    def transition(current: TaskState, target: TaskState) -> TaskState:
        if not TaskStateMachine.can_transition(current, target):
            raise ForgeError(
                f"Invalid transition: {current.value} -> {target.value}"
            )
        return target
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/state_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/state.py forge/core/state_test.py
git commit -m "feat: deterministic task state machine with bounded transitions"
```

---

## Phase 4: Resource Monitor

### Task 4.1: System Resource Monitor

**Files:**
- Create: `forge/core/monitor.py`
- Create: `forge/core/monitor_test.py`

**Step 1: Write the failing test**

```python
# forge/core/monitor_test.py
from unittest.mock import patch

from forge.core.monitor import ResourceMonitor, ResourceSnapshot


def test_snapshot_has_required_fields():
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert snap.cpu_percent == 50.0
    assert snap.memory_available_pct == 60.0
    assert snap.disk_free_gb == 100.0


def test_can_dispatch_when_resources_healthy():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is True


def test_cannot_dispatch_when_cpu_high():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=90.0, memory_available_pct=60.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is False


def test_cannot_dispatch_when_memory_low():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=10.0, disk_free_gb=100.0)
    assert monitor.can_dispatch(snap) is False


def test_cannot_dispatch_when_disk_low():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=2.0)
    assert monitor.can_dispatch(snap) is False


def test_take_snapshot_returns_real_values():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = monitor.take_snapshot()
    assert 0.0 <= snap.cpu_percent <= 100.0
    assert 0.0 <= snap.memory_available_pct <= 100.0
    assert snap.disk_free_gb >= 0.0


def test_blocked_reason_reports_all_violations():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=95.0, memory_available_pct=10.0, disk_free_gb=2.0)
    reasons = monitor.blocked_reasons(snap)
    assert len(reasons) == 3
    assert any("cpu" in r.lower() for r in reasons)
    assert any("memory" in r.lower() for r in reasons)
    assert any("disk" in r.lower() for r in reasons)


def test_healthy_snapshot_no_blocked_reasons():
    monitor = ResourceMonitor(cpu_threshold=80.0, memory_threshold_pct=20.0, disk_threshold_gb=5.0)
    snap = ResourceSnapshot(cpu_percent=50.0, memory_available_pct=60.0, disk_free_gb=100.0)
    reasons = monitor.blocked_reasons(snap)
    assert len(reasons) == 0
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/monitor_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/monitor.py
"""Resource monitor. Tracks CPU, memory, disk and gates agent dispatch."""

from dataclasses import dataclass

import psutil


@dataclass(frozen=True)
class ResourceSnapshot:
    """Point-in-time system resource reading."""

    cpu_percent: float
    memory_available_pct: float
    disk_free_gb: float


class ResourceMonitor:
    """Monitors system resources and decides if new agents can be dispatched."""

    def __init__(
        self,
        cpu_threshold: float,
        memory_threshold_pct: float,
        disk_threshold_gb: float,
    ) -> None:
        self._cpu_threshold = cpu_threshold
        self._memory_threshold_pct = memory_threshold_pct
        self._disk_threshold_gb = disk_threshold_gb

    def take_snapshot(self) -> ResourceSnapshot:
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return ResourceSnapshot(
            cpu_percent=psutil.cpu_percent(interval=0.1),
            memory_available_pct=mem.available / mem.total * 100,
            disk_free_gb=disk.free / (1024 ** 3),
        )

    def can_dispatch(self, snapshot: ResourceSnapshot) -> bool:
        return len(self.blocked_reasons(snapshot)) == 0

    def blocked_reasons(self, snapshot: ResourceSnapshot) -> list[str]:
        reasons: list[str] = []
        if snapshot.cpu_percent > self._cpu_threshold:
            reasons.append(
                f"CPU at {snapshot.cpu_percent:.1f}% (threshold: {self._cpu_threshold:.1f}%)"
            )
        if snapshot.memory_available_pct < self._memory_threshold_pct:
            reasons.append(
                f"Memory available {snapshot.memory_available_pct:.1f}% "
                f"(threshold: {self._memory_threshold_pct:.1f}%)"
            )
        if snapshot.disk_free_gb < self._disk_threshold_gb:
            reasons.append(
                f"Disk free {snapshot.disk_free_gb:.1f}GB "
                f"(threshold: {self._disk_threshold_gb:.1f}GB)"
            )
        return reasons
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/monitor_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/monitor.py forge/core/monitor_test.py
git commit -m "feat: resource monitor with CPU/memory/disk thresholds"
```

---

## Phase 5: Scheduler

### Task 5.1: DAG-Aware Scheduler

**Files:**
- Create: `forge/core/scheduler.py`
- Create: `forge/core/scheduler_test.py`

**Step 1: Write the failing test**

```python
# forge/core/scheduler_test.py
import pytest

from forge.core.models import TaskRecord, TaskState, Complexity, AgentRecord, AgentState
from forge.core.scheduler import Scheduler


def _record(id: str, depends_on: list[str] | None = None, state: TaskState = TaskState.TODO) -> TaskRecord:
    return TaskRecord(
        id=id, title=f"Task {id}", description="Desc",
        files=[f"{id}.py"], depends_on=depends_on or [],
        complexity=Complexity.LOW, state=state,
    )


def _agent(id: str, state: AgentState = AgentState.IDLE) -> AgentRecord:
    return AgentRecord(id=id, state=state)


class TestReadyQueue:
    def test_no_deps_task_is_ready(self):
        tasks = [_record("a")]
        ready = Scheduler.ready_tasks(tasks)
        assert [t.id for t in ready] == ["a"]

    def test_dep_not_done_blocks_task(self):
        tasks = [
            _record("a"),
            _record("b", depends_on=["a"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [t.id for t in ready] == ["a"]

    def test_dep_done_unblocks_task(self):
        tasks = [
            _record("a", state=TaskState.DONE),
            _record("b", depends_on=["a"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [t.id for t in ready] == ["b"]

    def test_already_in_progress_not_ready(self):
        tasks = [_record("a", state=TaskState.IN_PROGRESS)]
        ready = Scheduler.ready_tasks(tasks)
        assert ready == []

    def test_diamond_dependency(self):
        tasks = [
            _record("a", state=TaskState.DONE),
            _record("b", depends_on=["a"], state=TaskState.DONE),
            _record("c", depends_on=["a"], state=TaskState.DONE),
            _record("d", depends_on=["b", "c"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [t.id for t in ready] == ["d"]

    def test_partial_deps_done_still_blocked(self):
        tasks = [
            _record("a", state=TaskState.DONE),
            _record("b"),
            _record("c", depends_on=["a", "b"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        # c is blocked by b, but b is ready
        ids = [t.id for t in ready]
        assert "b" in ids
        assert "c" not in ids


class TestDispatchPlan:
    def test_assigns_ready_to_idle_agents(self):
        tasks = [_record("a"), _record("b")]
        agents = [_agent("w1"), _agent("w2")]
        plan = Scheduler.dispatch_plan(tasks, agents, max_agents=4)
        assert len(plan) == 2
        assert plan[0] == ("a", "w1")
        assert plan[1] == ("b", "w2")

    def test_respects_max_agents(self):
        tasks = [_record("a"), _record("b"), _record("c")]
        agents = [_agent("w1"), _agent("w2"), _agent("w3")]
        plan = Scheduler.dispatch_plan(tasks, agents, max_agents=2)
        assert len(plan) == 2

    def test_skips_busy_agents(self):
        tasks = [_record("a"), _record("b")]
        agents = [_agent("w1", state=AgentState.WORKING), _agent("w2")]
        plan = Scheduler.dispatch_plan(tasks, agents, max_agents=4)
        assert len(plan) == 1
        assert plan[0] == ("a", "w2")

    def test_no_ready_tasks_empty_plan(self):
        tasks = [_record("a", state=TaskState.DONE)]
        agents = [_agent("w1")]
        plan = Scheduler.dispatch_plan(tasks, agents, max_agents=4)
        assert plan == []

    def test_no_idle_agents_empty_plan(self):
        tasks = [_record("a")]
        agents = [_agent("w1", state=AgentState.WORKING)]
        plan = Scheduler.dispatch_plan(tasks, agents, max_agents=4)
        assert plan == []
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/scheduler_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/scheduler.py
"""DAG-aware task scheduler. Deterministic dispatch based on dependency state."""

from forge.core.models import AgentRecord, AgentState, TaskRecord, TaskState


class Scheduler:
    """Pure-function scheduler. No side effects — returns dispatch plans."""

    @staticmethod
    def ready_tasks(tasks: list[TaskRecord]) -> list[TaskRecord]:
        """Return tasks that are TODO and have all dependencies DONE."""
        done_ids = {t.id for t in tasks if t.state == TaskState.DONE}
        return [
            t for t in tasks
            if t.state == TaskState.TODO
            and all(dep in done_ids for dep in t.depends_on)
        ]

    @staticmethod
    def dispatch_plan(
        tasks: list[TaskRecord],
        agents: list[AgentRecord],
        max_agents: int,
    ) -> list[tuple[str, str]]:
        """Produce (task_id, agent_id) pairs for dispatch.

        Respects: DAG ordering, idle agents only, max concurrency.
        """
        ready = Scheduler.ready_tasks(tasks)
        idle = [a for a in agents if a.state == AgentState.IDLE]

        working_count = sum(1 for a in agents if a.state == AgentState.WORKING)
        available_slots = max(0, max_agents - working_count)

        plan: list[tuple[str, str]] = []
        for task, agent in zip(ready, idle):
            if len(plan) >= available_slots:
                break
            plan.append((task.id, agent.id))

        return plan
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/scheduler_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/scheduler.py forge/core/scheduler_test.py
git commit -m "feat: DAG-aware scheduler with dispatch planning"
```

---

## Phase 6: Git Worktree Management

### Task 6.1: Worktree Lifecycle

**Files:**
- Create: `forge/merge/worktree.py`
- Create: `forge/merge/worktree_test.py`

**Step 1: Write the failing test**

```python
# forge/merge/worktree_test.py
import os
import subprocess
import pytest

from forge.merge.worktree import WorktreeManager


@pytest.fixture
def git_repo(tmp_path):
    """Create a real git repo for worktree tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    # Need at least one commit for worktrees
    (repo / "README.md").write_text("# Test")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


@pytest.fixture
def manager(git_repo):
    worktrees_dir = git_repo.parent / "worktrees"
    return WorktreeManager(repo_path=str(git_repo), worktrees_dir=str(worktrees_dir))


def test_create_worktree(manager, git_repo):
    path = manager.create("task-1")
    assert os.path.isdir(path)
    assert os.path.exists(os.path.join(path, "README.md"))


def test_create_worktree_branch_name(manager, git_repo):
    path = manager.create("task-1")
    result = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=path, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "forge/task-1"


def test_remove_worktree(manager):
    path = manager.create("task-2")
    assert os.path.isdir(path)
    manager.remove("task-2")
    assert not os.path.isdir(path)


def test_list_worktrees(manager):
    manager.create("task-a")
    manager.create("task-b")
    active = manager.list_active()
    assert "task-a" in active
    assert "task-b" in active


def test_create_duplicate_raises(manager):
    manager.create("task-dup")
    with pytest.raises(ValueError, match="already exists"):
        manager.create("task-dup")


def test_remove_nonexistent_raises(manager):
    with pytest.raises(ValueError, match="does not exist"):
        manager.remove("ghost")
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/merge/worktree_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/merge/worktree.py
"""Git worktree lifecycle management. One worktree per task for isolation."""

import os
import subprocess


class WorktreeManager:
    """Creates, tracks, and removes git worktrees for tasks."""

    def __init__(self, repo_path: str, worktrees_dir: str) -> None:
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir

    def _task_path(self, task_id: str) -> str:
        return os.path.join(self._worktrees_dir, task_id)

    def _branch_name(self, task_id: str) -> str:
        return f"forge/{task_id}"

    def create(self, task_id: str) -> str:
        """Create a worktree for a task. Returns the worktree path."""
        path = self._task_path(task_id)
        if os.path.exists(path):
            raise ValueError(f"Worktree for '{task_id}' already exists: {path}")

        branch = self._branch_name(task_id)
        os.makedirs(self._worktrees_dir, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", branch, path],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        return path

    def remove(self, task_id: str) -> None:
        """Remove a task's worktree and its branch."""
        path = self._task_path(task_id)
        if not os.path.exists(path):
            raise ValueError(f"Worktree for '{task_id}' does not exist")

        subprocess.run(
            ["git", "worktree", "remove", path, "--force"],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        branch = self._branch_name(task_id)
        subprocess.run(
            ["git", "branch", "-D", branch],
            cwd=self._repo,
            capture_output=True,
        )

    def list_active(self) -> list[str]:
        """Return task IDs with active worktrees."""
        if not os.path.isdir(self._worktrees_dir):
            return []
        return [
            name for name in os.listdir(self._worktrees_dir)
            if os.path.isdir(os.path.join(self._worktrees_dir, name))
        ]
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/merge/worktree_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/merge/worktree.py forge/merge/worktree_test.py
git commit -m "feat: git worktree lifecycle for task isolation"
```

---

## Phase 7: Agent Runtime

### Task 7.1: Agent Adapter Interface

**Files:**
- Create: `forge/agents/adapter.py`
- Create: `forge/agents/adapter_test.py`

**Step 1: Write the failing test**

```python
# forge/agents/adapter_test.py
import pytest
from forge.agents.adapter import AgentAdapter, AgentResult, ClaudeAdapter


def test_agent_result_fields():
    result = AgentResult(
        success=True,
        files_changed=["a.py", "b.py"],
        summary="Added user model",
        token_usage=5000,
    )
    assert result.success is True
    assert len(result.files_changed) == 2
    assert result.token_usage == 5000


def test_agent_result_failure():
    result = AgentResult(
        success=False,
        files_changed=[],
        summary="Could not parse requirements",
        error="ValueError: missing field",
    )
    assert result.success is False
    assert result.error is not None


def test_claude_adapter_is_agent_adapter():
    adapter = ClaudeAdapter()
    assert isinstance(adapter, AgentAdapter)


def test_adapter_has_run_method():
    adapter = ClaudeAdapter()
    assert callable(getattr(adapter, "run", None))
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/agents/adapter_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/agents/adapter.py
"""Agent adapter interface. Claude primary, others pluggable."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentResult:
    """Outcome of an agent task execution."""

    success: bool
    files_changed: list[str]
    summary: str
    token_usage: int = 0
    error: str | None = None


class AgentAdapter(ABC):
    """Interface for agent backends. Implement for each supported agent."""

    @abstractmethod
    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
    ) -> AgentResult:
        """Execute a task and return the result."""


class ClaudeAdapter(AgentAdapter):
    """Claude Code agent via claude_agent_sdk."""

    async def run(
        self,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
        timeout_seconds: int,
    ) -> AgentResult:
        # Placeholder — will integrate claude_agent_sdk in a later step
        # This allows the rest of the system to be built and tested
        raise NotImplementedError("Claude adapter not yet wired to SDK")
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/agents/adapter_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/agents/adapter.py forge/agents/adapter_test.py
git commit -m "feat: agent adapter interface with Claude adapter stub"
```

---

### Task 7.2: Agent Runtime (Lifecycle Manager)

**Files:**
- Create: `forge/agents/runtime.py`
- Create: `forge/agents/runtime_test.py`

**Step 1: Write the failing test**

```python
# forge/agents/runtime_test.py
import pytest
from unittest.mock import AsyncMock

from forge.agents.adapter import AgentResult
from forge.agents.runtime import AgentRuntime


@pytest.fixture
def mock_adapter():
    adapter = AsyncMock()
    adapter.run.return_value = AgentResult(
        success=True,
        files_changed=["a.py"],
        summary="Done",
        token_usage=1000,
    )
    return adapter


async def test_run_task_calls_adapter(mock_adapter):
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is True
    mock_adapter.run.assert_called_once_with(
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
        timeout_seconds=60,
    )


async def test_run_task_catches_timeout(mock_adapter):
    mock_adapter.run.side_effect = TimeoutError("timed out")
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is False
    assert "timed out" in result.error.lower() or "timeout" in result.error.lower()


async def test_run_task_catches_unexpected_error(mock_adapter):
    mock_adapter.run.side_effect = RuntimeError("kaboom")
    runtime = AgentRuntime(adapter=mock_adapter, timeout_seconds=60)
    result = await runtime.run_task(
        agent_id="agent-1",
        task_prompt="Build X",
        worktree_path="/tmp/wt",
        allowed_files=["a.py"],
    )
    assert result.success is False
    assert "kaboom" in result.error
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/agents/runtime_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/agents/runtime.py
"""Agent runtime. Manages agent execution lifecycle with error boundaries."""

from forge.agents.adapter import AgentAdapter, AgentResult


class AgentRuntime:
    """Wraps an adapter with timeout handling and error boundaries."""

    def __init__(self, adapter: AgentAdapter, timeout_seconds: int) -> None:
        self._adapter = adapter
        self._timeout = timeout_seconds

    async def run_task(
        self,
        agent_id: str,
        task_prompt: str,
        worktree_path: str,
        allowed_files: list[str],
    ) -> AgentResult:
        try:
            return await self._adapter.run(
                task_prompt=task_prompt,
                worktree_path=worktree_path,
                allowed_files=allowed_files,
                timeout_seconds=self._timeout,
            )
        except TimeoutError:
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent '{agent_id}' timed out after {self._timeout}s",
                error=f"Timeout after {self._timeout}s",
            )
        except Exception as e:
            return AgentResult(
                success=False,
                files_changed=[],
                summary=f"Agent '{agent_id}' failed: {e}",
                error=str(e),
            )
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/agents/runtime_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/agents/runtime.py forge/agents/runtime_test.py
git commit -m "feat: agent runtime with error boundaries and timeout handling"
```

---

## Phase 8: Planner

### Task 8.1: LLM Planner Interface

**Files:**
- Create: `forge/core/planner.py`
- Create: `forge/core/planner_test.py`

**Step 1: Write the failing test**

```python
# forge/core/planner_test.py
import json
import pytest
from unittest.mock import AsyncMock

from forge.core.errors import ValidationError
from forge.core.models import TaskGraph
from forge.core.planner import Planner, PlannerLLM


VALID_GRAPH_JSON = json.dumps({
    "tasks": [
        {
            "id": "task-1",
            "title": "Create model",
            "description": "Build user model",
            "files": ["src/models/user.py"],
            "depends_on": [],
            "complexity": "low",
        },
        {
            "id": "task-2",
            "title": "Build API",
            "description": "Build auth endpoints",
            "files": ["src/api/auth.py"],
            "depends_on": ["task-1"],
            "complexity": "medium",
        },
    ]
})

CYCLIC_GRAPH_JSON = json.dumps({
    "tasks": [
        {
            "id": "task-1",
            "title": "A",
            "description": "A",
            "files": ["a.py"],
            "depends_on": ["task-2"],
            "complexity": "low",
        },
        {
            "id": "task-2",
            "title": "B",
            "description": "B",
            "files": ["b.py"],
            "depends_on": ["task-1"],
            "complexity": "low",
        },
    ]
})


@pytest.fixture
def mock_llm():
    return AsyncMock(spec=PlannerLLM)


async def test_plan_returns_valid_task_graph(mock_llm):
    mock_llm.generate_plan.return_value = VALID_GRAPH_JSON
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build a REST API with auth")
    assert isinstance(graph, TaskGraph)
    assert len(graph.tasks) == 2


async def test_plan_retries_on_invalid_graph(mock_llm):
    mock_llm.generate_plan.side_effect = [
        '{"tasks": []}',  # Invalid: empty tasks
        VALID_GRAPH_JSON,  # Valid on retry
    ]
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build something")
    assert len(graph.tasks) == 2
    assert mock_llm.generate_plan.call_count == 2


async def test_plan_retries_on_cyclic_graph(mock_llm):
    mock_llm.generate_plan.side_effect = [
        CYCLIC_GRAPH_JSON,  # Invalid: cycle
        VALID_GRAPH_JSON,   # Valid on retry
    ]
    planner = Planner(llm=mock_llm, max_retries=3)
    graph = await planner.plan("Build something")
    assert len(graph.tasks) == 2


async def test_plan_fails_after_max_retries(mock_llm):
    mock_llm.generate_plan.return_value = '{"tasks": []}'
    planner = Planner(llm=mock_llm, max_retries=2)
    with pytest.raises(ValidationError, match="retries"):
        await planner.plan("Build something")
    assert mock_llm.generate_plan.call_count == 2
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/planner_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/planner.py
"""LLM planner. Decomposes user input into a validated TaskGraph."""

import json
from abc import ABC, abstractmethod

from pydantic import ValidationError as PydanticValidationError

from forge.core.errors import ValidationError
from forge.core.models import TaskGraph
from forge.core.validator import validate_task_graph


class PlannerLLM(ABC):
    """Interface for the LLM that generates plans."""

    @abstractmethod
    async def generate_plan(self, user_input: str, context: str, feedback: str | None = None) -> str:
        """Generate a TaskGraph JSON string from user input."""


class Planner:
    """Orchestrates plan generation with validation and retry loop."""

    def __init__(self, llm: PlannerLLM, max_retries: int = 3) -> None:
        self._llm = llm
        self._max_retries = max_retries

    async def plan(self, user_input: str, context: str = "") -> TaskGraph:
        feedback: str | None = None

        for attempt in range(self._max_retries):
            raw = await self._llm.generate_plan(user_input, context, feedback)
            graph, error = self._parse_and_validate(raw)
            if graph is not None:
                return graph
            feedback = f"Previous attempt failed: {error}. Please fix and try again."

        raise ValidationError(
            f"Planner failed to produce a valid TaskGraph after {self._max_retries} retries"
        )

    def _parse_and_validate(self, raw: str) -> tuple[TaskGraph | None, str | None]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            return None, f"Invalid JSON: {e}"

        try:
            graph = TaskGraph.model_validate(data)
        except PydanticValidationError as e:
            return None, f"Schema validation failed: {e}"

        try:
            validate_task_graph(graph)
        except Exception as e:
            return None, str(e)

        return graph, None
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/planner_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/planner.py forge/core/planner_test.py
git commit -m "feat: LLM planner with validation retry loop"
```

---

## Phase 9: Review Pipeline

### Task 9.1: Gate 1 — Auto-Check

**Files:**
- Create: `forge/review/auto_check.py`
- Create: `forge/review/auto_check_test.py`

**Step 1: Write the failing test**

```python
# forge/review/auto_check_test.py
from forge.review.auto_check import AutoCheck, CheckResult


def test_all_pass():
    result = AutoCheck.run_all(
        test_passed=True,
        lint_clean=True,
        build_ok=True,
        file_conflicts=[],
    )
    assert result.passed is True
    assert result.failures == []


def test_test_failure():
    result = AutoCheck.run_all(
        test_passed=False,
        lint_clean=True,
        build_ok=True,
        file_conflicts=[],
    )
    assert result.passed is False
    assert any("test" in f.lower() for f in result.failures)


def test_lint_failure():
    result = AutoCheck.run_all(
        test_passed=True,
        lint_clean=False,
        build_ok=True,
        file_conflicts=[],
    )
    assert result.passed is False
    assert any("lint" in f.lower() for f in result.failures)


def test_file_conflicts():
    result = AutoCheck.run_all(
        test_passed=True,
        lint_clean=True,
        build_ok=True,
        file_conflicts=["shared.py"],
    )
    assert result.passed is False
    assert any("conflict" in f.lower() for f in result.failures)


def test_multiple_failures_reported():
    result = AutoCheck.run_all(
        test_passed=False,
        lint_clean=False,
        build_ok=False,
        file_conflicts=["a.py"],
    )
    assert result.passed is False
    assert len(result.failures) == 4
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/review/auto_check_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/review/auto_check.py
"""Gate 1: Programmatic auto-checks. Fast, deterministic, no LLM."""

from dataclasses import dataclass, field


@dataclass
class CheckResult:
    """Outcome of Gate 1 checks."""

    passed: bool
    failures: list[str] = field(default_factory=list)


class AutoCheck:
    """Runs all programmatic checks and returns a unified result."""

    @staticmethod
    def run_all(
        test_passed: bool,
        lint_clean: bool,
        build_ok: bool,
        file_conflicts: list[str],
    ) -> CheckResult:
        failures: list[str] = []

        if not test_passed:
            failures.append("Tests failed")

        if not lint_clean:
            failures.append("Lint errors found")

        if not build_ok:
            failures.append("Build failed")

        if file_conflicts:
            failures.append(
                f"File conflicts with other agents: {', '.join(file_conflicts)}"
            )

        return CheckResult(passed=len(failures) == 0, failures=failures)
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/review/auto_check_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/review/auto_check.py forge/review/auto_check_test.py
git commit -m "feat: Gate 1 auto-check with test/lint/build/conflict checks"
```

---

### Task 9.2: Review Pipeline Orchestrator

**Files:**
- Create: `forge/review/pipeline.py`
- Create: `forge/review/pipeline_test.py`

**Step 1: Write the failing test**

```python
# forge/review/pipeline_test.py
import pytest
from unittest.mock import AsyncMock

from forge.review.auto_check import CheckResult
from forge.review.pipeline import ReviewPipeline, ReviewOutcome, GateResult


@pytest.fixture
def mock_gate1():
    return AsyncMock()


@pytest.fixture
def mock_gate2():
    return AsyncMock()


@pytest.fixture
def mock_gate3():
    return AsyncMock()


def _pass():
    return GateResult(passed=True, gate="test", details="OK")


def _fail(gate: str, details: str):
    return GateResult(passed=False, gate=gate, details=details)


async def test_all_gates_pass(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _pass()
    mock_gate2.return_value = _pass()
    mock_gate3.return_value = _pass()

    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is True
    assert outcome.gate_results[0].passed is True


async def test_gate1_fail_stops_pipeline(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _fail("auto-check", "Tests failed")

    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is False
    assert outcome.failed_gate == "auto-check"
    mock_gate2.assert_not_called()
    mock_gate3.assert_not_called()


async def test_gate2_fail_stops_pipeline(mock_gate1, mock_gate2, mock_gate3):
    mock_gate1.return_value = _pass()
    mock_gate2.return_value = _fail("llm-review", "Code quality issues")

    pipeline = ReviewPipeline(gate1=mock_gate1, gate2=mock_gate2, gate3=mock_gate3, max_retries=3)
    outcome = await pipeline.review("task-1")
    assert outcome.approved is False
    assert outcome.failed_gate == "llm-review"
    mock_gate3.assert_not_called()
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/review/pipeline_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/review/pipeline.py
"""3-gate review pipeline. Mandatory, no exceptions."""

from dataclasses import dataclass, field
from typing import Callable, Awaitable


@dataclass
class GateResult:
    """Outcome of a single review gate."""

    passed: bool
    gate: str
    details: str


@dataclass
class ReviewOutcome:
    """Final outcome of the full review pipeline."""

    approved: bool
    gate_results: list[GateResult] = field(default_factory=list)
    failed_gate: str | None = None


# Gate callable type: takes task_id, returns GateResult
GateFunc = Callable[[str], Awaitable[GateResult]]


class ReviewPipeline:
    """Runs Gate 1 → Gate 2 → Gate 3 sequentially. Stops on first failure."""

    def __init__(
        self,
        gate1: GateFunc,
        gate2: GateFunc,
        gate3: GateFunc,
        max_retries: int = 3,
    ) -> None:
        self._gates = [gate1, gate2, gate3]
        self._max_retries = max_retries

    async def review(self, task_id: str) -> ReviewOutcome:
        results: list[GateResult] = []

        for gate in self._gates:
            result = await gate(task_id)
            results.append(result)
            if not result.passed:
                return ReviewOutcome(
                    approved=False,
                    gate_results=results,
                    failed_gate=result.gate,
                )

        return ReviewOutcome(approved=True, gate_results=results)
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/review/pipeline_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/review/pipeline.py forge/review/pipeline_test.py
git commit -m "feat: 3-gate review pipeline with sequential fail-fast"
```

---

## Phase 10: Merge Pipeline

### Task 10.1: Merge Worker

**Files:**
- Create: `forge/merge/worker.py`
- Create: `forge/merge/worker_test.py`

**Step 1: Write the failing test**

```python
# forge/merge/worker_test.py
import subprocess
import pytest

from forge.merge.worker import MergeWorker, MergeResult


@pytest.fixture
def git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    (repo / "base.py").write_text("# base\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def _create_branch_with_commit(repo, branch: str, filename: str, content: str):
    subprocess.run(["git", "checkout", "-b", branch], cwd=repo, check=True, capture_output=True)
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", f"add {filename}"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "checkout", "master"], cwd=repo, check=True, capture_output=True)


def test_successful_merge(git_repo):
    _create_branch_with_commit(git_repo, "forge/task-1", "feature.py", "# feature\n")
    worker = MergeWorker(repo_path=str(git_repo))
    result = worker.merge("forge/task-1")
    assert result.success is True
    assert (git_repo / "feature.py").exists()


def test_merge_conflict_detected(git_repo):
    # Create conflicting changes on master and branch
    _create_branch_with_commit(git_repo, "forge/task-2", "conflict.py", "version A\n")
    (git_repo / "conflict.py").write_text("version B\n")
    subprocess.run(["git", "add", "."], cwd=git_repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "conflict on master"], cwd=git_repo, check=True, capture_output=True)

    worker = MergeWorker(repo_path=str(git_repo))
    result = worker.merge("forge/task-2")
    assert result.success is False
    assert len(result.conflicting_files) > 0


def test_merge_result_fields():
    r = MergeResult(success=True, conflicting_files=[])
    assert r.success is True
    assert r.conflicting_files == []
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/merge/worker_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/merge/worker.py
"""Merge worker. Rebases task branch onto main, detects conflicts."""

import subprocess
from dataclasses import dataclass, field


@dataclass
class MergeResult:
    """Outcome of a merge attempt."""

    success: bool
    conflicting_files: list[str] = field(default_factory=list)
    error: str | None = None


class MergeWorker:
    """Handles rebasing and merging task branches into main."""

    def __init__(self, repo_path: str, main_branch: str = "master") -> None:
        self._repo = repo_path
        self._main = main_branch

    def merge(self, branch: str) -> MergeResult:
        """Attempt to rebase branch onto main and fast-forward merge."""
        try:
            self._rebase(branch)
        except _RebaseConflict as e:
            self._abort_rebase()
            return MergeResult(success=False, conflicting_files=e.files)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        try:
            self._fast_forward(branch)
        except Exception as e:
            return MergeResult(success=False, error=str(e))

        return MergeResult(success=True)

    def _rebase(self, branch: str) -> None:
        result = subprocess.run(
            ["git", "rebase", self._main, branch],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            conflicts = self._find_conflicts()
            raise _RebaseConflict(files=conflicts)

    def _abort_rebase(self) -> None:
        subprocess.run(
            ["git", "rebase", "--abort"],
            cwd=self._repo,
            capture_output=True,
        )
        subprocess.run(
            ["git", "checkout", self._main],
            cwd=self._repo,
            capture_output=True,
        )

    def _fast_forward(self, branch: str) -> None:
        subprocess.run(
            ["git", "checkout", self._main],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "merge", "--ff-only", branch],
            cwd=self._repo,
            check=True,
            capture_output=True,
        )

    def _find_conflicts(self) -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            cwd=self._repo,
            capture_output=True,
            text=True,
        )
        return [f for f in result.stdout.strip().split("\n") if f]


class _RebaseConflict(Exception):
    def __init__(self, files: list[str]) -> None:
        self.files = files
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/merge/worker_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/merge/worker.py forge/merge/worker_test.py
git commit -m "feat: merge worker with rebase and conflict detection"
```

---

## Phase 11: Module Registry

### Task 11.1: Code Index

**Files:**
- Create: `forge/registry/index.py`
- Create: `forge/registry/index_test.py`

**Step 1: Write the failing test**

```python
# forge/registry/index_test.py
import json
from forge.registry.index import ModuleRegistry, FunctionEntry


def test_scan_python_file(tmp_path):
    source = tmp_path / "example.py"
    source.write_text(
        'def greet(name: str) -> str:\n'
        '    """Say hello."""\n'
        '    return f"Hello {name}"\n'
        '\n'
        'def _private():\n'
        '    pass\n'
    )
    registry = ModuleRegistry()
    registry.scan_file(str(source))
    entries = registry.all_entries()
    # Should find public function, skip private
    public = [e for e in entries if e.name == "greet"]
    assert len(public) == 1
    assert public[0].signature == "(name: str) -> str"
    assert public[0].docstring == "Say hello."


def test_scan_directory(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    (tmp_path / "b.py").write_text("def bar(): pass\n")
    (tmp_path / "not_python.txt").write_text("ignore me")
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    names = {e.name for e in registry.all_entries()}
    assert "foo" in names
    assert "bar" in names


def test_search_by_name(tmp_path):
    (tmp_path / "utils.py").write_text(
        "def calculate_total(items: list) -> float:\n"
        '    """Sum item prices."""\n'
        "    pass\n"
    )
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    results = registry.search("calculate")
    assert len(results) == 1
    assert results[0].name == "calculate_total"


def test_export_json(tmp_path):
    (tmp_path / "mod.py").write_text("def hello(): pass\n")
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    data = registry.to_json()
    parsed = json.loads(data)
    assert len(parsed) >= 1


def test_empty_registry():
    registry = ModuleRegistry()
    assert registry.all_entries() == []
    assert registry.search("anything") == []
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/registry/index_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/registry/index.py
"""Module registry. Indexes all public functions for reuse lookup."""

import ast
import json
import os
from dataclasses import dataclass, asdict


@dataclass
class FunctionEntry:
    """A public function in the codebase."""

    name: str
    file_path: str
    line_number: int
    signature: str
    docstring: str | None


class ModuleRegistry:
    """Scans Python files and maintains a searchable index of public functions."""

    def __init__(self) -> None:
        self._entries: list[FunctionEntry] = []

    def scan_file(self, file_path: str) -> None:
        try:
            with open(file_path) as f:
                source = f.read()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError):
            return

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                self._entries.append(_extract_function(node, file_path))

    def scan_directory(self, directory: str) -> None:
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.endswith(".py"):
                    self.scan_file(os.path.join(root, fname))

    def all_entries(self) -> list[FunctionEntry]:
        return list(self._entries)

    def search(self, query: str) -> list[FunctionEntry]:
        query_lower = query.lower()
        return [
            e for e in self._entries
            if query_lower in e.name.lower()
            or (e.docstring and query_lower in e.docstring.lower())
        ]

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self._entries], indent=2)


def _extract_function(node: ast.FunctionDef, file_path: str) -> FunctionEntry:
    sig = _build_signature(node)
    docstring = ast.get_docstring(node)
    return FunctionEntry(
        name=node.name,
        file_path=file_path,
        line_number=node.lineno,
        signature=sig,
        docstring=docstring,
    )


def _build_signature(node: ast.FunctionDef) -> str:
    args = ast.unparse(node.args) if node.args.args else ""
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({args}){ret}"
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/registry/index_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/registry/index.py forge/registry/index_test.py
git commit -m "feat: module registry with AST-based function indexing"
```

---

## Phase 12: CLI

### Task 12.1: CLI Entry Point

**Files:**
- Create: `forge/cli/main.py`
- Create: `forge/cli/main_test.py`

**Step 1: Write the failing test**

```python
# forge/cli/main_test.py
from click.testing import CliRunner
from forge.cli.main import cli


def test_cli_version():
    runner = CliRunner()
    result = runner.invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_cli_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Forge" in result.output


def test_cli_init_creates_forge_dir(tmp_path):
    runner = CliRunner()
    result = runner.invoke(cli, ["init", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert (tmp_path / ".forge").is_dir()
    assert (tmp_path / ".forge" / "build-log.md").exists()
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/cli/main_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/cli/main.py
"""Forge CLI. Entry point for all user interaction."""

import os

import click


@click.group()
@click.version_option(version="0.1.0", prog_name="Forge")
def cli() -> None:
    """Forge — Multi-agent orchestration engine."""


@cli.command()
@click.option("--project-dir", default=".", help="Project root directory")
def init(project_dir: str) -> None:
    """Initialize Forge in a project directory."""
    forge_dir = os.path.join(project_dir, ".forge")
    os.makedirs(forge_dir, exist_ok=True)

    _write_if_missing(os.path.join(forge_dir, "build-log.md"), "# Forge Build Log\n")
    _write_if_missing(os.path.join(forge_dir, "decisions.md"), "# Architectural Decisions\n")
    _write_if_missing(os.path.join(forge_dir, "module-registry.json"), "[]")

    click.echo(f"Forge initialized in {forge_dir}")


def _write_if_missing(path: str, content: str) -> None:
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(content)
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/cli/main_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/cli/main.py forge/cli/main_test.py
git commit -m "feat: CLI entry point with init command"
```

---

## Phase 13: Orchestration Engine

### Task 13.1: Engine Core (Ties Everything Together)

**Files:**
- Create: `forge/core/engine.py`
- Create: `forge/core/engine_test.py`

**Step 1: Write the failing test**

```python
# forge/core/engine_test.py
import pytest
from unittest.mock import AsyncMock, MagicMock

from forge.core.engine import ForgeEngine
from forge.core.models import (
    TaskGraph, TaskDefinition, Complexity,
    TaskRecord, TaskState, AgentRecord, AgentState,
)
from forge.core.monitor import ResourceSnapshot
from forge.agents.adapter import AgentResult
from forge.review.pipeline import ReviewOutcome, GateResult


@pytest.fixture
def mock_deps():
    """Create all mocked dependencies for the engine."""
    return {
        "db": AsyncMock(),
        "planner": AsyncMock(),
        "monitor": MagicMock(),
        "worktree_manager": MagicMock(),
        "agent_runtime": AsyncMock(),
        "review_pipeline": AsyncMock(),
        "merge_worker": MagicMock(),
    }


def _healthy_snapshot():
    return ResourceSnapshot(cpu_percent=30.0, memory_available_pct=70.0, disk_free_gb=50.0)


def _simple_graph():
    return TaskGraph(tasks=[
        TaskDefinition(
            id="task-1", title="Build feature",
            description="Build it", files=["a.py"],
            complexity=Complexity.LOW,
        ),
    ])


async def test_engine_plan_validates_and_stores(mock_deps):
    mock_deps["planner"].plan.return_value = _simple_graph()
    mock_deps["db"].list_tasks.return_value = []

    engine = ForgeEngine(**mock_deps, max_agents=4)
    graph = await engine.plan("Build a feature")

    assert len(graph.tasks) == 1
    mock_deps["planner"].plan.assert_called_once()
    mock_deps["db"].create_task.assert_called_once()


async def test_engine_dispatch_respects_resources(mock_deps):
    snapshot = ResourceSnapshot(cpu_percent=95.0, memory_available_pct=5.0, disk_free_gb=1.0)
    mock_deps["monitor"].take_snapshot.return_value = snapshot
    mock_deps["db"].list_tasks.return_value = [
        TaskRecord(id="t1", title="T", description="D", files=["a.py"],
                   depends_on=[], complexity=Complexity.LOW),
    ]
    mock_deps["db"].list_agents.return_value = [AgentRecord(id="w1")]

    engine = ForgeEngine(**mock_deps, max_agents=4)
    dispatched = await engine.dispatch_cycle()

    assert dispatched == 0  # Resources exhausted, nothing dispatched
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/engine_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/engine.py
"""Forge orchestration engine. The deterministic brain that ties everything together."""

from forge.agents.adapter import AgentResult
from forge.agents.runtime import AgentRuntime
from forge.core.models import TaskGraph, TaskRecord, TaskState, AgentState
from forge.core.monitor import ResourceMonitor
from forge.core.planner import Planner
from forge.core.scheduler import Scheduler
from forge.core.state import TaskStateMachine
from forge.merge.worker import MergeWorker
from forge.merge.worktree import WorktreeManager
from forge.review.pipeline import ReviewPipeline
from forge.storage.db import Database


class ForgeEngine:
    """Central orchestration. LLMs propose, this code disposes."""

    def __init__(
        self,
        db: Database,
        planner: Planner,
        monitor: ResourceMonitor,
        worktree_manager: WorktreeManager,
        agent_runtime: AgentRuntime,
        review_pipeline: ReviewPipeline,
        merge_worker: MergeWorker,
        max_agents: int = 4,
    ) -> None:
        self._db = db
        self._planner = planner
        self._monitor = monitor
        self._worktrees = worktree_manager
        self._runtime = agent_runtime
        self._review = review_pipeline
        self._merge = merge_worker
        self._max_agents = max_agents

    async def plan(self, user_input: str) -> TaskGraph:
        """Decompose user input into a validated TaskGraph and persist tasks."""
        graph = await self._planner.plan(user_input)
        for task_def in graph.tasks:
            await self._db.create_task(
                id=task_def.id,
                title=task_def.title,
                description=task_def.description,
                files=task_def.files,
                depends_on=task_def.depends_on,
                complexity=task_def.complexity.value,
            )
        return graph

    async def dispatch_cycle(self) -> int:
        """Run one dispatch cycle. Returns number of tasks dispatched."""
        snapshot = self._monitor.take_snapshot()
        if not self._monitor.can_dispatch(snapshot):
            return 0

        tasks = await self._db.list_tasks()
        agents = await self._db.list_agents()

        task_records = [_row_to_record(t) for t in tasks]
        agent_records = [_row_to_agent(a) for a in agents]

        dispatch_plan = Scheduler.dispatch_plan(
            task_records, agent_records, self._max_agents,
        )

        for task_id, agent_id in dispatch_plan:
            await self._db.assign_task(task_id, agent_id)
            await self._db.update_task_state(task_id, TaskState.IN_PROGRESS.value)

        return len(dispatch_plan)


def _row_to_record(row) -> TaskRecord:
    return TaskRecord(
        id=row.id, title=row.title, description=row.description,
        files=row.files, depends_on=row.depends_on, complexity=row.complexity,
        state=TaskState(row.state),
        assigned_agent=row.assigned_agent,
        retry_count=row.retry_count,
    )


def _row_to_agent(row) -> "AgentRecord":
    from forge.core.models import AgentRecord, AgentState
    return AgentRecord(
        id=row.id,
        state=AgentState(row.state),
        current_task=row.current_task,
    )
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/engine_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/engine.py forge/core/engine_test.py
git commit -m "feat: orchestration engine with plan and dispatch cycle"
```

---

## Phase 14: Cross-Session Continuity

### Task 14.1: Session Handoff Manager

**Files:**
- Create: `forge/core/continuity.py`
- Create: `forge/core/continuity_test.py`

**Step 1: Write the failing test**

```python
# forge/core/continuity_test.py
import json
import os
from forge.core.continuity import SessionHandoff


def test_write_handoff(tmp_path):
    handoff = SessionHandoff(forge_dir=str(tmp_path))
    handoff.write(
        completed=["Phase 1: Foundation"],
        in_progress=["Phase 2: Validator - cycle detection done, file conflicts WIP"],
        blockers=["Need to decide on AST parser library"],
        next_steps=["Finish file conflict detection", "Start state machine"],
        decisions=["Using Pydantic v2 for schema validation"],
    )
    path = tmp_path / "session-handoff.md"
    assert path.exists()
    content = path.read_text()
    assert "Phase 1: Foundation" in content
    assert "cycle detection" in content
    assert "AST parser" in content


def test_read_handoff(tmp_path):
    handoff = SessionHandoff(forge_dir=str(tmp_path))
    handoff.write(
        completed=["Task A"],
        in_progress=["Task B"],
        blockers=[],
        next_steps=["Task C"],
        decisions=["Used SQLite"],
    )
    data = handoff.read()
    assert data is not None
    assert "Task A" in data


def test_read_missing_handoff(tmp_path):
    handoff = SessionHandoff(forge_dir=str(tmp_path))
    data = handoff.read()
    assert data is None


def test_update_build_log(tmp_path):
    handoff = SessionHandoff(forge_dir=str(tmp_path))
    log = {
        "Phase 1: Foundation": True,
        "Phase 2: Validator": False,
        "Phase 3: State Machine": False,
    }
    handoff.update_build_log(log)
    path = tmp_path / "build-log.md"
    assert path.exists()
    content = path.read_text()
    assert "[x] Phase 1: Foundation" in content
    assert "[ ] Phase 2: Validator" in content
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/core/continuity_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/core/continuity.py
"""Cross-session continuity. Structured handoff between sessions."""

import os


class SessionHandoff:
    """Manages session handoff files for cross-session continuity."""

    def __init__(self, forge_dir: str) -> None:
        self._dir = forge_dir

    def _handoff_path(self) -> str:
        return os.path.join(self._dir, "session-handoff.md")

    def _build_log_path(self) -> str:
        return os.path.join(self._dir, "build-log.md")

    def write(
        self,
        completed: list[str],
        in_progress: list[str],
        blockers: list[str],
        next_steps: list[str],
        decisions: list[str],
    ) -> None:
        """Write a structured session handoff file."""
        os.makedirs(self._dir, exist_ok=True)
        lines = ["# Session Handoff\n"]

        lines.append("\n## Completed\n")
        for item in completed:
            lines.append(f"- {item}\n")

        lines.append("\n## In Progress\n")
        for item in in_progress:
            lines.append(f"- {item}\n")

        if blockers:
            lines.append("\n## Blockers\n")
            for item in blockers:
                lines.append(f"- {item}\n")

        lines.append("\n## Next Steps\n")
        for item in next_steps:
            lines.append(f"- {item}\n")

        lines.append("\n## Decisions This Session\n")
        for item in decisions:
            lines.append(f"- {item}\n")

        with open(self._handoff_path(), "w") as f:
            f.writelines(lines)

    def read(self) -> str | None:
        """Read the handoff file, or None if it doesn't exist."""
        path = self._handoff_path()
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return f.read()

    def update_build_log(self, phases: dict[str, bool]) -> None:
        """Update the build log with phase completion status."""
        os.makedirs(self._dir, exist_ok=True)
        lines = ["# Forge Build Log\n\n"]
        for phase, done in phases.items():
            marker = "x" if done else " "
            lines.append(f"- [{marker}] {phase}\n")

        with open(self._build_log_path(), "w") as f:
            f.writelines(lines)
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/core/continuity_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/core/continuity.py forge/core/continuity_test.py
git commit -m "feat: session handoff for cross-session continuity"
```

---

## Phase 15: Standards Enforcement

### Task 15.1: Programmatic Standards Checker

**Files:**
- Create: `forge/review/standards.py`
- Create: `forge/review/standards_test.py`

**Step 1: Write the failing test**

```python
# forge/review/standards_test.py
from forge.review.standards import StandardsChecker, Violation


def test_function_too_long(tmp_path):
    source = tmp_path / "long.py"
    lines = ["def too_long():\n"] + [f"    x = {i}\n" for i in range(35)]
    source.write_text("".join(lines))
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "max_function_length" for v in violations)


def test_function_ok_length(tmp_path):
    source = tmp_path / "short.py"
    source.write_text("def short():\n    return 1\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert not any(v.rule == "max_function_length" for v in violations)


def test_file_too_long(tmp_path):
    source = tmp_path / "big.py"
    source.write_text("\n".join([f"x{i} = {i}" for i in range(310)]))
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "max_file_length" for v in violations)


def test_bare_except_detected(tmp_path):
    source = tmp_path / "bare.py"
    source.write_text("try:\n    pass\nexcept:\n    pass\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "no_bare_except" for v in violations)


def test_bare_except_with_type_ok(tmp_path):
    source = tmp_path / "typed.py"
    source.write_text("try:\n    pass\nexcept ValueError:\n    pass\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert not any(v.rule == "no_bare_except" for v in violations)


def test_clean_file_no_violations(tmp_path):
    source = tmp_path / "clean.py"
    source.write_text(
        "def greet(name: str) -> str:\n"
        '    """Say hello."""\n'
        '    return f"Hello {name}"\n'
    )
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert violations == []
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/review/standards_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/review/standards.py
"""Programmatic coding standards enforcement. Part of Gate 1."""

import ast
from dataclasses import dataclass


@dataclass
class Violation:
    """A standards violation found in code."""

    rule: str
    file_path: str
    line: int
    message: str


class StandardsChecker:
    """Checks Python files against coding standards."""

    def __init__(self, max_function_lines: int = 30, max_file_lines: int = 300) -> None:
        self._max_func_lines = max_function_lines
        self._max_file_lines = max_file_lines

    def check_file(self, file_path: str) -> list[Violation]:
        try:
            with open(file_path) as f:
                source = f.read()
                lines = source.splitlines()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError):
            return []

        violations: list[Violation] = []
        violations.extend(self._check_file_length(file_path, lines))
        violations.extend(self._check_function_lengths(file_path, tree))
        violations.extend(self._check_bare_except(file_path, tree))
        return violations

    def _check_file_length(self, path: str, lines: list[str]) -> list[Violation]:
        if len(lines) > self._max_file_lines:
            return [Violation(
                rule="max_file_length",
                file_path=path,
                line=len(lines),
                message=f"File has {len(lines)} lines (max {self._max_file_lines})",
            )]
        return []

    def _check_function_lengths(self, path: str, tree: ast.AST) -> list[Violation]:
        violations: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = _function_length(node)
                if length > self._max_func_lines:
                    violations.append(Violation(
                        rule="max_function_length",
                        file_path=path,
                        line=node.lineno,
                        message=f"Function '{node.name}' is {length} lines (max {self._max_func_lines})",
                    ))
        return violations

    def _check_bare_except(self, path: str, tree: ast.AST) -> list[Violation]:
        violations: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                violations.append(Violation(
                    rule="no_bare_except",
                    file_path=path,
                    line=node.lineno,
                    message="Bare except clause (catch a specific exception)",
                ))
        return violations


def _function_length(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if not node.body:
        return 0
    first_line = node.body[0].lineno
    last_line = node.end_lineno or node.body[-1].lineno
    return last_line - first_line + 1
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/review/standards_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/review/standards.py forge/review/standards_test.py
git commit -m "feat: programmatic standards checker for Gate 1"
```

---

## Phase 16: TUI Dashboard (Minimal)

### Task 16.1: Status Display

**Files:**
- Create: `forge/tui/dashboard.py`
- Create: `forge/tui/dashboard_test.py`

**Step 1: Write the failing test**

```python
# forge/tui/dashboard_test.py
from forge.tui.dashboard import format_status_table
from forge.core.models import TaskRecord, TaskState, Complexity


def test_format_empty():
    output = format_status_table([])
    assert "No tasks" in output


def test_format_with_tasks():
    tasks = [
        TaskRecord(
            id="task-1", title="Build model", description="D",
            files=["a.py"], depends_on=[], complexity=Complexity.LOW,
            state=TaskState.IN_PROGRESS, assigned_agent="agent-1",
        ),
        TaskRecord(
            id="task-2", title="Build API", description="D",
            files=["b.py"], depends_on=["task-1"], complexity=Complexity.MEDIUM,
            state=TaskState.TODO,
        ),
    ]
    output = format_status_table(tasks)
    assert "task-1" in output
    assert "Build model" in output
    assert "in_progress" in output
    assert "task-2" in output
```

**Step 2: Run test to verify it fails**

```bash
pytest forge/tui/dashboard_test.py -v
```
Expected: FAIL

**Step 3: Write minimal implementation**

```python
# forge/tui/dashboard.py
"""TUI dashboard. Minimal status display using Rich."""

from rich.console import Console
from rich.table import Table

from forge.core.models import TaskRecord


def format_status_table(tasks: list[TaskRecord]) -> str:
    """Format tasks as a Rich table string."""
    if not tasks:
        return "No tasks"

    console = Console(record=True, width=120)
    table = Table(title="Forge Tasks")
    table.add_column("ID", style="cyan")
    table.add_column("Title", style="white")
    table.add_column("State", style="bold")
    table.add_column("Agent", style="green")
    table.add_column("Files", style="dim")

    for task in tasks:
        state_style = _state_color(task.state.value)
        table.add_row(
            task.id,
            task.title,
            f"[{state_style}]{task.state.value}[/{state_style}]",
            task.assigned_agent or "-",
            ", ".join(task.files[:2]) + ("..." if len(task.files) > 2 else ""),
        )

    console.print(table)
    return console.export_text()


def _state_color(state: str) -> str:
    colors = {
        "todo": "white",
        "in_progress": "yellow",
        "in_review": "blue",
        "merging": "magenta",
        "done": "green",
        "error": "red",
        "cancelled": "dim",
    }
    return colors.get(state, "white")
```

**Step 4: Run test to verify it passes**

```bash
pytest forge/tui/dashboard_test.py -v
```
Expected: ALL PASS

**Step 5: Commit**

```bash
git add forge/tui/dashboard.py forge/tui/dashboard_test.py
git commit -m "feat: minimal TUI status table with Rich"
```

---

## Final: Run All Tests

After all phases are complete:

```bash
pytest forge/ -v --tb=short
```

Expected: ALL PASS across all modules.

Then update `.forge/build-log.md` marking all phases complete, and commit.

```bash
git add .forge/build-log.md
git commit -m "docs: mark all build phases complete"
```

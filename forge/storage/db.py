"""Unified database layer. SQLAlchemy 2.0 async. SQLite default, Postgres optional.

Single Database class for ALL Forge data: auth (users, audit logs),
pipelines, tasks, and agents.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Integer,
    String,
    Text,
    case,
    func,
    or_,
    select,
    text,
)
from sqlalchemy import (
    delete as sa_delete,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# ── Auth models ───────────────────────────────────────────────────────


class UserRow(Base):
    """Registered user account."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    settings_json: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class AuditLogRow(Base):
    """Immutable audit trail entry."""

    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)


# ── Pipeline models ───────────────────────────────────────────────────


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
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None, index=True)
    review_feedback: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    retry_reason: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    agent_cost_usd: Mapped[float] = mapped_column(default=0.0)
    review_cost_usd: Mapped[float] = mapped_column(default=0.0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    approval_context: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    prior_diff: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    implementation_summary: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    session_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    questions_asked: Mapped[int] = mapped_column(default=0)
    questions_limit: Mapped[int] = mapped_column(default=3)
    review_diff: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    repo_id: Mapped[str] = mapped_column(String, default="default")
    # Timing and metrics columns
    started_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    completed_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    agent_duration_s: Mapped[float] = mapped_column(default=0.0)
    review_duration_s: Mapped[float] = mapped_column(default=0.0)
    lint_duration_s: Mapped[float] = mapped_column(default=0.0)
    merge_duration_s: Mapped[float] = mapped_column(default=0.0)
    num_turns: Mapped[int] = mapped_column(default=0)
    max_turns: Mapped[int] = mapped_column(default=0)
    error_message: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class AgentRow(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="idle")
    current_task: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


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
    planner_cost_usd: Mapped[float] = mapped_column(default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)
    budget_limit_usd: Mapped[float] = mapped_column(default=0.0)
    estimated_cost_usd: Mapped[float] = mapped_column(default=0.0)
    paused: Mapped[bool] = mapped_column(default=False)
    conventions_json: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    require_approval: Mapped[bool] = mapped_column(default=False)
    github_issue_url: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    github_issue_number: Mapped[int | None] = mapped_column(default=None)
    template_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    template_config_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # Contract Builder output (JSON blob)
    contracts_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    paused_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    paused_duration: Mapped[float] = mapped_column(default=0.0)
    # Pipeline metrics columns
    duration_s: Mapped[float] = mapped_column(default=0.0)
    total_input_tokens: Mapped[int] = mapped_column(default=0)
    total_output_tokens: Mapped[int] = mapped_column(default=0)
    tasks_succeeded: Mapped[int] = mapped_column(default=0)
    tasks_failed: Mapped[int] = mapped_column(default=0)
    total_retries: Mapped[int] = mapped_column(default=0)
    # Cross-project tracking
    project_path: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    project_name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Executor tracking for orphan detection
    executor_pid: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    executor_token: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Integration health check baseline
    baseline_exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    integration_status: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    # Multi-repo workspace support
    repos_json: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # CI Auto-Fix
    ci_fix_enabled: Mapped[bool] = mapped_column(default=True)
    ci_fix_status: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    ci_fix_attempt: Mapped[int] = mapped_column(default=0)
    ci_fix_max_retries: Mapped[int] = mapped_column(default=3)
    ci_fix_cost_usd: Mapped[float] = mapped_column(default=0.0)
    ci_fix_log: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)

    def get_repos(self) -> list[dict]:
        """Return repo configurations. Single-repo returns synthetic default entry."""
        if self.repos_json:
            return json.loads(self.repos_json)
        if not self.base_branch:
            raise ValueError(
                f"Pipeline {self.id} has no base_branch set. "
                "This should have been set during execute()."
            )
        return [
            {
                "id": "default",
                "path": self.project_dir,
                "base_branch": self.base_branch,
                "branch_name": self.branch_name or "",
            }
        ]


class UserTemplateRow(Base):
    """User-owned custom pipeline template stored in the DB."""

    __tablename__ = "user_templates"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        nullable=False,
    )


class PipelineEventRow(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(UTC).isoformat())


class TaskQuestionRow(Base):
    """Agent question awaiting human answer."""

    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    answered_by: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    context: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.now(UTC).isoformat(),
    )
    answered_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    stage: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class InterjectionRow(Base):
    """Human message sent to a running agent."""

    __tablename__ = "task_interjections"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    delivered: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.now(UTC).isoformat(),
    )
    delivered_at: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


class LessonRow(Base):
    """Learned lesson from agent failures — injected into future prompts."""

    __tablename__ = "lessons"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    scope: Mapped[str] = mapped_column(String, nullable=False, index=True)  # 'global' or 'project'
    project_dir: Mapped[str | None] = mapped_column(String, nullable=True, default=None, index=True)
    category: Mapped[str] = mapped_column(
        String, nullable=False
    )  # 'command_failure', 'review_failure', 'code_pattern'
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(String, nullable=False, index=True)
    resolution: Mapped[str] = mapped_column(Text, nullable=False)
    hit_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.now(UTC).isoformat(),
    )
    last_hit_at: Mapped[str] = mapped_column(
        String,
        default=lambda: datetime.now(UTC).isoformat(),
    )
    confidence: Mapped[float] = mapped_column(default=0.5)


# ── All model classes (used by _add_missing_columns) ──────────────────
_ALL_MODELS = (
    UserRow,
    AuditLogRow,
    TaskRow,
    AgentRow,
    PipelineRow,
    UserTemplateRow,
    PipelineEventRow,
    TaskQuestionRow,
    InterjectionRow,
    LessonRow,
)


class Database:
    """Unified async database interface. One DB for everything."""

    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

    async def __aenter__(self) -> Database:
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.close()

    async def initialize(self) -> None:
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(self._add_missing_columns)

    @staticmethod
    def _validate_identifier(name: str) -> str:
        """Validate that a SQL identifier contains only safe characters.

        Raises ValueError if the name contains anything other than
        alphanumeric characters and underscores.
        """
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
            raise ValueError(f"Invalid SQL identifier: {name!r}")
        return name

    @staticmethod
    def _add_missing_columns(connection) -> None:
        """Add columns that exist in the ORM model but not in the DB table."""
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import text

        inspector = sa_inspect(connection)
        for table_cls in _ALL_MODELS:
            table_name = table_cls.__tablename__
            # Validate table name from ORM metadata against injection
            Database._validate_identifier(table_name)
            if not inspector.has_table(table_name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table_name)}
            for attr in table_cls.__table__.columns:
                if attr.name not in existing:
                    # Validate column name from ORM metadata against injection
                    Database._validate_identifier(attr.name)
                    col_type = attr.type.compile(dialect=connection.dialect)
                    nullable = "NULL" if attr.nullable else "NOT NULL"
                    default = ""
                    if attr.default is not None and attr.default.arg is not None:
                        default = f" DEFAULT {attr.default.arg!r}"
                    connection.execute(
                        text(
                            f"ALTER TABLE {table_name} ADD COLUMN {attr.name} {col_type} {nullable}{default}"
                        )
                    )

    async def close(self) -> None:
        await self._engine.dispose()

    # ── Auth: Users ───────────────────────────────────────────────────

    async def create_user(
        self,
        *,
        email: str,
        password: str,
        display_name: str,
    ) -> UserRow:
        """Register a new user. Raises ValueError if email already taken."""
        try:
            import bcrypt
        except ImportError:
            raise ImportError(
                "bcrypt is required for user management. "
                "Install with: pip install forge-orchestrator[web]"
            )
        async with self._session_factory() as session:
            result = await session.execute(select(UserRow).where(UserRow.email == email))
            if result.scalar_one_or_none() is not None:
                raise ValueError(f"Email {email} is already registered")

            hashed = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            user = UserRow(
                email=email,
                password_hash=hashed,
                display_name=display_name,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    async def get_user_by_email(self, email: str) -> UserRow | None:
        async with self._session_factory() as session:
            result = await session.execute(select(UserRow).where(UserRow.email == email))
            return result.scalar_one_or_none()

    @staticmethod
    def verify_password(password: str, hashed: str) -> bool:
        try:
            import bcrypt
        except ImportError:
            raise ImportError(
                "bcrypt is required for user management. "
                "Install with: pip install forge-orchestrator[web]"
            )
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

    # ── User settings ────────────────────────────────────────────────

    async def get_user_settings(self, user_id: str) -> dict | None:
        """Load persisted settings JSON for a user, or None if not set."""
        async with self._session_factory() as session:
            user = await session.get(UserRow, user_id)
            if user and user.settings_json:
                return json.loads(user.settings_json)
            return None

    async def save_user_settings(self, user_id: str, settings: dict) -> None:
        """Persist settings JSON for a user."""
        async with self._session_factory() as session:
            user = await session.get(UserRow, user_id)
            if user:
                user.settings_json = json.dumps(settings)
                await session.commit()

    # ── Auth: Audit logs ──────────────────────────────────────────────

    async def log_audit(
        self,
        user_id: str,
        action: str,
        metadata: dict | None = None,
        ip: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = AuditLogRow(
                user_id=user_id,
                action=action,
                metadata_json=json.dumps(metadata) if metadata is not None else None,
                ip_address=ip,
            )
            session.add(row)
            await session.commit()

    async def list_audit_logs(self, user_id: str, limit: int = 100) -> list[AuditLogRow]:
        async with self._session_factory() as session:
            stmt = (
                select(AuditLogRow)
                .where(AuditLogRow.user_id == user_id)
                .order_by(AuditLogRow.timestamp.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Tasks ─────────────────────────────────────────────────────────

    async def create_task(
        self,
        id: str,
        title: str,
        description: str,
        files: list[str],
        depends_on: list[str],
        complexity: str,
        pipeline_id: str | None = None,
        repo_id: str = "default",
    ) -> None:
        async with self._session_factory() as session:
            row = TaskRow(
                id=id,
                title=title,
                description=description,
                files=files,
                depends_on=depends_on,
                complexity=complexity,
                pipeline_id=pipeline_id,
                repo_id=repo_id,
            )
            session.add(row)
            await session.commit()

    async def get_task(self, task_id: str) -> TaskRow | None:
        async with self._session_factory() as session:
            return await session.get(TaskRow, task_id)

    async def get_tasks_by_ids(self, task_ids: list[str]) -> list[TaskRow]:
        """Batch-fetch tasks by a list of IDs.

        Returns TaskRow objects for all found IDs. Missing IDs are silently
        omitted. If task_ids is empty, returns [] without issuing a query.
        Duplicate IDs produce only one TaskRow per unique ID.
        Order is NOT guaranteed to match input order.
        """
        if not task_ids:
            return []
        unique_ids = list(set(task_ids))
        async with self._session_factory() as session:
            result = await session.execute(select(TaskRow).where(TaskRow.id.in_(unique_ids)))
            return list(result.scalars().all())

    async def update_task_state(self, task_id: str, state: str) -> None:
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                # Guard: validate state transition via TaskStateMachine
                try:
                    from forge.core.models import TaskState
                    from forge.core.state import TaskStateMachine

                    current = TaskState(task.state)
                    target = TaskState(state)
                    if not TaskStateMachine.can_transition(current, target):
                        logger.warning(
                            "Invalid state transition for task %s: %s -> %s",
                            task_id,
                            task.state,
                            state,
                        )
                except (ValueError, KeyError):
                    # Unknown state value — log and proceed
                    logger.warning(
                        "Could not validate state transition for task %s: %s -> %s",
                        task_id,
                        task.state,
                        state,
                    )
                task.state = state
                await session.commit()

    async def list_tasks(self, state: str | None = None) -> list[TaskRow]:
        async with self._session_factory() as session:
            stmt = select(TaskRow)
            if state:
                stmt = stmt.where(TaskRow.state == state)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def list_tasks_by_pipeline(self, pipeline_id: str) -> list[TaskRow]:
        async with self._session_factory() as session:
            stmt = select(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_tasks_by_state(self, pipeline_id: str, state: str) -> list[TaskRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskRow)
                .where(TaskRow.pipeline_id == pipeline_id)
                .where(TaskRow.state == state)
            )
            return list(result.scalars().all())

    async def add_task_cost(self, task_id: str, cost: float) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("UPDATE tasks SET cost_usd = COALESCE(cost_usd, 0) + :cost WHERE id = :tid"),
                {"cost": cost, "tid": task_id},
            )
            await session.commit()

    async def add_task_agent_cost(
        self,
        task_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        """Record agent execution cost and token usage for a task."""
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE tasks SET "
                    "agent_cost_usd = COALESCE(agent_cost_usd, 0) + :cost, "
                    "cost_usd = COALESCE(cost_usd, 0) + :cost, "
                    "input_tokens = COALESCE(input_tokens, 0) + :inp, "
                    "output_tokens = COALESCE(output_tokens, 0) + :outp "
                    "WHERE id = :tid"
                ),
                {"cost": cost, "inp": input_tokens, "outp": output_tokens, "tid": task_id},
            )
            await session.commit()

    async def add_task_review_cost(self, task_id: str, cost: float) -> None:
        """Record review cost for a task."""
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE tasks SET "
                    "review_cost_usd = COALESCE(review_cost_usd, 0) + :cost, "
                    "cost_usd = COALESCE(cost_usd, 0) + :cost "
                    "WHERE id = :tid"
                ),
                {"cost": cost, "tid": task_id},
            )
            await session.commit()

    async def add_pipeline_cost(self, pipeline_id: str, cost: float) -> None:
        """Add cost to the pipeline total."""
        async with self._session_factory() as session:
            await session.execute(
                text(
                    "UPDATE pipelines SET total_cost_usd = COALESCE(total_cost_usd, 0) + :delta WHERE id = :pid"
                ),
                {"delta": cost, "pid": pipeline_id},
            )
            await session.commit()

    async def set_pipeline_planner_cost(self, pipeline_id: str, cost: float) -> None:
        """Set the planner cost for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.planner_cost_usd = cost
                await session.commit()

    async def get_pipeline_cost(self, pipeline_id: str) -> float:
        """Return the current total cost for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                return row.total_cost_usd or 0.0
            return 0.0

    async def get_pipeline_budget(self, pipeline_id: str) -> float:
        """Return the budget limit for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                return row.budget_limit_usd or 0.0
            return 0.0

    # ── Agents ────────────────────────────────────────────────────────

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

    async def retry_task(self, task_id: str, review_feedback: str | None = None) -> None:
        """Reset a task for retry: increment retry_count, set state to todo, unassign agent."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.retry_count += 1
                task.state = "todo"
                task.assigned_agent = None
                task.retry_reason = None  # Clear merge flag — this is a full retry
                if review_feedback is not None:
                    task.review_feedback = review_feedback
                await session.commit()

    async def retry_task_for_merge(self, task_id: str) -> None:
        """Reset a task for merge-only retry (skip agent + review on next run).

        Sets retry_reason='merge_failed' so _execute_task() knows to go
        directly to the merge step without re-running the agent or review.
        """
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.retry_count += 1
                task.state = "todo"
                task.assigned_agent = None
                task.retry_reason = "merge_failed"
                await session.commit()

    async def release_agent(self, agent_id: str) -> None:
        """Set agent back to idle and clear its current task."""
        async with self._session_factory() as session:
            agent = await session.get(AgentRow, agent_id)
            if agent:
                agent.state = "idle"
                agent.current_task = None
                await session.commit()

    async def force_release_agent(self, agent_id: str) -> None:
        """Force-reset agent to idle via raw SQL. Last-resort fallback."""
        async with self._session_factory() as session:
            await session.execute(
                text("UPDATE agents SET state = 'idle', current_task = NULL WHERE id = :aid"),
                {"aid": agent_id},
            )
            await session.commit()

    async def list_agents(self, prefix: str | None = None) -> list[AgentRow]:
        async with self._session_factory() as session:
            stmt = select(AgentRow)
            if prefix:
                stmt = stmt.where(AgentRow.id.startswith(prefix))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Pipelines ─────────────────────────────────────────────────────

    async def create_pipeline(
        self,
        id: str,
        description: str,
        project_dir: str,
        model_strategy: str = "auto",
        user_id: str | None = None,
        base_branch: str | None = None,
        branch_name: str | None = None,
        build_cmd: str | None = None,
        test_cmd: str | None = None,
        budget_limit_usd: float = 0.0,
        github_issue_url: str | None = None,
        github_issue_number: int | None = None,
        project_path: str | None = None,
        project_name: str | None = None,
        repos_json: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = PipelineRow(
                id=id,
                description=description,
                project_dir=project_dir,
                model_strategy=model_strategy,
                user_id=user_id,
                base_branch=base_branch,
                branch_name=branch_name,
                build_cmd=build_cmd,
                test_cmd=test_cmd,
                budget_limit_usd=budget_limit_usd,
                github_issue_url=github_issue_url,
                github_issue_number=github_issue_number,
                project_path=project_path,
                project_name=project_name,
                repos_json=repos_json,
                created_at=datetime.now(UTC).isoformat(),
            )
            session.add(row)
            await session.commit()

    async def get_pipeline(self, pipeline_id: str) -> PipelineRow | None:
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            return result.scalar_one_or_none()

    async def set_executor_info(self, pipeline_id: str, pid: int, token: str) -> None:
        """Record the PID and session token of the executor process for orphan detection."""
        async with self._session_factory() as session:
            pipeline = await session.get(PipelineRow, pipeline_id)
            if pipeline:
                pipeline.executor_pid = pid
                pipeline.executor_token = token
                await session.commit()

    async def clear_executor_info(self, pipeline_id: str) -> None:
        """Clear executor tracking fields when a pipeline finishes cleanly."""
        async with self._session_factory() as session:
            pipeline = await session.get(PipelineRow, pipeline_id)
            if pipeline:
                pipeline.executor_pid = None
                pipeline.executor_token = None
                await session.commit()

    async def update_pipeline_status(self, pipeline_id: str, status: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.status = status
                if status in ("complete", "error"):
                    row.completed_at = datetime.now(UTC).isoformat()
                await session.commit()

    async def set_pipeline_plan(self, pipeline_id: str, task_graph_json: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.task_graph_json = task_graph_json
                row.status = "planned"
                await session.commit()

    async def set_pipeline_pr_url(self, pipeline_id: str, pr_url: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.pr_url = pr_url
                await session.commit()

    async def set_pipeline_base_branch(self, pipeline_id: str, base_branch: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.base_branch = base_branch
                await session.commit()

    async def set_pipeline_branch_name(self, pipeline_id: str, branch_name: str) -> None:
        """Store the computed pipeline branch name (custom or auto-generated)."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.branch_name = branch_name
                await session.commit()

    async def set_baseline_exit_code(self, pipeline_id: str, exit_code: int | None) -> None:
        """Store the integration baseline exit code for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.baseline_exit_code = exit_code
                await session.commit()

    async def set_integration_status(self, pipeline_id: str, status_json: str) -> None:
        """Store integration health check status JSON for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.integration_status = status_json
                await session.commit()

    async def set_pipeline_contracts(self, pipeline_id: str, contracts_json: str) -> None:
        """Store the ContractSet JSON for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.contracts_json = contracts_json
                await session.commit()

    async def get_pipeline_contracts(self, pipeline_id: str) -> str | None:
        """Get the ContractSet JSON for a pipeline."""
        pipeline = await self.get_pipeline(pipeline_id)
        return getattr(pipeline, "contracts_json", None) if pipeline else None

    async def list_pipelines(
        self,
        user_id: str | None = None,
        project_path: str | None = None,
    ) -> list[PipelineRow]:
        async with self._session_factory() as session:
            query = select(PipelineRow)
            if user_id:
                query = query.where(PipelineRow.user_id == user_id)
            if project_path is not None:
                query = query.where(PipelineRow.project_path == project_path)
            result = await session.execute(query.order_by(PipelineRow.created_at.desc()))
            return list(result.scalars().all())

    async def list_projects(self) -> list[dict]:
        """Return unique projects with pipeline counts and latest timestamps."""
        async with self._session_factory() as session:
            stmt = (
                select(
                    PipelineRow.project_path,
                    PipelineRow.project_name,
                    func.count(PipelineRow.id).label("pipeline_count"),
                    func.max(PipelineRow.created_at).label("latest_pipeline_at"),
                )
                .where(PipelineRow.project_path.isnot(None))
                .group_by(PipelineRow.project_path, PipelineRow.project_name)
                .order_by(func.max(PipelineRow.created_at).desc())
            )
            result = await session.execute(stmt)
            return [
                {
                    "project_path": row.project_path,
                    "project_name": row.project_name,
                    "pipeline_count": row.pipeline_count,
                    "latest_pipeline_at": row.latest_pipeline_at,
                }
                for row in result.all()
            ]

    async def restart_pipeline(self, pipeline_id: str) -> dict:
        """Reset a pipeline for a fresh restart.

        - Deletes all task rows (so re-planning can create fresh ones)
        - Resets pipeline status to 'pending'
        - Clears pipeline's task_graph_json so fresh planning occurs
        - Deletes all pipeline events (clean slate)

        Returns dict with counts: {'tasks_reset': int, 'events_deleted': int}
        """
        async with self._session_factory() as session:
            # Reset pipeline
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            pipeline = result.scalar_one_or_none()
            if pipeline is None:
                return {"tasks_reset": 0, "events_deleted": 0}

            pipeline.status = "pending"
            pipeline.task_graph_json = None
            pipeline.completed_at = None
            pipeline.pr_url = None
            pipeline.cancelled_at = None

            # Delete all old tasks so re-planning can create fresh rows
            # with the same prefixed IDs (pipeline_id[:8]-task-N).
            del_tasks = await session.execute(
                sa_delete(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
            )
            tasks_reset = del_tasks.rowcount

            # Delete all pipeline events
            del_result = await session.execute(
                sa_delete(PipelineEventRow).where(PipelineEventRow.pipeline_id == pipeline_id)
            )
            events_deleted = del_result.rowcount

            await session.commit()
            return {"tasks_reset": tasks_reset, "events_deleted": events_deleted}

    async def cancel_pipeline_hard(self, pipeline_id: str) -> dict:
        """Hard-cancel a pipeline: mark everything cancelled with a timestamp.

        - Sets pipeline status to 'cancelled'
        - Sets cancelled_at timestamp
        - Marks all non-terminal tasks as CANCELLED

        Returns dict with counts: {'tasks_cancelled': int}
        """
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            pipeline = result.scalar_one_or_none()
            if pipeline is None:
                return {"tasks_cancelled": 0}

            pipeline.status = "cancelled"
            pipeline.cancelled_at = datetime.now(UTC).isoformat()

            # Cancel all non-terminal tasks
            task_result = await session.execute(
                select(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
            )
            tasks = list(task_result.scalars().all())
            _terminal_states = {"done", "error", "cancelled"}
            tasks_cancelled = 0
            for task in tasks:
                if task.state not in _terminal_states:
                    task.state = "cancelled"
                    tasks_cancelled += 1

            await session.commit()
            return {"tasks_cancelled": tasks_cancelled}

    # ── Approval context ──────────────────────────────────────────────

    async def set_task_approval_context(self, task_id: str, context_json: str) -> None:
        """Store approval context JSON for a task awaiting human approval."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.approval_context = context_json
                await session.commit()

    async def clear_task_approval_context(self, task_id: str) -> None:
        """Clear approval context after approval/rejection."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.approval_context = None
                await session.commit()

    async def approve_task_atomically(self, task_id: str, pipeline_id: str):
        """Atomically check state=awaiting_approval and transition to merging.

        Returns the task row if successful, None if task not found.
        Raises ValueError if task is not in awaiting_approval state.
        """
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task is None or task.pipeline_id != pipeline_id:
                return None
            if task.state != "awaiting_approval":
                raise ValueError(f"Task {task_id} in state '{task.state}', not 'awaiting_approval'")
            task.state = "merging"
            await session.commit()
            await session.refresh(task)
            return task

    async def set_task_prior_diff(self, task_id: str, diff: str) -> None:
        """Store the rejected diff so the re-reviewer can compare on retry."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.prior_diff = diff
                await session.commit()

    async def set_task_review_diff(self, task_id: str, diff: str) -> None:
        """Store the current diff when task enters review.

        This is the diff the TUI displays in the review screen and
        pipeline diff viewer.  Computed by the daemon in the worktree
        so it reflects the task's actual changes, not the merged state.
        """
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.review_diff = diff
                await session.commit()

    async def get_task_review_diff(self, task_id: str) -> str | None:
        """Retrieve the stored review diff for a task."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            return task.review_diff if task else None

    async def update_pipeline_conventions(self, pipeline_id: str, conventions_json: str) -> None:
        """Store conventions JSON for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.conventions_json = conventions_json
                await session.commit()

    async def update_pipeline_repos_json(self, pipeline_id: str, repos_json: str) -> None:
        """Update the repos_json column for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.repos_json = repos_json
                await session.commit()

    async def update_task_implementation_summary(self, task_id: str, summary: str) -> None:
        """Store implementation summary for a task."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.implementation_summary = summary
                await session.commit()

    async def set_pipeline_paused(self, pipeline_id: str, paused: bool) -> None:
        """Set or clear the paused flag on a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.paused = paused
                await session.commit()

    async def set_pipeline_paused_at(self, pipeline_id: str, paused_at: str | None) -> None:
        """Set or clear the paused_at timestamp on a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.paused_at = paused_at
                await session.commit()

    async def add_pipeline_paused_duration(self, pipeline_id: str, elapsed_seconds: float) -> None:
        """Add elapsed_seconds to the pipeline's paused_duration accumulator."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.paused_duration = (row.paused_duration or 0.0) + elapsed_seconds
                await session.commit()

    # ── User templates ─────────────────────────────────────────────

    async def create_user_template(
        self,
        user_id: str,
        name: str,
        config_json: str,
    ) -> UserTemplateRow:
        """Create a new user-owned pipeline template."""
        async with self._session_factory() as session:
            row = UserTemplateRow(
                user_id=user_id,
                name=name,
                config_json=config_json,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_user_templates(self, user_id: str) -> list[UserTemplateRow]:
        """List all templates owned by a user."""
        async with self._session_factory() as session:
            stmt = (
                select(UserTemplateRow)
                .where(UserTemplateRow.user_id == user_id)
                .order_by(UserTemplateRow.created_at.desc())
            )
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_user_template(self, template_id: str) -> UserTemplateRow | None:
        """Get a single user template by ID."""
        async with self._session_factory() as session:
            return await session.get(UserTemplateRow, template_id)

    async def update_user_template(
        self,
        template_id: str,
        name: str | None = None,
        config_json: str | None = None,
    ) -> UserTemplateRow | None:
        """Update a user template. Returns None if not found."""
        async with self._session_factory() as session:
            row = await session.get(UserTemplateRow, template_id)
            if row is None:
                return None
            if name is not None:
                row.name = name
            if config_json is not None:
                row.config_json = config_json
            row.updated_at = datetime.now(UTC)
            await session.commit()
            await session.refresh(row)
            return row

    async def delete_user_template(self, template_id: str) -> bool:
        """Delete a user template. Returns True if deleted, False if not found."""
        async with self._session_factory() as session:
            row = await session.get(UserTemplateRow, template_id)
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def set_pipeline_template_config(
        self,
        pipeline_id: str,
        template_id: str,
        config_json: str,
    ) -> None:
        """Associate a template with a pipeline and store the resolved config."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                row.template_id = template_id
                row.template_config_json = config_json
                await session.commit()

    # ── Pipeline events ──────────────────────────────────────────────

    async def log_event(
        self,
        *,
        pipeline_id: str,
        task_id: str | None,
        event_type: str,
        payload: dict,
    ) -> None:
        async with self._session_factory() as session:
            event = PipelineEventRow(
                pipeline_id=pipeline_id,
                task_id=task_id,
                event_type=event_type,
                payload=payload,
            )
            session.add(event)
            await session.commit()

    async def list_events(
        self,
        pipeline_id: str,
        *,
        task_id: str | None = None,
        event_type: str | None = None,
    ) -> list[PipelineEventRow]:
        async with self._session_factory() as session:
            stmt = (
                select(PipelineEventRow)
                .where(PipelineEventRow.pipeline_id == pipeline_id)
                .order_by(PipelineEventRow.created_at.asc())
            )
            if task_id is not None:
                stmt = stmt.where(PipelineEventRow.task_id == task_id)
            if event_type is not None:
                stmt = stmt.where(PipelineEventRow.event_type == event_type)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    # ── Task questions ────────────────────────────────────────────────

    async def create_task_question(
        self,
        *,
        task_id: str,
        pipeline_id: str,
        question: str,
        suggestions: list[str] | None = None,
        context: dict | None = None,
        stage: str | None = None,
    ) -> TaskQuestionRow:
        async with self._session_factory() as session:
            row = TaskQuestionRow(
                task_id=task_id,
                pipeline_id=pipeline_id,
                question=question,
                suggestions=json.dumps(suggestions) if suggestions else None,
                context=json.dumps(context) if context else None,
                stage=stage,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def answer_question(
        self,
        question_id: str,
        answer: str,
        answered_by: str = "human",
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(TaskQuestionRow, question_id)
            if row:
                row.answer = answer
                row.answered_by = answered_by
                row.answered_at = datetime.now(UTC).isoformat()
                await session.commit()

    async def get_pending_questions(self, pipeline_id: str) -> list[TaskQuestionRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.pipeline_id == pipeline_id)
                .where(TaskQuestionRow.answer.is_(None))
                .order_by(TaskQuestionRow.created_at)
            )
            return list(result.scalars().all())

    async def get_task_questions(self, task_id: str) -> list[TaskQuestionRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.task_id == task_id)
                .order_by(TaskQuestionRow.created_at)
            )
            return list(result.scalars().all())

    async def get_planning_questions(self, pipeline_id: str) -> list[TaskQuestionRow]:
        """Get all planning-phase questions for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.pipeline_id == pipeline_id)
                .where(TaskQuestionRow.stage == "planning")
                .order_by(TaskQuestionRow.created_at)
            )
            return list(result.scalars().all())

    async def get_expired_questions(self, timeout_seconds: int) -> list[TaskQuestionRow]:
        cutoff = datetime.now(UTC).timestamp() - timeout_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=UTC).isoformat()
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.answer.is_(None))
                .where(TaskQuestionRow.created_at < cutoff_iso)
            )
            return list(result.scalars().all())

    # ── Task interjections ─────────────────────────────────────────────

    async def create_interjection(
        self,
        *,
        task_id: str,
        pipeline_id: str,
        message: str,
    ) -> InterjectionRow:
        async with self._session_factory() as session:
            row = InterjectionRow(
                task_id=task_id,
                pipeline_id=pipeline_id,
                message=message,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_pending_interjections(self, task_id: str) -> list[InterjectionRow]:
        async with self._session_factory() as session:
            result = await session.execute(
                select(InterjectionRow)
                .where(InterjectionRow.task_id == task_id)
                .where(InterjectionRow.delivered == False)  # noqa: E712
                .order_by(InterjectionRow.created_at)
            )
            return list(result.scalars().all())

    async def mark_interjection_delivered(self, interjection_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(InterjectionRow, interjection_id)
            if row:
                row.delivered = True
                row.delivered_at = datetime.now(UTC).isoformat()
                await session.commit()

    # ── Lessons ────────────────────────────────────────────────────────

    MAX_LESSONS = 500

    async def add_lesson(
        self,
        *,
        scope: str,
        category: str,
        title: str,
        content: str,
        trigger: str,
        resolution: str,
        project_dir: str | None = None,
        confidence: float = 0.5,
    ) -> str:
        """Add a lesson. Returns the lesson ID.

        After inserting, prunes excess lessons if total count exceeds
        MAX_LESSONS, removing lowest-value rows (lowest hit_count, oldest first).
        """
        now = datetime.now(UTC).isoformat()
        normalized_trigger = self._normalize_trigger(trigger)
        row = LessonRow(
            scope=scope,
            project_dir=project_dir,
            category=category,
            title=title,
            content=content,
            trigger=normalized_trigger,
            resolution=resolution,
            hit_count=1,
            created_at=now,
            last_hit_at=now,
            confidence=confidence,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.flush()
            lesson_id = row.id

            # Prune excess lessons: single DELETE with subquery
            # Count how many to remove (0 if under cap)
            count_result = await session.execute(select(func.count()).select_from(LessonRow))
            total = count_result.scalar() or 0
            excess = total - self.MAX_LESSONS
            if excess > 0:
                prune_ids = (
                    select(LessonRow.id)
                    .order_by(LessonRow.hit_count.asc(), LessonRow.created_at.asc())
                    .limit(excess)
                ).scalar_subquery()
                await session.execute(sa_delete(LessonRow).where(LessonRow.id.in_(prune_ids)))

            await session.commit()
            return lesson_id

    @staticmethod
    def _normalize_trigger(trigger: str) -> str:
        """Normalize a trigger string for dedup comparison.

        Lowercases, strips whitespace, and collapses internal whitespace.
        """
        import re

        return re.sub(r"\s+", " ", trigger.strip().lower())

    async def find_matching_lesson(
        self, trigger: str, project_dir: str | None = None
    ) -> LessonRow | None:
        """Find a lesson whose trigger matches (normalized substring in either direction)."""
        normalized = self._normalize_trigger(trigger)
        async with self._session_factory() as session:
            # Use parameterized raw SQL for normalized comparison:
            # lower/trim both sides, then check substring in either direction
            query = select(LessonRow).where(
                or_(
                    text("instr(LOWER(TRIM(trigger)), :norm_trigger) > 0").bindparams(
                        norm_trigger=normalized
                    ),
                    text("instr(:norm_trigger2, LOWER(TRIM(trigger))) > 0").bindparams(
                        norm_trigger2=normalized
                    ),
                )
            )
            if project_dir:
                query = query.where(
                    or_(LessonRow.scope == "global", LessonRow.project_dir == project_dir)
                )
            query = query.order_by(LessonRow.hit_count.desc()).limit(1)
            result = await session.execute(query)
            return result.scalar_one_or_none()

    async def bump_lesson_hit(self, lesson_id: str) -> None:
        """Increment hit_count and update last_hit_at."""
        async with self._session_factory() as session:
            row = await session.get(LessonRow, lesson_id)
            if row:
                row.hit_count += 1
                row.last_hit_at = datetime.now(UTC).isoformat()
                row.confidence = min(1.0, row.confidence + min(0.1, (1.0 - row.confidence) * 0.2))
                await session.commit()

    async def get_relevant_lessons(
        self,
        project_dir: str | None = None,
        categories: list[str] | None = None,
        max_count: int = 20,
        max_tokens: int = 2000,
    ) -> list[LessonRow]:
        """Get lessons ranked by effective confidence, capped at token budget.

        Returns both global and project-scoped lessons for the given project_dir.
        Effective confidence = stored confidence - decay for staleness.
        Token budget is approximate (1 token ~ 4 chars).
        """
        # Compute effective confidence in SQL:
        # CASE WHEN last_hit_at IS NOT NULL
        #   THEN confidence - MAX(0, (julianday('now') - julianday(last_hit_at)) - 30) / 300
        #   ELSE confidence - 0.2
        # END
        effective_conf = case(
            (
                LessonRow.last_hit_at.isnot(None),
                LessonRow.confidence
                - func.max(0, (func.julianday("now") - func.julianday(LessonRow.last_hit_at) - 30))
                / 300,
            ),
            else_=LessonRow.confidence - 0.2,
        ).label("effective_confidence")

        async with self._session_factory() as session:
            query = select(LessonRow, effective_conf)
            conditions = []
            if project_dir:
                conditions.append(
                    or_(LessonRow.scope == "global", LessonRow.project_dir == project_dir)
                )
            else:
                conditions.append(LessonRow.scope == "global")
            if categories:
                conditions.append(LessonRow.category.in_(categories))

            # Filter: effective_confidence >= 0.1
            conditions.append(effective_conf >= 0.1)
            query = query.where(*conditions)
            query = query.order_by(effective_conf.desc()).limit(max_count)
            result = await session.execute(query)
            rows = [row[0] for row in result.all()]

        # Apply token budget
        char_budget = max_tokens * 4
        total_chars = 0
        filtered = []
        for row in rows:
            row_chars = len(row.title) + len(row.content) + len(row.resolution)
            if total_chars + row_chars > char_budget:
                break
            filtered.append(row)
            total_chars += row_chars
        return filtered

    async def list_all_lessons(self) -> list[LessonRow]:
        """Return all lessons."""
        async with self._session_factory() as session:
            result = await session.execute(select(LessonRow).order_by(LessonRow.hit_count.desc()))
            return list(result.scalars().all())

    async def prune_stale_lessons(self, max_age_days: int = 90) -> int:
        """Delete lessons not hit in max_age_days. Returns count deleted."""
        import logging as _logging
        from datetime import timedelta  # noqa: F811

        _logger = _logging.getLogger("forge")
        cutoff = (datetime.now(UTC) - timedelta(days=max_age_days)).isoformat()
        async with self._session_factory() as session:
            result = await session.execute(
                sa_delete(LessonRow).where(LessonRow.last_hit_at < cutoff)
            )
            await session.commit()
            count = result.rowcount
            if count:
                _logger.info("Pruned %d stale lessons (older than %d days)", count, max_age_days)
            return count

    async def get_lesson_by_id(self, lesson_id: str) -> LessonRow | None:
        """Get a single lesson by exact ID or ID prefix."""
        async with self._session_factory() as session:
            # Try exact match first
            result = await session.execute(select(LessonRow).where(LessonRow.id == lesson_id))
            row = result.scalars().first()
            if row:
                return row
            # Try prefix match
            result = await session.execute(
                select(LessonRow).where(LessonRow.id.startswith(lesson_id))
            )
            return result.scalars().first()

    async def delete_lesson(self, lesson_id: str) -> bool:
        """Delete a lesson by exact ID. Returns True if deleted."""
        async with self._session_factory() as session:
            result = await session.execute(sa_delete(LessonRow).where(LessonRow.id == lesson_id))
            await session.commit()
            return result.rowcount > 0

    async def clear_lessons(self, project_dir: str | None = None) -> int:
        """Delete lessons. If project_dir given, only project-scoped. Otherwise all."""
        async with self._session_factory() as session:
            if project_dir:
                count_q = (
                    select(func.count())
                    .select_from(LessonRow)
                    .where(LessonRow.project_dir == project_dir)
                )
                del_q = sa_delete(LessonRow).where(LessonRow.project_dir == project_dir)
            else:
                count_q = select(func.count()).select_from(LessonRow)
                del_q = sa_delete(LessonRow)
            count = (await session.execute(count_q)).scalar() or 0
            await session.execute(del_q)
            await session.commit()
            return count

    # ── Analytics / Metrics ────────────────────────────────────────────

    async def set_task_timing(
        self,
        task_id: str,
        *,
        started_at: str | None = None,
        completed_at: str | None = None,
        agent_duration_s: float | None = None,
        review_duration_s: float | None = None,
        lint_duration_s: float | None = None,
        merge_duration_s: float | None = None,
    ) -> None:
        """Update timing fields on a TaskRow. Only non-None kwargs are written."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                if started_at is not None:
                    task.started_at = started_at
                if completed_at is not None:
                    task.completed_at = completed_at
                if agent_duration_s is not None:
                    task.agent_duration_s = agent_duration_s
                if review_duration_s is not None:
                    task.review_duration_s = review_duration_s
                if lint_duration_s is not None:
                    task.lint_duration_s = lint_duration_s
                if merge_duration_s is not None:
                    task.merge_duration_s = merge_duration_s
                await session.commit()

    async def set_task_turns(self, task_id: str, num_turns: int, max_turns: int) -> None:
        """Store agent conversation turn counts for a task."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.num_turns = num_turns
                task.max_turns = max_turns
                await session.commit()

    async def set_task_error(self, task_id: str, error_message: str) -> None:
        """Store the last error message on a task."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.error_message = error_message
                await session.commit()

    async def finalize_pipeline_metrics(self, pipeline_id: str) -> None:
        """Compute and store aggregated pipeline metrics by summing task rows.

        Updates PipelineRow fields: duration_s, total_input_tokens,
        total_output_tokens, tasks_succeeded, tasks_failed, total_retries.
        Duration is computed from pipeline created_at to now minus paused_duration.
        """
        async with self._session_factory() as session:
            pipeline = await session.get(PipelineRow, pipeline_id)
            if not pipeline:
                return

            # Compute duration from created_at to now minus paused_duration
            if pipeline.created_at:
                try:
                    created = datetime.fromisoformat(pipeline.created_at)
                    now = datetime.now(UTC)
                    wall_seconds = (now - created).total_seconds()
                    paused = pipeline.paused_duration or 0.0
                    pipeline.duration_s = max(0.0, wall_seconds - paused)
                except (ValueError, TypeError):
                    pipeline.duration_s = 0.0
            else:
                pipeline.duration_s = 0.0

            # Sum task-level metrics with SQL aggregates
            agg = await session.execute(
                select(
                    func.coalesce(func.sum(TaskRow.input_tokens), 0),
                    func.coalesce(func.sum(TaskRow.output_tokens), 0),
                    func.count(case((TaskRow.state == "done", 1))),
                    func.count(case((TaskRow.state == "error", 1))),
                    func.coalesce(func.sum(TaskRow.retry_count), 0),
                ).where(TaskRow.pipeline_id == pipeline_id)
            )
            row = agg.one()
            pipeline.total_input_tokens = row[0]
            pipeline.total_output_tokens = row[1]
            pipeline.tasks_succeeded = row[2]
            pipeline.tasks_failed = row[3]
            pipeline.total_retries = row[4]

            await session.commit()

    async def get_pipeline_stats(self, pipeline_id: str) -> dict:
        """Return full pipeline + per-task metrics dict for the stats command."""
        async with self._session_factory() as session:
            pipeline = await session.get(PipelineRow, pipeline_id)
            if not pipeline:
                return {}

            # Fetch only the columns needed for per-task metrics
            task_cols = [
                TaskRow.id,
                TaskRow.title,
                TaskRow.state,
                TaskRow.started_at,
                TaskRow.completed_at,
                TaskRow.agent_duration_s,
                TaskRow.review_duration_s,
                TaskRow.lint_duration_s,
                TaskRow.merge_duration_s,
                TaskRow.cost_usd,
                TaskRow.agent_cost_usd,
                TaskRow.review_cost_usd,
                TaskRow.input_tokens,
                TaskRow.output_tokens,
                TaskRow.retry_count,
                TaskRow.num_turns,
                TaskRow.max_turns,
                TaskRow.error_message,
            ]
            result = await session.execute(
                select(*task_cols).where(TaskRow.pipeline_id == pipeline_id)
            )
            rows = result.all()

            task_metrics = []
            for t in rows:
                task_metrics.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "state": t.state,
                        "started_at": t.started_at,
                        "completed_at": t.completed_at,
                        "agent_duration_s": t.agent_duration_s or 0.0,
                        "review_duration_s": t.review_duration_s or 0.0,
                        "lint_duration_s": t.lint_duration_s or 0.0,
                        "merge_duration_s": t.merge_duration_s or 0.0,
                        "cost_usd": t.cost_usd or 0.0,
                        "agent_cost_usd": t.agent_cost_usd or 0.0,
                        "review_cost_usd": t.review_cost_usd or 0.0,
                        "input_tokens": t.input_tokens or 0,
                        "output_tokens": t.output_tokens or 0,
                        "retry_count": t.retry_count or 0,
                        "num_turns": t.num_turns or 0,
                        "max_turns": t.max_turns or 0,
                        "error_message": t.error_message,
                    }
                )

            return {
                "id": pipeline.id,
                "description": pipeline.description,
                "status": pipeline.status,
                "created_at": pipeline.created_at,
                "completed_at": pipeline.completed_at,
                "duration_s": pipeline.duration_s or 0.0,
                "total_cost_usd": pipeline.total_cost_usd or 0.0,
                "planner_cost_usd": pipeline.planner_cost_usd or 0.0,
                "total_input_tokens": pipeline.total_input_tokens or 0,
                "total_output_tokens": pipeline.total_output_tokens or 0,
                "tasks_succeeded": pipeline.tasks_succeeded or 0,
                "tasks_failed": pipeline.tasks_failed or 0,
                "total_retries": pipeline.total_retries or 0,
                "tasks": task_metrics,
            }

    async def get_pipeline_export_data(self, pipeline_id: str) -> dict | None:
        """Return full pipeline + per-task data dict for export. Returns None if not found."""
        async with self._session_factory() as session:
            pipeline = await session.get(PipelineRow, pipeline_id)
            if not pipeline:
                return None

            # Fetch only needed columns for export
            export_cols = [
                TaskRow.id,
                TaskRow.title,
                TaskRow.description,
                TaskRow.state,
                TaskRow.files,
                TaskRow.assigned_agent,
                TaskRow.cost_usd,
                TaskRow.agent_cost_usd,
                TaskRow.review_cost_usd,
                TaskRow.retry_count,
                TaskRow.input_tokens,
                TaskRow.output_tokens,
                TaskRow.started_at,
                TaskRow.completed_at,
                TaskRow.agent_duration_s,
                TaskRow.review_duration_s,
                TaskRow.lint_duration_s,
                TaskRow.merge_duration_s,
                TaskRow.num_turns,
                TaskRow.error_message,
                TaskRow.complexity,
                TaskRow.repo_id,
            ]
            result = await session.execute(
                select(*export_cols).where(TaskRow.pipeline_id == pipeline_id)
            )
            rows = result.all()

            task_list = []
            for t in rows:
                task_list.append(
                    {
                        "id": t.id,
                        "title": t.title,
                        "description": t.description,
                        "state": t.state,
                        "files": t.files if t.files is not None else [],
                        "assigned_agent": t.assigned_agent,
                        "cost_usd": t.cost_usd or 0.0,
                        "agent_cost_usd": t.agent_cost_usd or 0.0,
                        "review_cost_usd": t.review_cost_usd or 0.0,
                        "retry_count": t.retry_count or 0,
                        "input_tokens": t.input_tokens or 0,
                        "output_tokens": t.output_tokens or 0,
                        "started_at": t.started_at,
                        "completed_at": t.completed_at,
                        "agent_duration_s": t.agent_duration_s or 0.0,
                        "review_duration_s": t.review_duration_s or 0.0,
                        "lint_duration_s": t.lint_duration_s or 0.0,
                        "merge_duration_s": t.merge_duration_s or 0.0,
                        "num_turns": t.num_turns or 0,
                        "error_message": t.error_message,
                        "complexity": t.complexity,
                        "repo_id": t.repo_id or "default",
                    }
                )

            return {
                "id": pipeline.id,
                "description": pipeline.description,
                "status": pipeline.status,
                "created_at": pipeline.created_at,
                "completed_at": pipeline.completed_at,
                "duration_s": pipeline.duration_s or 0.0,
                "total_cost_usd": pipeline.total_cost_usd or 0.0,
                "planner_cost_usd": pipeline.planner_cost_usd or 0.0,
                "total_input_tokens": pipeline.total_input_tokens or 0,
                "total_output_tokens": pipeline.total_output_tokens or 0,
                "tasks_succeeded": pipeline.tasks_succeeded or 0,
                "tasks_failed": pipeline.tasks_failed or 0,
                "total_retries": pipeline.total_retries or 0,
                "base_branch": pipeline.base_branch,
                "branch_name": pipeline.branch_name,
                "pr_url": pipeline.pr_url,
                "model_strategy": pipeline.model_strategy or "auto",
                "project_name": pipeline.project_name,
                "tasks": task_list,
            }

    async def get_pipeline_trends(
        self, project_path: str | None = None, limit: int = 20
    ) -> list[dict]:
        """Return recent pipeline metrics for trend analysis, ordered by created_at descending."""
        async with self._session_factory() as session:
            stmt = select(PipelineRow).order_by(PipelineRow.created_at.desc())
            if project_path is not None:
                stmt = stmt.where(PipelineRow.project_path == project_path)
            stmt = stmt.limit(limit)
            result = await session.execute(stmt)
            pipelines = list(result.scalars().all())

            return [
                {
                    "id": p.id,
                    "description": p.description,
                    "status": p.status,
                    "duration_s": p.duration_s or 0.0,
                    "total_cost_usd": p.total_cost_usd or 0.0,
                    "total_input_tokens": p.total_input_tokens or 0,
                    "total_output_tokens": p.total_output_tokens or 0,
                    "tasks_succeeded": p.tasks_succeeded or 0,
                    "tasks_failed": p.tasks_failed or 0,
                    "total_retries": p.total_retries or 0,
                    "created_at": p.created_at,
                    "total_tasks": (p.tasks_succeeded or 0) + (p.tasks_failed or 0),
                }
                for p in pipelines
            ]

    async def get_pipeline_analytics(self, limit: int = 500) -> dict:
        """Aggregate analytics across recent pipelines (default last 500)."""
        async with self._session_factory() as session:
            stmt = select(PipelineRow).order_by(PipelineRow.created_at.desc()).limit(limit)
            result = await session.execute(stmt)
            pipelines = list(result.scalars().all())

            total = len(pipelines)
            passed = 0
            failed = 0
            partial = 0
            cancelled = 0
            other = 0

            for p in pipelines:
                s = p.status or ""
                if s in ("done", "complete"):
                    passed += 1
                elif s == "error":
                    failed += 1
                elif s == "cancelled":
                    cancelled += 1
                else:
                    succ = p.tasks_succeeded or 0
                    fail = p.tasks_failed or 0
                    if succ > 0 and fail > 0:
                        partial += 1
                    else:
                        other += 1

            # Current streak: consecutive successes from most recent
            current_streak = 0
            for p in pipelines:
                if (p.status or "") in ("done", "complete"):
                    current_streak += 1
                else:
                    break

            # Longest streak: scan oldest-first
            longest_streak = 0
            streak = 0
            for p in reversed(pipelines):
                if (p.status or "") in ("done", "complete"):
                    streak += 1
                    if streak > longest_streak:
                        longest_streak = streak
                else:
                    streak = 0

            return {
                "total": total,
                "passed": passed,
                "failed": failed,
                "partial": partial,
                "cancelled": cancelled,
                "other": other,
                "current_streak": current_streak,
                "longest_streak": longest_streak,
            }

    async def purge_old_pipelines(self, older_than_days: int = 30) -> int:
        """Delete pipelines and associated tasks older than N days. Returns count of deleted pipelines."""
        from datetime import timedelta

        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        cutoff_iso = cutoff.isoformat()

        async with self._session_factory() as session:
            # Use subquery to batch-delete tasks and pipelines in two statements
            old_ids_subq = (
                select(PipelineRow.id).where(PipelineRow.created_at < cutoff_iso)
            ).scalar_subquery()

            # Delete associated tasks first (uses pipeline_id index)
            await session.execute(sa_delete(TaskRow).where(TaskRow.pipeline_id.in_(old_ids_subq)))

            # Delete pipelines
            del_result = await session.execute(
                sa_delete(PipelineRow).where(PipelineRow.created_at < cutoff_iso)
            )
            await session.commit()
            return del_result.rowcount

    async def get_retry_summary(self, pipeline_id: str | None = None) -> list[dict]:
        """Aggregate retry counts and error messages across tasks, grouped by error pattern.

        Returns list sorted by total_retries descending.
        """
        async with self._session_factory() as session:
            stmt = select(TaskRow).where(TaskRow.retry_count > 0)
            if pipeline_id is not None:
                stmt = stmt.where(TaskRow.pipeline_id == pipeline_id)
            result = await session.execute(stmt)
            tasks = list(result.scalars().all())

            # Group by normalized error pattern
            patterns: dict[str, dict] = {}
            for t in tasks:
                raw = t.error_message or "unknown error"
                # Normalize: first 120 chars, lowercased, whitespace-collapsed
                import re as _re

                pattern = _re.sub(r"\s+", " ", raw[:120].lower()).strip()
                if pattern not in patterns:
                    patterns[pattern] = {
                        "error_pattern": pattern,
                        "total_retries": 0,
                        "task_count": 0,
                        "task_ids": [],
                    }
                patterns[pattern]["total_retries"] += t.retry_count or 0
                patterns[pattern]["task_count"] += 1
                patterns[pattern]["task_ids"].append(t.id)

            # Sort by total_retries descending
            return sorted(patterns.values(), key=lambda x: x["total_retries"], reverse=True)

    # ── CI Auto-Fix ──────────────────────────────────────────────────

    async def update_pipeline_ci_fix(self, pipeline_id: str, **kwargs) -> None:
        """Update CI fix fields on a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if row:
                for key, value in kwargs.items():
                    if hasattr(row, key) and key.startswith("ci_fix_"):
                        setattr(row, key, value)
                await session.commit()

    async def get_pipeline_ci_fix_state(self, pipeline_id: str) -> dict:
        """Get CI fix state for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(select(PipelineRow).where(PipelineRow.id == pipeline_id))
            row = result.scalar_one_or_none()
            if not row:
                return {}
            return {
                "ci_fix_enabled": row.ci_fix_enabled,
                "ci_fix_status": row.ci_fix_status,
                "ci_fix_attempt": row.ci_fix_attempt,
                "ci_fix_max_retries": row.ci_fix_max_retries,
                "ci_fix_cost_usd": row.ci_fix_cost_usd,
                "ci_fix_log": row.ci_fix_log,
            }

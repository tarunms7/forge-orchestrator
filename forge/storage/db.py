"""Unified database layer. SQLAlchemy 2.0 async. SQLite default, Postgres optional.

Single Database class for ALL Forge data: auth (users, audit logs),
pipelines, tasks, and agents.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, JSON, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


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
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    settings_json: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)


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
    metadata_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)


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
    assigned_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    retry_count: Mapped[int] = mapped_column(default=0)
    branch_name: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    worktree_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    pipeline_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    review_feedback: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    retry_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    agent_cost_usd: Mapped[float] = mapped_column(default=0.0)
    review_cost_usd: Mapped[float] = mapped_column(default=0.0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    approval_context: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    prior_diff: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    implementation_summary: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    session_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    questions_asked: Mapped[int] = mapped_column(default=0)
    questions_limit: Mapped[int] = mapped_column(default=3)


class AgentRow(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    state: Mapped[str] = mapped_column(String, default="idle")
    current_task: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)


class PipelineRow(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    description: Mapped[str] = mapped_column(String)
    project_dir: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="planning")
    model_strategy: Mapped[str] = mapped_column(String, default="auto")
    task_graph_json: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    created_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    completed_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    pr_url: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    base_branch: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    branch_name: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    cancelled_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    build_cmd: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    test_cmd: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    planner_cost_usd: Mapped[float] = mapped_column(default=0.0)
    total_cost_usd: Mapped[float] = mapped_column(default=0.0)
    budget_limit_usd: Mapped[float] = mapped_column(default=0.0)
    estimated_cost_usd: Mapped[float] = mapped_column(default=0.0)
    paused: Mapped[bool] = mapped_column(default=False)
    conventions_json: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    require_approval: Mapped[bool] = mapped_column(default=False)
    github_issue_url: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    github_issue_number: Mapped[Optional[int]] = mapped_column(default=None)
    template_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    template_config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    # Contract Builder output (JSON blob)
    contracts_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    paused_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    paused_duration: Mapped[float] = mapped_column(default=0.0)
    # Cross-project tracking
    project_path: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    project_name: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    # Executor tracking for orphan detection
    executor_pid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=None)
    executor_token: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)


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
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


class PipelineEventRow(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    task_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(timezone.utc).isoformat())


class TaskQuestionRow(Base):
    """Agent question awaiting human answer."""

    __tablename__ = "task_questions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4()),
    )
    task_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    suggestions: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    answered_by: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)
    context: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(
        String, default=lambda: datetime.now(timezone.utc).isoformat(),
    )
    answered_at: Mapped[Optional[str]] = mapped_column(String, nullable=True, default=None)


# ── All model classes (used by _add_missing_columns) ──────────────────
_ALL_MODELS = (UserRow, AuditLogRow, TaskRow, AgentRow, PipelineRow, UserTemplateRow, PipelineEventRow, TaskQuestionRow)


class Database:
    """Unified async database interface. One DB for everything."""

    def __init__(self, url: str) -> None:
        self._engine = create_async_engine(url)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)

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
        if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name):
            raise ValueError(f"Invalid SQL identifier: {name!r}")
        return name

    @staticmethod
    def _add_missing_columns(connection) -> None:
        """Add columns that exist in the ORM model but not in the DB table."""
        from sqlalchemy import inspect as sa_inspect, text

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
                        text(f"ALTER TABLE {table_name} ADD COLUMN {attr.name} {col_type} {nullable}{default}")
                    )

    async def close(self) -> None:
        await self._engine.dispose()

    # ── Auth: Users ───────────────────────────────────────────────────

    async def create_user(
        self, *, email: str, password: str, display_name: str,
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
            result = await session.execute(
                select(UserRow).where(UserRow.email == email)
            )
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
            result = await session.execute(
                select(UserRow).where(UserRow.email == email)
            )
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
        self, user_id: str, action: str,
        metadata: dict | None = None, ip: str | None = None,
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
    ) -> None:
        async with self._session_factory() as session:
            row = TaskRow(
                id=id, title=title, description=description,
                files=files, depends_on=depends_on, complexity=complexity,
                pipeline_id=pipeline_id,
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

    async def list_tasks_by_pipeline(self, pipeline_id: str) -> list[TaskRow]:
        async with self._session_factory() as session:
            stmt = select(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def add_task_cost(self, task_id: str, cost: float) -> None:
        async with self._session_factory() as session:
            await session.execute(
                text("UPDATE tasks SET cost_usd = COALESCE(cost_usd, 0) + :cost WHERE id = :tid"),
                {"cost": cost, "tid": task_id},
            )
            await session.commit()

    async def add_task_agent_cost(
        self, task_id: str, cost: float, input_tokens: int, output_tokens: int,
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
                text("UPDATE pipelines SET total_cost_usd = COALESCE(total_cost_usd, 0) + :delta WHERE id = :pid"),
                {"delta": cost, "pid": pipeline_id},
            )
            await session.commit()

    async def set_pipeline_planner_cost(self, pipeline_id: str, cost: float) -> None:
        """Set the planner cost for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.planner_cost_usd = cost
                await session.commit()

    async def get_pipeline_cost(self, pipeline_id: str) -> float:
        """Return the current total cost for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                return row.total_cost_usd or 0.0
            return 0.0

    async def get_pipeline_budget(self, pipeline_id: str) -> float:
        """Return the budget limit for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
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

    async def list_agents(self, prefix: str | None = None) -> list[AgentRow]:
        async with self._session_factory() as session:
            stmt = select(AgentRow)
            if prefix:
                stmt = stmt.where(AgentRow.id.startswith(prefix))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def get_task_counts_by_state(self) -> dict[str, int]:
        async with self._session_factory() as session:
            stmt = select(TaskRow.state, func.count(TaskRow.id)).group_by(TaskRow.state)
            result = await session.execute(stmt)
            return {state: count for state, count in result.all()}

    # ── Pipelines ─────────────────────────────────────────────────────

    async def create_pipeline(
        self, id: str, description: str, project_dir: str,
        model_strategy: str = "auto", user_id: str | None = None,
        base_branch: str | None = None, branch_name: str | None = None,
        build_cmd: str | None = None, test_cmd: str | None = None,
        budget_limit_usd: float = 0.0,
        github_issue_url: str | None = None,
        github_issue_number: int | None = None,
        project_path: str | None = None,
        project_name: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            row = PipelineRow(
                id=id, description=description, project_dir=project_dir,
                model_strategy=model_strategy, user_id=user_id,
                base_branch=base_branch, branch_name=branch_name,
                build_cmd=build_cmd, test_cmd=test_cmd,
                budget_limit_usd=budget_limit_usd,
                github_issue_url=github_issue_url,
                github_issue_number=github_issue_number,
                project_path=project_path,
                project_name=project_name,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            session.add(row)
            await session.commit()

    async def get_pipeline(self, pipeline_id: str) -> PipelineRow | None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
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
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.status = status
                if status in ("complete", "error"):
                    row.completed_at = datetime.now(timezone.utc).isoformat()
                await session.commit()

    async def set_pipeline_plan(self, pipeline_id: str, task_graph_json: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.task_graph_json = task_graph_json
                row.status = "planned"
                await session.commit()

    async def set_pipeline_pr_url(self, pipeline_id: str, pr_url: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.pr_url = pr_url
                await session.commit()

    async def set_pipeline_base_branch(self, pipeline_id: str, base_branch: str) -> None:
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.base_branch = base_branch
                await session.commit()

    async def set_pipeline_branch_name(self, pipeline_id: str, branch_name: str) -> None:
        """Store the computed pipeline branch name (custom or auto-generated)."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.branch_name = branch_name
                await session.commit()

    async def set_pipeline_contracts(self, pipeline_id: str, contracts_json: str) -> None:
        """Store the ContractSet JSON for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.contracts_json = contracts_json
                await session.commit()

    async def get_pipeline_contracts(self, pipeline_id: str) -> str | None:
        """Get the ContractSet JSON for a pipeline."""
        pipeline = await self.get_pipeline(pipeline_id)
        return getattr(pipeline, "contracts_json", None) if pipeline else None

    async def list_pipelines(
        self, user_id: str | None = None, project_path: str | None = None,
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
        from sqlalchemy import delete

        async with self._session_factory() as session:
            # Reset pipeline
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
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
                delete(TaskRow).where(TaskRow.pipeline_id == pipeline_id)
            )
            tasks_reset = del_tasks.rowcount

            # Delete all pipeline events
            del_result = await session.execute(
                delete(PipelineEventRow).where(
                    PipelineEventRow.pipeline_id == pipeline_id
                )
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
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            pipeline = result.scalar_one_or_none()
            if pipeline is None:
                return {"tasks_cancelled": 0}

            pipeline.status = "cancelled"
            pipeline.cancelled_at = datetime.now(timezone.utc).isoformat()

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

    async def update_pipeline_conventions(self, pipeline_id: str, conventions_json: str) -> None:
        """Store conventions JSON for a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.conventions_json = conventions_json
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
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.paused = paused
                await session.commit()

    async def set_pipeline_paused_at(self, pipeline_id: str, paused_at: str | None) -> None:
        """Set or clear the paused_at timestamp on a pipeline."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.paused_at = paused_at
                await session.commit()

    async def add_pipeline_paused_duration(self, pipeline_id: str, elapsed_seconds: float) -> None:
        """Add elapsed_seconds to the pipeline's paused_duration accumulator."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.paused_duration = (row.paused_duration or 0.0) + elapsed_seconds
                await session.commit()

    # ── User templates ─────────────────────────────────────────────

    async def create_user_template(
        self, user_id: str, name: str, config_json: str,
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
        self, template_id: str, name: str | None = None, config_json: str | None = None,
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
            row.updated_at = datetime.now(timezone.utc)
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
        self, pipeline_id: str, template_id: str, config_json: str,
    ) -> None:
        """Associate a template with a pipeline and store the resolved config."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(PipelineRow).where(PipelineRow.id == pipeline_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.template_id = template_id
                row.template_config_json = config_json
                await session.commit()

    # ── Pipeline events ──────────────────────────────────────────────

    async def log_event(
        self, *, pipeline_id: str, task_id: str | None, event_type: str, payload: dict,
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
        self, pipeline_id: str, *, task_id: str | None = None, event_type: str | None = None,
    ) -> list[PipelineEventRow]:
        async with self._session_factory() as session:
            stmt = select(PipelineEventRow).where(
                PipelineEventRow.pipeline_id == pipeline_id
            ).order_by(PipelineEventRow.created_at.asc())
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
    ) -> TaskQuestionRow:
        async with self._session_factory() as session:
            row = TaskQuestionRow(
                task_id=task_id,
                pipeline_id=pipeline_id,
                question=question,
                suggestions=json.dumps(suggestions) if suggestions else None,
                context=json.dumps(context) if context else None,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def answer_question(
        self, question_id: str, answer: str, answered_by: str = "human",
    ) -> None:
        async with self._session_factory() as session:
            row = await session.get(TaskQuestionRow, question_id)
            if row:
                row.answer = answer
                row.answered_by = answered_by
                row.answered_at = datetime.now(timezone.utc).isoformat()
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

    async def get_expired_questions(self, timeout_seconds: int) -> list[TaskQuestionRow]:
        cutoff = datetime.now(timezone.utc).timestamp() - timeout_seconds
        cutoff_iso = datetime.fromtimestamp(cutoff, tz=timezone.utc).isoformat()
        async with self._session_factory() as session:
            result = await session.execute(
                select(TaskQuestionRow)
                .where(TaskQuestionRow.answer.is_(None))
                .where(TaskQuestionRow.created_at < cutoff_iso)
            )
            return list(result.scalars().all())

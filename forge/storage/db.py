"""Unified database layer. SQLAlchemy 2.0 async. SQLite default, Postgres optional.

Single Database class for ALL Forge data: auth (users, audit logs),
pipelines, tasks, and agents.
"""

import json
import uuid
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import DateTime, String, Text, JSON, func, select
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
        default=lambda: datetime.now(timezone.utc),
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
    pipeline_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    review_feedback: Mapped[str | None] = mapped_column(String, nullable=True, default=None)


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


class PipelineEventRow(Base):
    __tablename__ = "pipeline_events"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    pipeline_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    task_id: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[str] = mapped_column(String, default=lambda: datetime.now(timezone.utc).isoformat())


# ── All model classes (used by _add_missing_columns) ──────────────────
_ALL_MODELS = (UserRow, AuditLogRow, TaskRow, AgentRow, PipelineRow, PipelineEventRow)


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
    def _add_missing_columns(connection) -> None:
        """Add columns that exist in the ORM model but not in the DB table."""
        from sqlalchemy import inspect as sa_inspect, text

        inspector = sa_inspect(connection)
        for table_cls in _ALL_MODELS:
            table_name = table_cls.__tablename__
            if not inspector.has_table(table_name):
                continue
            existing = {col["name"] for col in inspector.get_columns(table_name)}
            for attr in table_cls.__table__.columns:
                if attr.name not in existing:
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
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))

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
                if review_feedback is not None:
                    task.review_feedback = review_feedback
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
    ) -> None:
        async with self._session_factory() as session:
            row = PipelineRow(
                id=id, description=description, project_dir=project_dir,
                model_strategy=model_strategy, user_id=user_id,
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

    async def list_pipelines(self, user_id: str | None = None) -> list[PipelineRow]:
        async with self._session_factory() as session:
            query = select(PipelineRow)
            if user_id:
                query = query.where(PipelineRow.user_id == user_id)
            result = await session.execute(query.order_by(PipelineRow.created_at.desc()))
            return list(result.scalars().all())

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

"""Database layer. SQLAlchemy 2.0 async. SQLite default, Postgres optional."""

from sqlalchemy import String, JSON, func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
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

    async def retry_task(self, task_id: str) -> None:
        """Reset a task for retry: increment retry_count, set state to todo, unassign agent."""
        async with self._session_factory() as session:
            task = await session.get(TaskRow, task_id)
            if task:
                task.retry_count += 1
                task.state = "todo"
                task.assigned_agent = None
                await session.commit()

    async def release_agent(self, agent_id: str) -> None:
        """Set agent back to idle and clear its current task."""
        async with self._session_factory() as session:
            agent = await session.get(AgentRow, agent_id)
            if agent:
                agent.state = "idle"
                agent.current_task = None
                await session.commit()

    async def list_agents(self) -> list[AgentRow]:
        async with self._session_factory() as session:
            result = await session.execute(select(AgentRow))
            return list(result.scalars().all())

    async def get_task_counts_by_state(self) -> dict[str, int]:
        """Return a dict mapping each task state to its count using a single GROUP BY query."""
        async with self._session_factory() as session:
            stmt = select(TaskRow.state, func.count(TaskRow.id)).group_by(TaskRow.state)
            result = await session.execute(stmt)
            return {state: count for state, count in result.all()}

    # ---- Pipeline CRUD ----

    async def create_pipeline(
        self, id: str, description: str, project_dir: str,
        model_strategy: str = "auto", user_id: str | None = None,
    ) -> None:
        async with self._session_factory() as session:
            from datetime import datetime, timezone
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
                    from datetime import datetime, timezone
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

    async def list_pipelines(self, user_id: str | None = None) -> list[PipelineRow]:
        async with self._session_factory() as session:
            query = select(PipelineRow)
            if user_id:
                query = query.where(PipelineRow.user_id == user_id)
            result = await session.execute(query.order_by(PipelineRow.created_at.desc()))
            return list(result.scalars().all())

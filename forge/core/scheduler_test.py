
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

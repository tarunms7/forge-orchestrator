from forge.core.models import AgentRecord, AgentState, Complexity, TaskRecord, TaskState
from forge.core.scheduler import Scheduler


def _record(
    id: str, depends_on: list[str] | None = None, state: TaskState = TaskState.TODO
) -> TaskRecord:
    return TaskRecord(
        id=id,
        title=f"Task {id}",
        description="Desc",
        files=[f"{id}.py"],
        depends_on=depends_on or [],
        complexity=Complexity.LOW,
        state=state,
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

    def test_prioritizes_critical_path_over_leaf_work(self):
        tasks = [
            _record("root"),
            _record("leaf"),
            _record("mid", depends_on=["root"]),
            _record("tail", depends_on=["mid"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [task.id for task in ready][:2] == ["root", "leaf"]

    def test_prioritizes_task_that_unlocks_more_downstream_work(self):
        tasks = [
            _record("fanout"),
            _record("single"),
            _record("child-a", depends_on=["fanout"]),
            _record("child-b", depends_on=["fanout"]),
            _record("single-child", depends_on=["single"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [task.id for task in ready][:2] == ["fanout", "single"]


class TestErrorDependencies:
    def test_task_depending_on_error_not_ready(self):
        tasks = [
            _record("a", state=TaskState.ERROR),
            _record("b", depends_on=["a"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert ready == []

    def test_blocked_by_error_returns_affected_tasks(self):
        tasks = [
            _record("a", state=TaskState.ERROR),
            _record("b", depends_on=["a"]),
            _record("c"),
        ]
        blocked = Scheduler.blocked_by_error(tasks)
        assert [t.id for t in blocked] == ["b"]

    def test_mixed_deps_some_error_excluded_from_ready(self):
        tasks = [
            _record("a", state=TaskState.DONE),
            _record("b", state=TaskState.ERROR),
            _record("c", depends_on=["a", "b"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert "c" not in [t.id for t in ready]

    def test_blocked_by_error_mixed_deps(self):
        tasks = [
            _record("a", state=TaskState.DONE),
            _record("b", state=TaskState.ERROR),
            _record("c", depends_on=["a", "b"]),
        ]
        blocked = Scheduler.blocked_by_error(tasks)
        assert [t.id for t in blocked] == ["c"]

    def test_done_dep_not_blocked_by_error(self):
        tasks = [
            _record("a", state=TaskState.ERROR),
            _record("b", state=TaskState.DONE),
            _record("c", depends_on=["b"]),
        ]
        ready = Scheduler.ready_tasks(tasks)
        assert [t.id for t in ready] == ["c"]
        blocked = Scheduler.blocked_by_error(tasks)
        assert blocked == []


class TestDependsOnNoneSafety:
    def test_none_depends_on_is_ready(self):
        """A task with depends_on=None should be treated as having no dependencies."""
        # Use model_construct to bypass pydantic validation, simulating
        # data arriving with None (e.g. from a dict with missing key).
        task = TaskRecord.model_construct(
            id="x",
            title="Task x",
            description="Desc",
            files=["x.py"],
            depends_on=None,
            complexity=Complexity.LOW,
            state=TaskState.TODO,
        )
        ready = Scheduler.ready_tasks([task])
        assert [t.id for t in ready] == ["x"]


class TestSchedulingAnalysis:
    def test_analysis_classifies_waiting_blocked_and_human_wait(self):
        tasks = [
            _record("done", state=TaskState.DONE),
            _record("broken", state=TaskState.ERROR),
            _record("ready"),
            _record("waiting", depends_on=["done", "ready"]),
            _record("blocked", depends_on=["broken"]),
            _record("question", state=TaskState.AWAITING_INPUT),
            _record("review", state=TaskState.IN_REVIEW),
        ]

        analysis = Scheduler.analyze(tasks)

        assert analysis.ready_task_ids == ("ready",)
        assert analysis.waiting_task_ids == ("waiting",)
        assert analysis.blocked_task_ids == ("blocked",)
        assert analysis.human_wait_task_ids == ("question",)
        assert analysis.active_task_ids == ("review",)
        assert analysis.error_task_ids == ("broken",)
        assert analysis.done_task_ids == ("done",)
        assert analysis.task_insights["blocked"].reason == "Blocked by failed dependency: broken"
        assert analysis.task_insights["waiting"].reason == "Waiting on ready"

    def test_analysis_payload_includes_next_up_and_task_metadata(self):
        tasks = [
            _record("a"),
            _record("b"),
            _record("c", depends_on=["a"]),
            _record("d", depends_on=["c"]),
        ]

        analysis = Scheduler.analyze(tasks)
        payload = analysis.to_payload(dispatching_now=["a"])

        assert payload["critical_path_length"] == 3
        assert payload["dispatching_now"] == ["a"]
        assert payload["next_up"][0]["task_id"] == "a"
        assert payload["tasks"]["a"]["priority_rank"] == 1
        assert payload["tasks"]["b"]["status"] == "ready"

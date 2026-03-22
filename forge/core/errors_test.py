from forge.core.errors import (
    AgentError,
    AgentTimeoutError,
    CyclicDependencyError,
    FileConflictError,
    ForgeError,
    MergeConflictError,
    MergeError,
    ResourceExhaustedError,
    SchedulerError,
    SdkCallError,
    ValidationError,
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


def test_sdk_call_error_preserves_original():
    orig = RuntimeError("rate limit")
    err = SdkCallError("SDK failed", original_error=orig)
    assert err.original_error is orig
    assert "SDK failed" in str(err)

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

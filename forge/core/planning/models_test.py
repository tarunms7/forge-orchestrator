import pytest
from pydantic import ValidationError

from forge.core.planning.models import (
    CodebaseMap,
    CodebaseMapMeta,
    KeyModule,
    RelevantInterface,
)


def test_codebas_map_valid_minimal():
    m = CodebaseMap(
        architecture_summary="Monorepo with Python backend",
        key_modules=[],
    )
    assert m.architecture_summary == "Monorepo with Python backend"
    assert m.key_modules == []
    assert m.existing_patterns == {}
    assert m.relevant_interfaces == []
    assert m.risks == []


def test_codebas_map_valid_full():
    m = CodebaseMap(
        architecture_summary="Monorepo",
        key_modules=[
            KeyModule(
                path="src/main.py",
                purpose="Entry point",
                key_interfaces=["main()"],
                dependencies=["utils.py"],
                loc=100,
            )
        ],
        existing_patterns={"testing": "pytest"},
        relevant_interfaces=[
            RelevantInterface(
                name="Handler",
                file="src/handler.py",
                signature="async def handle(req) -> Response",
            )
        ],
        risks=["main.py is large"],
    )
    assert len(m.key_modules) == 1
    assert m.key_modules[0].path == "src/main.py"


def test_codebas_map_missing_required():
    with pytest.raises(ValidationError):
        CodebaseMap(key_modules=[])  # missing architecture_summary


def test_key_module_requires_path_and_purpose():
    with pytest.raises(ValidationError):
        KeyModule(path="", purpose="x", key_interfaces=[], dependencies=[], loc=0)
    with pytest.raises(ValidationError):
        KeyModule(path="src/main.py", purpose="", key_interfaces=[], dependencies=[], loc=0)


def test_codebase_map_meta_valid():
    meta = CodebaseMapMeta(
        created_at="2026-03-16T00:00:00Z",
        git_commit="abc123",
        git_branch="main",
    )
    assert meta.scout_model == "sonnet"
    assert meta.file_hashes == {}

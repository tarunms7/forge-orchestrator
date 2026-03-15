import pytest
from pydantic import ValidationError

from forge.core.planning.models import (
    CodebaseMap,
    CodebaseMapMeta,
    KeyModule,
    RelevantInterface,
)


def test_codebase_map_valid_minimal():
    m = CodebaseMap(
        architecture_summary="Monorepo with Python backend",
        key_modules=[],
    )
    assert m.architecture_summary == "Monorepo with Python backend"
    assert m.key_modules == []
    assert m.existing_patterns == {}
    assert m.relevant_interfaces == []
    assert m.risks == []


def test_codebase_map_valid_full():
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


def test_codebase_map_missing_required():
    with pytest.raises(ValidationError):
        CodebaseMap(key_modules=[])  # missing architecture_summary


def test_key_module_requires_path_and_purpose():
    with pytest.raises(ValidationError):
        KeyModule(path="", purpose="x", key_interfaces=[], dependencies=[], loc=0)
    with pytest.raises(ValidationError):
        KeyModule(path="src/main.py", purpose="", key_interfaces=[], dependencies=[], loc=0)


def test_codebase_map_slice_for_files():
    cmap = CodebaseMap(
        architecture_summary="Monorepo",
        existing_patterns={"testing": "pytest"},
        key_modules=[
            KeyModule(path="src/main.py", purpose="Entry point"),
            KeyModule(path="src/utils.py", purpose="Utilities"),
        ],
        relevant_interfaces=[
            RelevantInterface(name="Handler", file="src/main.py", signature="def handle()"),
            RelevantInterface(name="Helper", file="src/utils.py", signature="def help()"),
        ],
        risks=["main.py is large"],
    )

    sliced = cmap.slice_for_files(["src/main.py"])

    # matching module and interface are kept
    assert len(sliced.key_modules) == 1
    assert sliced.key_modules[0].path == "src/main.py"
    assert len(sliced.relevant_interfaces) == 1
    assert sliced.relevant_interfaces[0].file == "src/main.py"

    # non-matching entries are excluded
    assert all(m.path != "src/utils.py" for m in sliced.key_modules)
    assert all(i.file != "src/utils.py" for i in sliced.relevant_interfaces)

    # risks is always empty in the sliced result
    assert sliced.risks == []

    # architecture_summary and existing_patterns are preserved
    assert sliced.architecture_summary == "Monorepo"
    assert sliced.existing_patterns == {"testing": "pytest"}


def test_codebase_map_meta_valid():
    meta = CodebaseMapMeta(
        created_at="2026-03-16T00:00:00Z",
        git_commit="abc123",
        git_branch="main",
    )
    assert meta.scout_model == "sonnet"
    assert meta.file_hashes == {}

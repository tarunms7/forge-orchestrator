import json
from forge.registry.index import ModuleRegistry


def test_scan_python_file(tmp_path):
    source = tmp_path / "example.py"
    source.write_text(
        'def greet(name: str) -> str:\n'
        '    """Say hello."""\n'
        '    return f"Hello {name}"\n'
        '\n'
        'def _private():\n'
        '    pass\n'
    )
    registry = ModuleRegistry()
    registry.scan_file(str(source))
    entries = registry.all_entries()
    public = [e for e in entries if e.name == "greet"]
    assert len(public) == 1
    assert public[0].signature == "(name: str) -> str"
    assert public[0].docstring == "Say hello."


def test_scan_directory(tmp_path):
    (tmp_path / "a.py").write_text("def foo(): pass\n")
    (tmp_path / "b.py").write_text("def bar(): pass\n")
    (tmp_path / "not_python.txt").write_text("ignore me")
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    names = {e.name for e in registry.all_entries()}
    assert "foo" in names
    assert "bar" in names


def test_search_by_name(tmp_path):
    (tmp_path / "utils.py").write_text(
        "def calculate_total(items: list) -> float:\n"
        '    """Sum item prices."""\n'
        "    pass\n"
    )
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    results = registry.search("calculate")
    assert len(results) == 1
    assert results[0].name == "calculate_total"


def test_export_json(tmp_path):
    (tmp_path / "mod.py").write_text("def hello(): pass\n")
    registry = ModuleRegistry()
    registry.scan_directory(str(tmp_path))
    data = registry.to_json()
    parsed = json.loads(data)
    assert len(parsed) >= 1


def test_empty_registry():
    registry = ModuleRegistry()
    assert registry.all_entries() == []
    assert registry.search("anything") == []

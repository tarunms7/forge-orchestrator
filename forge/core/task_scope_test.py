"""Tests for task scope helpers."""

from forge.core.task_scope import effective_task_files, extract_explicit_file_paths


def test_extract_explicit_file_paths_finds_repo_relative_paths():
    text = "Add tests in `forge/review/pipeline_test.py` and update forge/core/models.py."

    assert extract_explicit_file_paths(text) == [
        "forge/review/pipeline_test.py",
        "forge/core/models.py",
    ]


def test_extract_explicit_file_paths_rejects_urls_and_parent_traversal():
    text = (
        "See https://example.com/forge/core/models.py and ../outside.py but update "
        "forge/core/models_test.py."
    )

    assert extract_explicit_file_paths(text) == ["forge/core/models_test.py"]


def test_effective_task_files_merges_explicit_description_paths_once():
    scope = effective_task_files(
        ["forge/core/models.py"],
        "Update forge/core/models.py and add forge/core/models_test.py.",
    )

    assert scope == ["forge/core/models.py", "forge/core/models_test.py"]

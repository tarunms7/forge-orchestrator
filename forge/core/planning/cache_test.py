import pytest
from forge.core.planning.cache import CodebaseMapCache
from forge.core.planning.models import CodebaseMap, CodebaseMapMeta


@pytest.fixture
def tmp_forge_dir(tmp_path):
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    return str(forge_dir)

@pytest.fixture
def sample_map():
    return CodebaseMap(architecture_summary="Test project", key_modules=[])


class TestCacheSaveLoad:
    def test_save_and_load(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={"a.py": "sha256:xxx"})
        loaded = cache.load()
        assert loaded is not None
        assert loaded.architecture_summary == "Test project"

    def test_load_returns_none_when_missing(self, tmp_forge_dir):
        cache = CodebaseMapCache(tmp_forge_dir)
        assert cache.load() is None

    def test_load_meta(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={"a.py": "sha256:xxx"})
        meta = cache.load_meta()
        assert meta is not None
        assert meta.git_commit == "abc123"
        assert meta.git_branch == "main"


class TestCacheInvalidation:
    def test_same_commit_is_valid(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={})
        decision = cache.check_freshness(current_commit="abc123", current_branch="main", total_files=100, changed_files=[])
        assert decision == "skip"

    def test_different_branch_triggers_full(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={})
        decision = cache.check_freshness(current_commit="def456", current_branch="feature", total_files=100, changed_files=[])
        assert decision == "full"

    def test_few_changes_triggers_incremental(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={})
        changed = [f"file{i}.py" for i in range(5)]
        decision = cache.check_freshness(current_commit="def456", current_branch="main", total_files=100, changed_files=changed)
        assert decision == "incremental"

    def test_many_changes_triggers_full(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={})
        changed = [f"file{i}.py" for i in range(25)]
        decision = cache.check_freshness(current_commit="def456", current_branch="main", total_files=100, changed_files=changed)
        assert decision == "full"

    def test_no_cache_triggers_full(self, tmp_forge_dir):
        cache = CodebaseMapCache(tmp_forge_dir)
        decision = cache.check_freshness(current_commit="abc", current_branch="main", total_files=100, changed_files=[])
        assert decision == "full"


class TestCacheCleanup:
    def test_clear_removes_files(self, tmp_forge_dir, sample_map):
        cache = CodebaseMapCache(tmp_forge_dir)
        cache.save(sample_map, git_commit="abc123", git_branch="main", file_hashes={})
        cache.clear()
        assert cache.load() is None
        assert cache.load_meta() is None

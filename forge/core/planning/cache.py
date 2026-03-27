"""Persistent CodebaseMap cache with incremental scouting support."""

from __future__ import annotations
import logging
import os
import tempfile
from datetime import datetime, timezone
from forge.core.planning.models import CodebaseMap, CodebaseMapMeta

logger = logging.getLogger("forge.planning.cache")

_MAP_FILE = "codebase_map.json"
_META_FILE = "codebase_map_meta.json"
_MAX_AGE_DAYS = 7
_INCREMENTAL_THRESHOLD = 0.20  # 20% of files changed → full re-scout


class CodebaseMapCache:
    def __init__(self, forge_dir: str) -> None:
        self._forge_dir = forge_dir
        self._map_path = os.path.join(forge_dir, _MAP_FILE)
        self._meta_path = os.path.join(forge_dir, _META_FILE)
        self._cached_meta: CodebaseMapMeta | None = None

    def save(self, codebase_map: CodebaseMap, *, git_commit: str, git_branch: str, file_hashes: dict[str, str], scout_model: str = "sonnet") -> None:
        os.makedirs(self._forge_dir, exist_ok=True)
        _atomic_write(self._map_path, codebase_map.model_dump_json(indent=2))
        meta = CodebaseMapMeta(
            created_at=datetime.now(timezone.utc).isoformat(),
            git_commit=git_commit, git_branch=git_branch,
            scout_model=scout_model, file_hashes=file_hashes,
        )
        _atomic_write(self._meta_path, meta.model_dump_json(indent=2))
        self._cached_meta = meta

    def load(self) -> CodebaseMap | None:
        if not os.path.isfile(self._map_path):
            return None
        try:
            with open(self._map_path, encoding="utf-8") as f:
                return CodebaseMap.model_validate_json(f.read())
        except Exception:
            logger.warning("Failed to load cached CodebaseMap from %s", self._map_path)
            return None

    def load_meta(self) -> CodebaseMapMeta | None:
        if self._cached_meta is not None:
            return self._cached_meta
        if not os.path.isfile(self._meta_path):
            return None
        try:
            with open(self._meta_path, encoding="utf-8") as f:
                meta = CodebaseMapMeta.model_validate_json(f.read())
            self._cached_meta = meta
            return meta
        except Exception:
            logger.warning("Failed to load cache metadata from %s", self._meta_path)
            return None

    def check_freshness(self, *, current_commit: str, current_branch: str, total_files: int, changed_files: list[str]) -> str:
        """Returns 'skip', 'incremental', or 'full'."""
        meta = self.load_meta()
        if meta is None:
            return "full"
        if meta.git_commit == current_commit:
            return "skip"
        if meta.git_branch != current_branch:
            return "full"
        try:
            created = datetime.fromisoformat(meta.created_at)
            age_days = (datetime.now(timezone.utc) - created).days
            if age_days > _MAX_AGE_DAYS:
                return "full"
        except (ValueError, TypeError):
            return "full"
        if total_files == 0:
            return "full"
        change_ratio = len(changed_files) / total_files
        if change_ratio >= _INCREMENTAL_THRESHOLD:
            return "full"
        return "incremental"

    def clear(self) -> None:
        self._cached_meta = None
        for path in (self._map_path, self._meta_path):
            try:
                os.remove(path)
            except FileNotFoundError:
                pass


def _atomic_write(path: str, content: str) -> None:
    """Write *content* to *path* atomically via a temp file + os.replace."""
    dir_name = os.path.dirname(path)
    fd, tmp_path = tempfile.mkstemp(dir=dir_name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

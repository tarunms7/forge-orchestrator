
import pytest
from pydantic import ValidationError

from forge.config.settings import ForgeSettings


def test_default_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    assert s.max_agents == 4
    assert s.cpu_threshold == 80.0
    assert s.memory_threshold_pct == 10.0
    assert s.agent_timeout_seconds == 600
    assert s.max_retries == 5
    assert s.db_url == f"sqlite+aiosqlite:///{tmp_path}/forge.db"
    assert s.data_dir == str(tmp_path)
    assert s.context_rotation_tokens == 80_000


def test_override_via_constructor():
    s = ForgeSettings(max_agents=2, cpu_threshold=90.0)
    assert s.max_agents == 2
    assert s.cpu_threshold == 90.0


def test_allowed_dirs_default_empty():
    s = ForgeSettings()
    assert s.allowed_dirs == []


def test_allowed_dirs_override():
    s = ForgeSettings(allowed_dirs=["/tmp/shared"])
    assert s.allowed_dirs == ["/tmp/shared"]


def test_postgres_url():
    s = ForgeSettings(db_url="postgresql+asyncpg://localhost/forge")
    assert "postgresql" in s.db_url


def test_negative_budget_raises():
    with pytest.raises(ValidationError, match="budget_limit_usd must be >= 0"):
        ForgeSettings(budget_limit_usd=-1.0)


def test_zero_cost_rate_raises():
    with pytest.raises(ValidationError, match="Cost rates must be > 0"):
        ForgeSettings(cost_rate_sonnet_input=0.0)


def test_cpu_threshold_negative_raises():
    with pytest.raises(ValidationError, match="cpu_threshold must be between 0 and 100"):
        ForgeSettings(cpu_threshold=-1.0)


def test_cpu_threshold_over_100_raises():
    with pytest.raises(ValidationError, match="cpu_threshold must be between 0 and 100"):
        ForgeSettings(cpu_threshold=200.0)


def test_max_agents_zero_raises():
    with pytest.raises(ValidationError, match="max_agents must be >= 1"):
        ForgeSettings(max_agents=0)


def test_agent_timeout_too_low_raises():
    with pytest.raises(ValidationError, match="agent_timeout_seconds must be >= 30"):
        ForgeSettings(agent_timeout_seconds=10)


def test_new_settings_defaults():
    s = ForgeSettings()
    assert s.pipeline_timeout_seconds == 3600
    assert s.contracts_required is False


def test_autonomy_default():
    s = ForgeSettings()
    assert s.autonomy == "balanced"


def test_question_limit_default():
    s = ForgeSettings()
    assert s.question_limit == 3


def test_question_timeout_default():
    s = ForgeSettings()
    assert s.question_timeout == 1800


def test_auto_pr_default():
    s = ForgeSettings()
    assert s.auto_pr is False


def test_autonomy_valid_values():
    for val in ("full", "balanced", "supervised"):
        s = ForgeSettings(autonomy=val)
        assert s.autonomy == val


def test_db_url_uses_centralized_path(tmp_path, monkeypatch):
    """db_url defaults to centralized forge_db_url() path."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    expected = f"sqlite+aiosqlite:///{tmp_path}/forge.db"
    assert s.db_url == expected


def test_db_url_override(tmp_path, monkeypatch):
    """db_url can still be overridden via constructor or env var."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings(db_url="postgresql+asyncpg://localhost/forge")
    assert s.db_url == "postgresql+asyncpg://localhost/forge"


def test_data_dir_default(tmp_path, monkeypatch):
    """data_dir defaults to forge_data_dir()."""
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    s = ForgeSettings()
    assert s.data_dir == str(tmp_path)


def test_data_dir_override(tmp_path, monkeypatch):
    """data_dir can be overridden."""
    custom = str(tmp_path / "custom")
    monkeypatch.delenv("FORGE_DATA_DIR", raising=False)
    s = ForgeSettings(data_dir=custom)
    assert s.data_dir == custom


def test_planning_mode_default():
    s = ForgeSettings()
    assert s.planning_mode == "auto"


def test_planning_mode_valid_values():
    for val in ("auto", "simple", "deep"):
        s = ForgeSettings(planning_mode=val)
        assert s.planning_mode == val


def test_planning_mode_invalid_raises():
    with pytest.raises(ValidationError, match="planning_mode must be"):
        ForgeSettings(planning_mode="invalid")

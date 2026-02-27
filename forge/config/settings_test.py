from forge.config.settings import ForgeSettings


def test_default_settings():
    s = ForgeSettings()
    assert s.max_agents == 4
    assert s.cpu_threshold == 80.0
    assert s.memory_threshold_pct == 10.0
    assert s.agent_timeout_seconds == 600
    assert s.max_retries == 3
    assert s.db_url == "sqlite+aiosqlite:///forge.db"
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

"""Forge configuration. All settings in one place with sensible defaults."""

from pydantic_settings import BaseSettings


class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Model routing strategy
    model_strategy: str = "auto"  # "auto", "fast", "quality"

    # Agent limits
    max_agents: int = 4
    agent_timeout_seconds: int = 600  # lowered from 1800
    context_rotation_tokens: int = 80_000
    max_retries: int = 3

    # Agent sandboxing
    allowed_dirs: list[str] = []  # Extra directories agents can access

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Polling
    scheduler_poll_interval: float = 1.0

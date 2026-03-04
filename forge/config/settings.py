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

    # Build & test verification
    build_cmd: str | None = None
    test_cmd: str | None = None

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Database
    db_url: str = "sqlite+aiosqlite:///forge.db"

    # Budget & cost tracking
    budget_limit_usd: float = 0.0  # 0 means unlimited

    # Cost rates per 1K tokens (USD)
    cost_rate_sonnet_input: float = 0.003
    cost_rate_sonnet_output: float = 0.015
    cost_rate_haiku_input: float = 0.00025
    cost_rate_haiku_output: float = 0.00125
    cost_rate_opus_input: float = 0.015
    cost_rate_opus_output: float = 0.075

    # Approval gate
    require_approval: bool = False

    # Polling
    scheduler_poll_interval: float = 1.0

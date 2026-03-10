"""Forge configuration. All settings in one place with sensible defaults."""

from pydantic import field_validator
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
    max_retries: int = 5

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

    # Auth
    auth_disabled: bool = False

    # Approval gate
    require_approval: bool = False

    # Pipeline
    pipeline_timeout_seconds: int = 3600  # 0 = unlimited
    contracts_required: bool = False

    # Conventions auto-update
    auto_update_conventions: bool = False

    # Polling
    scheduler_poll_interval: float = 1.0

    # GitHub webhook integration
    github_webhook_secret: str = ""
    github_allowed_repos: list[str] = []
    github_webhook_project_dir: str = ""

    @field_validator("budget_limit_usd")
    @classmethod
    def budget_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("budget_limit_usd must be >= 0")
        return v

    @field_validator("cost_rate_sonnet_input", "cost_rate_sonnet_output",
                     "cost_rate_haiku_input", "cost_rate_haiku_output",
                     "cost_rate_opus_input", "cost_rate_opus_output")
    @classmethod
    def cost_rates_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("Cost rates must be > 0")
        return v

    @field_validator("cpu_threshold")
    @classmethod
    def cpu_threshold_range(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("cpu_threshold must be between 0 and 100")
        return v

    @field_validator("memory_threshold_pct")
    @classmethod
    def memory_threshold_range(cls, v: float) -> float:
        if not (0 <= v <= 100):
            raise ValueError("memory_threshold_pct must be between 0 and 100")
        return v

    @field_validator("disk_threshold_gb")
    @classmethod
    def disk_threshold_non_negative(cls, v: float) -> float:
        if v < 0:
            raise ValueError("disk_threshold_gb must be >= 0")
        return v

    @field_validator("max_agents")
    @classmethod
    def max_agents_minimum(cls, v: int) -> int:
        if v < 1:
            raise ValueError("max_agents must be >= 1")
        return v

    @field_validator("agent_timeout_seconds")
    @classmethod
    def timeout_minimum(cls, v: int) -> int:
        if v < 30:
            raise ValueError("agent_timeout_seconds must be >= 30")
        return v

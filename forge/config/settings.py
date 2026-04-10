"""Forge configuration. All settings in one place with sensible defaults."""

from __future__ import annotations

from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings

ReasoningEffort = Literal["low", "medium", "high"]


class ForgeSettings(BaseSettings):
    """Global settings. Override via environment variables prefixed FORGE_."""

    model_config = {"env_prefix": "FORGE_"}

    # Model routing strategy
    model_strategy: str = "auto"  # "auto", "fast", "quality"

    # Planning mode
    planning_mode: str = "auto"  # "auto", "simple", "deep"

    # Agent limits — each agent spawns a Claude CLI subprocess consuming
    # ~300-500 MB.  Default 5 balances parallelism with memory pressure.
    # The resource monitor provides backpressure if the machine is
    # overloaded (CPU, memory, disk thresholds).  Override via FORGE_MAX_AGENTS.
    max_agents: int = 5
    agent_timeout_seconds: int = 600  # lowered from 1800
    agent_max_turns: int = 75  # Max turns per agent execution. Override via FORGE_AGENT_MAX_TURNS.
    context_rotation_tokens: int = 80_000
    max_retries: int = 5

    # Agent sandboxing
    allowed_dirs: list[str] = []  # Extra directories agents can access

    # Build, test & lint verification
    build_cmd: str | None = None
    test_cmd: str | None = None
    lint_cmd: str | None = None  # Override auto-detected linter check command
    lint_fix_cmd: str | None = None  # Override auto-detected linter fix command
    lint_timeout: int = (
        180  # Lint gate timeout (seconds). Adaptive: doubles on timeout via learning system.
    )

    # Resource thresholds
    cpu_threshold: float = 80.0
    memory_threshold_pct: float = 10.0
    disk_threshold_gb: float = 5.0

    # Central data directory
    data_dir: str = ""

    # Database
    db_url: str = ""

    # Budget & cost tracking
    budget_limit_usd: float = 0.0  # 0 means unlimited

    # Cost rates per 1K tokens (USD) — legacy fields, kept for backward compat
    cost_rate_sonnet_input: float = 0.003
    cost_rate_sonnet_output: float = 0.015
    cost_rate_haiku_input: float = 0.00025
    cost_rate_haiku_output: float = 0.00125
    cost_rate_opus_input: float = 0.015
    cost_rate_opus_output: float = 0.075

    # Multi-provider support
    openai_enabled: bool = False  # env: FORGE_OPENAI_ENABLED

    # Per-stage model overrides (env: FORGE_PLANNER_MODEL, etc.)
    planner_model: str | None = None
    agent_model_low: str | None = None
    agent_model_medium: str | None = None
    agent_model_high: str | None = None
    reviewer_model: str | None = None
    contract_builder_model: str | None = None
    ci_fix_model: str | None = None
    planner_reasoning_effort: ReasoningEffort | None = None
    agent_model_low_reasoning_effort: ReasoningEffort | None = None
    agent_model_medium_reasoning_effort: ReasoningEffort | None = None
    agent_model_high_reasoning_effort: ReasoningEffort | None = None
    reviewer_reasoning_effort: ReasoningEffort | None = None
    contract_builder_reasoning_effort: ReasoningEffort | None = None
    ci_fix_reasoning_effort: ReasoningEffort | None = None

    # New-style cost rates: dict of "provider:model" -> {input_per_1k, output_per_1k}
    cost_rates: dict[str, dict[str, float]] | None = None

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
    scheduler_poll_interval: float = 0.3

    # Human-in-the-loop settings
    autonomy: str = "balanced"  # full | balanced | supervised
    question_limit: int = 3  # max questions per task per execution cycle
    question_timeout: int = 1800  # seconds before auto-decide (30 min)
    auto_pr: bool = False  # skip final approval, auto-create PR

    # CI Auto-Fix
    ci_fix_enabled: bool = True
    ci_fix_max_retries: int = 3
    ci_fix_budget_usd: float = 0.0

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

    @field_validator(
        "cost_rate_sonnet_input",
        "cost_rate_sonnet_output",
        "cost_rate_haiku_input",
        "cost_rate_haiku_output",
        "cost_rate_opus_input",
        "cost_rate_opus_output",
    )
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

    @field_validator("model_strategy")
    @classmethod
    def model_strategy_valid(cls, v: str) -> str:
        if v not in ("auto", "fast", "quality"):
            raise ValueError("model_strategy must be 'auto', 'fast', or 'quality'")
        return v

    @field_validator("autonomy")
    @classmethod
    def autonomy_valid(cls, v: str) -> str:
        if v not in ("full", "balanced", "supervised"):
            raise ValueError("autonomy must be 'full', 'balanced', or 'supervised'")
        return v

    @field_validator("agent_max_turns")
    @classmethod
    def agent_max_turns_minimum(cls, v: int) -> int:
        if v < 1:
            raise ValueError("agent_max_turns must be >= 1")
        return v

    @field_validator("question_limit")
    @classmethod
    def question_limit_range(cls, v: int) -> int:
        if not (1 <= v <= 10):
            raise ValueError("question_limit must be between 1 and 10")
        return v

    @field_validator("question_timeout")
    @classmethod
    def question_timeout_range(cls, v: int) -> int:
        if not (60 <= v <= 7200):
            raise ValueError("question_timeout must be between 60 and 7200")
        return v

    @field_validator("planning_mode")
    @classmethod
    def planning_mode_valid(cls, v: str) -> str:
        if v not in ("auto", "simple", "deep"):
            raise ValueError("planning_mode must be 'auto', 'simple', or 'deep'")
        return v

    @model_validator(mode="after")
    def _apply_path_defaults(self) -> ForgeSettings:
        """Set data_dir and db_url from centralized paths if not overridden."""
        from forge.core.paths import forge_data_dir, forge_db_url

        if not self.data_dir:
            self.data_dir = forge_data_dir()
        if not self.db_url:
            self.db_url = forge_db_url()
        return self

    def build_routing_overrides(self) -> dict[str, str]:
        """Collect all per-stage model fields into the overrides dict format.

        Returns a dict suitable for passing as ``overrides`` to ``select_model()``.
        Only includes fields that are explicitly set (not None).
        """
        result: dict[str, str] = {}
        if self.planner_model is not None:
            result["planner_model"] = self.planner_model
        if self.agent_model_low is not None:
            result["agent_model_low"] = self.agent_model_low
        if self.agent_model_medium is not None:
            result["agent_model_medium"] = self.agent_model_medium
        if self.agent_model_high is not None:
            result["agent_model_high"] = self.agent_model_high
        if self.reviewer_model is not None:
            result["reviewer_model"] = self.reviewer_model
        if self.contract_builder_model is not None:
            result["contract_builder_model"] = self.contract_builder_model
        if self.ci_fix_model is not None:
            result["ci_fix_model"] = self.ci_fix_model
        return result

    def build_reasoning_effort_overrides(self) -> dict[str, ReasoningEffort]:
        """Collect explicit per-stage reasoning-effort overrides."""
        result: dict[str, ReasoningEffort] = {}
        if self.planner_reasoning_effort is not None:
            result["planner_reasoning_effort"] = self.planner_reasoning_effort
        if self.agent_model_low_reasoning_effort is not None:
            result["agent_model_low_reasoning_effort"] = self.agent_model_low_reasoning_effort
        if self.agent_model_medium_reasoning_effort is not None:
            result["agent_model_medium_reasoning_effort"] = self.agent_model_medium_reasoning_effort
        if self.agent_model_high_reasoning_effort is not None:
            result["agent_model_high_reasoning_effort"] = self.agent_model_high_reasoning_effort
        if self.reviewer_reasoning_effort is not None:
            result["reviewer_reasoning_effort"] = self.reviewer_reasoning_effort
        if self.contract_builder_reasoning_effort is not None:
            result["contract_builder_reasoning_effort"] = self.contract_builder_reasoning_effort
        if self.ci_fix_reasoning_effort is not None:
            result["ci_fix_reasoning_effort"] = self.ci_fix_reasoning_effort
        return result

    def resolve_reasoning_effort(
        self,
        stage: str,
        complexity: str = "medium",
    ) -> ReasoningEffort | None:
        """Return the explicit reasoning-effort override for a stage, if any."""
        if stage == "agent":
            normalized = complexity if complexity in {"low", "medium", "high"} else "medium"
            return getattr(self, f"agent_model_{normalized}_reasoning_effort")

        attr_map = {
            "planner": "planner_reasoning_effort",
            "reviewer": "reviewer_reasoning_effort",
            "contract_builder": "contract_builder_reasoning_effort",
            "ci_fix": "ci_fix_reasoning_effort",
        }
        attr_name = attr_map.get(stage)
        return getattr(self, attr_name) if attr_name else None

    def build_cost_registry_overrides(self) -> dict:
        """Convert both legacy cost_rate_* fields and new cost_rates dict.

        Returns a dict of ``"provider:model"`` -> ``ModelRates`` suitable for
        passing as ``overrides`` to ``CostRegistry()``.
        """
        from forge.core.cost_registry import ModelRates, migrate_legacy_cost_settings

        # Start with legacy fields
        result = migrate_legacy_cost_settings(
            sonnet_input=self.cost_rate_sonnet_input,
            sonnet_output=self.cost_rate_sonnet_output,
            haiku_input=self.cost_rate_haiku_input,
            haiku_output=self.cost_rate_haiku_output,
            opus_input=self.cost_rate_opus_input,
            opus_output=self.cost_rate_opus_output,
        )

        # Overlay new-style cost_rates if provided
        if self.cost_rates:
            for key, rates_dict in self.cost_rates.items():
                result[key] = ModelRates(
                    input_per_1k=rates_dict.get("input_per_1k", 0.0),
                    output_per_1k=rates_dict.get("output_per_1k", 0.0),
                )

        return result

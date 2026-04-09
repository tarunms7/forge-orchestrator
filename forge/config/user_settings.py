"""Local persisted user settings for CLI/TUI flows.

These settings are intentionally stored outside project TOML so users can tune
Forge directly from the interface without hand-editing config files.
"""

from __future__ import annotations

import json
import os
from typing import Any

from forge.core.paths import forge_data_dir

LOCAL_USER_SETTINGS_FILENAME = "user_settings.json"

USER_SETTINGS_FIELDS = {
    "max_agents",
    "timeout",
    "max_retries",
    "model_strategy",
    "planner_model",
    "agent_model_low",
    "agent_model_medium",
    "agent_model_high",
    "reviewer_model",
    "contract_builder_model",
    "ci_fix_model",
    "planner_reasoning_effort",
    "agent_model_low_reasoning_effort",
    "agent_model_medium_reasoning_effort",
    "agent_model_high_reasoning_effort",
    "reviewer_reasoning_effort",
    "contract_builder_reasoning_effort",
    "ci_fix_reasoning_effort",
    "autonomy",
    "question_limit",
    "question_timeout",
    "auto_pr",
}


def local_user_settings_path() -> str:
    """Return the global user-settings file path."""
    return os.path.join(forge_data_dir(), LOCAL_USER_SETTINGS_FILENAME)


def load_local_user_settings() -> dict[str, Any]:
    """Load local user settings from disk, returning an empty dict on failure."""
    path = local_user_settings_path()
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: value for key, value in data.items() if key in USER_SETTINGS_FIELDS}


def save_local_user_settings(settings: dict[str, Any]) -> None:
    """Persist the allowed subset of user settings atomically."""
    path = local_user_settings_path()
    payload = {key: settings[key] for key in USER_SETTINGS_FIELDS if key in settings}
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp_path, path)


def export_settings_snapshot(settings: object) -> dict[str, Any]:
    """Serialize supported settings fields from a ForgeSettings-like object."""
    snapshot: dict[str, Any] = {}
    field_map = {
        "max_agents": "max_agents",
        "timeout": "agent_timeout_seconds",
        "max_retries": "max_retries",
        "model_strategy": "model_strategy",
        "planner_model": "planner_model",
        "agent_model_low": "agent_model_low",
        "agent_model_medium": "agent_model_medium",
        "agent_model_high": "agent_model_high",
        "reviewer_model": "reviewer_model",
        "contract_builder_model": "contract_builder_model",
        "ci_fix_model": "ci_fix_model",
        "planner_reasoning_effort": "planner_reasoning_effort",
        "agent_model_low_reasoning_effort": "agent_model_low_reasoning_effort",
        "agent_model_medium_reasoning_effort": "agent_model_medium_reasoning_effort",
        "agent_model_high_reasoning_effort": "agent_model_high_reasoning_effort",
        "reviewer_reasoning_effort": "reviewer_reasoning_effort",
        "contract_builder_reasoning_effort": "contract_builder_reasoning_effort",
        "ci_fix_reasoning_effort": "ci_fix_reasoning_effort",
        "autonomy": "autonomy",
        "question_limit": "question_limit",
        "question_timeout": "question_timeout",
        "auto_pr": "auto_pr",
    }
    for key, attr_name in field_map.items():
        snapshot[key] = getattr(settings, attr_name, None)
    return snapshot

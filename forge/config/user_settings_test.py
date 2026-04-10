"""Tests for local CLI/TUI user settings persistence."""

from __future__ import annotations

import json
import os

from forge.config.settings import ForgeSettings
from forge.config.user_settings import (
    export_settings_snapshot,
    load_local_user_settings,
    local_user_settings_path,
    save_local_user_settings,
)


def test_local_user_settings_path_uses_forge_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    assert local_user_settings_path() == os.path.join(str(tmp_path), "user_settings.json")


def test_load_local_user_settings_returns_empty_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    assert load_local_user_settings() == {}


def test_save_and_load_local_user_settings_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_DATA_DIR", str(tmp_path))
    payload = {
        "planner_model": "claude:opus",
        "reviewer_model": "openai:gpt-5.4-mini",
        "reviewer_reasoning_effort": "high",
        "ignored": "value that should not survive",
    }

    save_local_user_settings(payload)

    with open(local_user_settings_path(), encoding="utf-8") as fh:
        raw = json.load(fh)
    assert "ignored" not in raw

    loaded = load_local_user_settings()
    assert loaded == {
        "planner_model": "claude:opus",
        "reviewer_model": "openai:gpt-5.4-mini",
        "reviewer_reasoning_effort": "high",
    }


def test_export_settings_snapshot_uses_supported_fields():
    settings = ForgeSettings(
        planner_model="claude:opus",
        reviewer_model="openai:gpt-5.4",
        reviewer_reasoning_effort="high",
        autonomy="balanced",
        question_limit=4,
    )

    snapshot = export_settings_snapshot(settings)

    assert snapshot["planner_model"] == "claude:opus"
    assert snapshot["reviewer_model"] == "openai:gpt-5.4"
    assert snapshot["reviewer_reasoning_effort"] == "high"
    assert snapshot["autonomy"] == "balanced"
    assert snapshot["question_limit"] == 4

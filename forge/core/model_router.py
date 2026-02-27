"""Model routing by task complexity and pipeline stage."""

# Strategy -> Stage -> Complexity -> Model
_ROUTING_TABLE: dict[str, dict[str, dict[str, str]]] = {
    "auto": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "sonnet", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "sonnet", "medium": "opus", "high": "opus"},
    },
    "fast": {
        "planner": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
        "agent": {"low": "haiku", "medium": "haiku", "high": "haiku"},
        "reviewer": {"low": "sonnet", "medium": "sonnet", "high": "sonnet"},
    },
    "quality": {
        "planner": {"low": "opus", "medium": "opus", "high": "opus"},
        "agent": {"low": "opus", "medium": "opus", "high": "opus"},
        "reviewer": {"low": "opus", "medium": "opus", "high": "opus"},
    },
}


def select_model(strategy: str, stage: str, complexity: str) -> str:
    """Select the Claude model for a given strategy, pipeline stage, and task complexity.

    Args:
        strategy: "auto", "fast", or "quality"
        stage: "planner", "agent", or "reviewer"
        complexity: "low", "medium", or "high"

    Returns:
        Model name string: "opus", "sonnet", or "haiku"
    """
    table = _ROUTING_TABLE.get(strategy, _ROUTING_TABLE["auto"])
    stage_map = table.get(stage, table["agent"])
    return stage_map.get(complexity, "sonnet")

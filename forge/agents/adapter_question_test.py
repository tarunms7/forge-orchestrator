from forge.agents.adapter import AGENT_SYSTEM_PROMPT_TEMPLATE, _build_question_protocol


def test_balanced_autonomy_protocol():
    protocol = _build_question_protocol(autonomy="balanced", remaining=3)
    assert "balanced" in protocol
    assert "3" in protocol
    assert "80% confident" in protocol
    assert "FORGE_QUESTION:" in protocol


def test_full_autonomy_no_questions():
    protocol = _build_question_protocol(autonomy="full", remaining=0)
    assert "NEVER ask questions" in protocol


def test_supervised_autonomy_always_ask():
    protocol = _build_question_protocol(autonomy="supervised", remaining=5)
    assert "ANY" in protocol or "any" in protocol


def test_protocol_included_in_system_prompt():
    # The template should contain {question_protocol} placeholder
    assert "{question_protocol}" in AGENT_SYSTEM_PROMPT_TEMPLATE

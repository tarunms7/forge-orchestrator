from forge.tui.widgets.review_gates import format_gates


def test_format_gates_all_passed():
    gates = {
        "gate0_build": {"status": "passed", "details": "OK in 1.8s"},
        "gate1_lint": {"status": "passed", "details": "Clean"},
    }
    result = format_gates(gates)
    assert "✓" in result
    assert "Build" in result


def test_format_gates_one_running():
    gates = {
        "gate0_build": {"status": "passed"},
        "gate1_5_test": {"status": "running"},
    }
    result = format_gates(gates)
    assert "running" in result.lower() or "◎" in result


def test_format_gates_one_failed():
    gates = {"gate0_build": {"status": "failed", "details": "Exit code 1"}}
    result = format_gates(gates)
    assert "✗" in result or "failed" in result.lower()


def test_format_gates_empty():
    result = format_gates({})
    assert "No review" in result or result == ""

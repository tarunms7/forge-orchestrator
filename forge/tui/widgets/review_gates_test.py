from forge.tui.widgets.review_gates import format_gates, format_streaming_output, ReviewGates


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


def test_format_streaming_output_empty():
    result = format_streaming_output([])
    assert result == ""


def test_format_streaming_output_with_lines():
    result = format_streaming_output(["Line 1", "Line 2"])
    assert "Line 1" in result
    assert "Line 2" in result


def test_format_streaming_output_with_typing_indicator():
    result = format_streaming_output(["Line 1"], streaming=True, typing_frame=0)
    assert "Line 1" in result
    assert "Typing" in result


def test_review_gates_update_streaming_output():
    widget = ReviewGates()
    widget.update_streaming_output(["Review line 1", "Review line 2"])
    assert widget._streaming_lines == ["Review line 1", "Review line 2"]


def test_review_gates_set_streaming():
    widget = ReviewGates()
    assert widget._streaming is False
    widget.set_streaming(True)
    assert widget._streaming is True
    widget.set_streaming(False)
    assert widget._streaming is False


def test_review_gates_set_streaming_idempotent():
    widget = ReviewGates()
    widget.set_streaming(True)
    widget.set_streaming(True)  # no-op
    assert widget._streaming is True


def test_review_gates_render_with_streaming():
    widget = ReviewGates()
    widget._gates = {"gate0_build": {"status": "passed"}}
    widget._streaming_lines = ["Checking files..."]
    widget._streaming = True
    result = widget.render()
    assert "Build" in result
    assert "Checking files..." in result
    assert "LLM Review Output" in result
    assert "Typing" in result


def test_review_gates_render_no_streaming_lines():
    widget = ReviewGates()
    widget._gates = {"gate0_build": {"status": "passed"}}
    widget._streaming_lines = []
    result = widget.render()
    assert "Build" in result
    assert "LLM Review Output" not in result

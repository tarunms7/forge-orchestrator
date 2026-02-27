from forge.review.auto_check import AutoCheck


def test_all_pass():
    result = AutoCheck.run_all(
        test_passed=True, lint_clean=True, build_ok=True, file_conflicts=[],
    )
    assert result.passed is True
    assert result.failures == []


def test_test_failure():
    result = AutoCheck.run_all(
        test_passed=False, lint_clean=True, build_ok=True, file_conflicts=[],
    )
    assert result.passed is False
    assert any("test" in f.lower() for f in result.failures)


def test_lint_failure():
    result = AutoCheck.run_all(
        test_passed=True, lint_clean=False, build_ok=True, file_conflicts=[],
    )
    assert result.passed is False
    assert any("lint" in f.lower() for f in result.failures)


def test_file_conflicts():
    result = AutoCheck.run_all(
        test_passed=True, lint_clean=True, build_ok=True, file_conflicts=["shared.py"],
    )
    assert result.passed is False
    assert any("conflict" in f.lower() for f in result.failures)


def test_multiple_failures_reported():
    result = AutoCheck.run_all(
        test_passed=False, lint_clean=False, build_ok=False, file_conflicts=["a.py"],
    )
    assert result.passed is False
    assert len(result.failures) == 4

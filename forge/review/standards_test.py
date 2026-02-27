from forge.review.standards import StandardsChecker, Violation


def test_function_too_long(tmp_path):
    source = tmp_path / "long.py"
    lines = ["def too_long():\n"] + [f"    x = {i}\n" for i in range(35)]
    source.write_text("".join(lines))
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "max_function_length" for v in violations)


def test_function_ok_length(tmp_path):
    source = tmp_path / "short.py"
    source.write_text("def short():\n    return 1\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert not any(v.rule == "max_function_length" for v in violations)


def test_file_too_long(tmp_path):
    source = tmp_path / "big.py"
    source.write_text("\n".join([f"x{i} = {i}" for i in range(310)]))
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "max_file_length" for v in violations)


def test_bare_except_detected(tmp_path):
    source = tmp_path / "bare.py"
    source.write_text("try:\n    pass\nexcept:\n    pass\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert any(v.rule == "no_bare_except" for v in violations)


def test_bare_except_with_type_ok(tmp_path):
    source = tmp_path / "typed.py"
    source.write_text("try:\n    pass\nexcept ValueError:\n    pass\n")
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert not any(v.rule == "no_bare_except" for v in violations)


def test_clean_file_no_violations(tmp_path):
    source = tmp_path / "clean.py"
    source.write_text(
        "def greet(name: str) -> str:\n"
        '    """Say hello."""\n'
        '    return f"Hello {name}"\n'
    )
    checker = StandardsChecker(max_function_lines=30, max_file_lines=300)
    violations = checker.check_file(str(source))
    assert violations == []

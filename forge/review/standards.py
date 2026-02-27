"""Programmatic coding standards enforcement. Part of Gate 1."""

import ast
from dataclasses import dataclass


@dataclass
class Violation:
    """A standards violation found in code."""

    rule: str
    file_path: str
    line: int
    message: str


class StandardsChecker:
    """Checks Python files against coding standards."""

    def __init__(self, max_function_lines: int = 30, max_file_lines: int = 300) -> None:
        self._max_func_lines = max_function_lines
        self._max_file_lines = max_file_lines

    def check_file(self, file_path: str) -> list[Violation]:
        try:
            with open(file_path) as f:
                source = f.read()
                lines = source.splitlines()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError):
            return []

        violations: list[Violation] = []
        violations.extend(self._check_file_length(file_path, lines))
        violations.extend(self._check_function_lengths(file_path, tree))
        violations.extend(self._check_bare_except(file_path, tree))
        return violations

    def _check_file_length(self, path: str, lines: list[str]) -> list[Violation]:
        if len(lines) > self._max_file_lines:
            return [Violation(
                rule="max_file_length",
                file_path=path,
                line=len(lines),
                message=f"File has {len(lines)} lines (max {self._max_file_lines})",
            )]
        return []

    def _check_function_lengths(self, path: str, tree: ast.AST) -> list[Violation]:
        violations: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                length = _function_length(node)
                if length > self._max_func_lines:
                    violations.append(Violation(
                        rule="max_function_length",
                        file_path=path,
                        line=node.lineno,
                        message=f"Function '{node.name}' is {length} lines (max {self._max_func_lines})",
                    ))
        return violations

    def _check_bare_except(self, path: str, tree: ast.AST) -> list[Violation]:
        violations: list[Violation] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                violations.append(Violation(
                    rule="no_bare_except",
                    file_path=path,
                    line=node.lineno,
                    message="Bare except clause (catch a specific exception)",
                ))
        return violations


def _function_length(node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    if not node.body:
        return 0
    first_line = node.body[0].lineno
    last_line = node.end_lineno or node.body[-1].lineno
    return last_line - first_line + 1

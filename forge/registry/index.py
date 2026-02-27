"""Module registry. Indexes all public functions for reuse lookup."""

import ast
import json
import os
from dataclasses import dataclass, asdict


@dataclass
class FunctionEntry:
    """A public function in the codebase."""

    name: str
    file_path: str
    line_number: int
    signature: str
    docstring: str | None


class ModuleRegistry:
    """Scans Python files and maintains a searchable index of public functions."""

    def __init__(self) -> None:
        self._entries: list[FunctionEntry] = []

    def scan_file(self, file_path: str) -> None:
        try:
            with open(file_path) as f:
                source = f.read()
            tree = ast.parse(source, filename=file_path)
        except (SyntaxError, OSError):
            return

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.FunctionDef) and not node.name.startswith("_"):
                self._entries.append(_extract_function(node, file_path))

    def scan_directory(self, directory: str) -> None:
        for root, _, files in os.walk(directory):
            for fname in files:
                if fname.endswith(".py"):
                    self.scan_file(os.path.join(root, fname))

    def all_entries(self) -> list[FunctionEntry]:
        return list(self._entries)

    def search(self, query: str) -> list[FunctionEntry]:
        query_lower = query.lower()
        return [
            e for e in self._entries
            if query_lower in e.name.lower()
            or (e.docstring and query_lower in e.docstring.lower())
        ]

    def to_json(self) -> str:
        return json.dumps([asdict(e) for e in self._entries], indent=2)


def _extract_function(node: ast.FunctionDef, file_path: str) -> FunctionEntry:
    sig = _build_signature(node)
    docstring = ast.get_docstring(node)
    return FunctionEntry(
        name=node.name,
        file_path=file_path,
        line_number=node.lineno,
        signature=sig,
        docstring=docstring,
    )


def _build_signature(node: ast.FunctionDef) -> str:
    args = ast.unparse(node.args) if node.args.args else ""
    ret = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"({args}){ret}"

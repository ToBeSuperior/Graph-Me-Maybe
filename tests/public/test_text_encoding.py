"""Text files are read and written as UTF-8 by name. Without it Windows uses its ANSI code page
(cp1252 in English), which has no Hangul: the first Korean label in a render plan stopped
`graphme xlsx-bundle` there."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "graphme"


def _unnamed_encodings(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr not in ("read_text", "write_text"):
            continue
        if not any(keyword.arg == "encoding" for keyword in node.keywords):
            found.append(f"{path.relative_to(SOURCE)}:{node.lineno} {node.func.attr}")
    return found


def test_every_text_file_names_its_encoding() -> None:
    found = [line for path in sorted(SOURCE.rglob("*.py")) for line in _unnamed_encodings(path)]

    assert found == []

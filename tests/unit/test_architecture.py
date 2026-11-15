"""Dependency rule: `app.domain` stays free of framework and service imports."""

import ast
from pathlib import Path

import app.domain

FORBIDDEN = ("sqlalchemy", "fastapi", "app.services")


def _imports(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.append(node.module)
    return names


def violations(root: Path) -> list[str]:
    return [
        f"{path.name}: {name}"
        for path in root.rglob("*.py")
        for name in _imports(path)
        if any(name == f or name.startswith(f + ".") for f in FORBIDDEN)
    ]


def test_domain_has_no_forbidden_imports() -> None:
    assert violations(Path(app.domain.__file__).parent) == []


def test_checker_detects_forbidden_import(tmp_path: Path) -> None:
    (tmp_path / "bad.py").write_text("from sqlalchemy import select\n")
    (tmp_path / "bad2.py").write_text("import app.services.rbac\n")
    assert len(violations(tmp_path)) == 2

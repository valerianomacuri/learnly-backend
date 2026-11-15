"""Dependency rule: `app.domain` stays free of framework and service imports."""

import ast
from pathlib import Path

import app.domain

FORBIDDEN = ("sqlalchemy", "fastapi", "app.services", "app.agent")


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


def test_rag_does_not_depend_on_the_web_layer() -> None:
    import app.rag

    root = Path(app.rag.__file__).parent
    web = [
        v
        for v in violations_for(
            root, ("fastapi", "app.api", "app.services", "app.agent")
        )
    ]
    assert web == []


def violations_for(root: Path, forbidden: tuple[str, ...]) -> list[str]:
    return [
        f"{path.name}: {name}"
        for path in root.rglob("*.py")
        for name in _imports(path)
        if any(name == f or name.startswith(f + ".") for f in forbidden)
    ]


def test_agent_does_not_depend_on_the_web_layer() -> None:
    """The agent uses services (like REST does) but never routes or FastAPI."""
    import app.agent

    root = Path(app.agent.__file__).parent
    assert violations_for(root, ("fastapi", "app.api")) == []

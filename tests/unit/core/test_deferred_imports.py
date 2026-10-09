from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "opengrader"


def _python_files() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _is_type_checking_test(node: ast.expr) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _bound_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    return {alias.asname or alias.name.split(".")[0] for alias in node.names}


def _type_checking_only_imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    names |= _bound_names(inner)
    return names


def _runtime_imports(tree: ast.AST) -> set[str]:
    deferred_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            deferred_nodes |= {id(inner) for inner in ast.walk(node)}

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)) and id(node) not in deferred_nodes:
            names |= _bound_names(node)
    return names


def _called_names(tree: ast.AST) -> set[str]:
    called: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        while isinstance(func, ast.Attribute):
            func = func.value
        if isinstance(func, ast.Name):
            called.add(func.id)
    return called


@pytest.mark.parametrize("path", _python_files(), ids=lambda p: str(p.name))
def test_no_type_checking_only_name_is_called_at_runtime(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    deferred = _type_checking_only_imports(tree)
    if not deferred:
        return

    unreachable = sorted((deferred & _called_names(tree)) - _runtime_imports(tree))
    assert not unreachable, (
        f"{path.relative_to(PACKAGE_ROOT.parent.parent)} calls "
        f"{unreachable} at run time, but only imports "
        "the name inside an `if TYPE_CHECKING:` block, where it never executes. "
        "Add a local import at the call site (keeping the deferral), or move "
        "the import to module scope if it is not actually heavy."
    )


@pytest.mark.parametrize(
    "module",
    [
        "opengrader.core.grading.solutions",
        "opengrader.core.grading.evaluation",
        "opengrader.core.submissions",
        "opengrader.cli.llm_eval",
    ],
)
def test_grading_and_submissions_import_in_either_order(module: str) -> None:
    # `core.submissions` reads a constant from `grading.solutions`, which in
    # turn uses a `core.submissions` helper. In one process only the first
    # import matters, so each is tried first in a process of its own.
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-600:]

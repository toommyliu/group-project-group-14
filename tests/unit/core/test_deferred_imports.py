"""A name imported only for the type checker must never be *called* at run time.

Opengrader defers its heavy imports on purpose — `cv2`, `fitz`, `numpy` and
the OCR extractor cost tens to hundreds of milliseconds each, and the modules
that need them are loaded eagerly on the TUI's startup path (see
`tests/unit/tui/test_startup_imports.py`, which guards the other half of this
discipline: that the deferral actually happened).

This file guards the failure mode the deferral keeps producing. The usual way
to keep a heavy name out of module scope while a type checker can still see it
is::

    if TYPE_CHECKING:
        from opengrader.core.ocr.extractor import process_submission_pdf

That import runs for the type checker and **never at run time** — so a call
site that was supposed to get its own local import, and didn't, raises
``NameError: name 'process_submission_pdf' is not defined`` the first time it
is reached. Nothing catches it earlier: the type checker is satisfied, and any
test that stubs the heavy module never reaches the missing name. It has now
happened twice — the six `photo_intake.py` functions referencing a module-scope
`cv2` that the 2026-08-09 lazy-import pass had removed, and
`core/submissions/processor.py` calling `process_submission_pdf` under exactly
the block above, which broke *every* OCR extraction while the whole suite
stayed green.

So the check is a static one over the real source: for every module in the
package, take the names imported inside an ``if TYPE_CHECKING`` block, and for
any of them that is **called** somewhere in the module, require that the module
also imports that name somewhere a run-time execution can reach — at module
scope, or locally inside a function. Names used only in annotations are
untouched, which is the entire point of the pattern; this only objects when the
code actually tries to *run* one of them.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[3] / "src" / "opengrader"


def _python_files() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _is_type_checking_test(node: ast.expr) -> bool:
    """Does this `if` test read as `TYPE_CHECKING` / `typing.TYPE_CHECKING`?"""
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _bound_names(node: ast.Import | ast.ImportFrom) -> set[str]:
    """The local names an import statement binds (honouring `as` aliases)."""
    return {alias.asname or alias.name.split(".")[0] for alias in node.names}


def _type_checking_only_imports(tree: ast.AST) -> set[str]:
    """Names imported inside an `if TYPE_CHECKING:` block."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_type_checking_test(node.test):
            for inner in ast.walk(node):
                if isinstance(inner, (ast.Import, ast.ImportFrom)):
                    names |= _bound_names(inner)
    return names


def _runtime_imports(tree: ast.AST) -> set[str]:
    """Names imported anywhere a run-time execution could reach.

    That means every import in the file *except* those inside an
    `if TYPE_CHECKING` block — a local import inside a function counts, since
    reaching the call site also runs the import above it.
    """
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
    """Bare names used in call position, e.g. the `f` in `f(x)`.

    Attribute calls (`mod.f(x)`) are covered too, via the root of the
    attribute chain, because that root is the imported name.
    """
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

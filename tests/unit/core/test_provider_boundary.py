from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "opengrader"

BELOW_THE_BOUNDARY = (
    SRC / "core" / "llm",
    SRC / "core" / "ocr",
    SRC / "core" / "app_config",
)

PROVIDER_NAMES = frozenset(
    {
        "OllamaConfig",
        "OllamaError",
        "OllamaVisionConfig",
        "OllamaVisionError",
        "OllamaVisionReader",
        "DialectConfig",
        "DialectVisionReader",
        "get_dialect",
    }
)

PROVIDER_MODULES = frozenset(
    {
        "opengrader.core.llm.ollama_client",
        "opengrader.core.llm.ollama_transport",
        "opengrader.core.ocr.ollama_vlm",
        "opengrader.core.llm.dialects",
        "ollama_client",
        "ollama_transport",
        "ollama_vlm",
    }
)

# Generic names exported by core.llm still select Ollama's client.
OLLAMA_REEXPORTS = frozenset({"generate", "list_models", "ensure_model_available"})
LLM_PACKAGE = "opengrader.core.llm"


def _python_files_above_the_boundary() -> list[Path]:
    files: list[Path] = []
    for path in SRC.rglob("*.py"):
        if any(
            path == allowed or allowed in path.parents
            for allowed in BELOW_THE_BOUNDARY
        ):
            continue
        files.append(path)
    return sorted(files)


def _provider_mentions(path: Path) -> list[tuple[str, int]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []

    def note(node: ast.AST, line: int) -> None:
        for inner in ast.walk(node):
            if isinstance(inner, ast.Name) and inner.id in PROVIDER_NAMES:
                found.append((inner.id, getattr(inner, "lineno", line)))
            elif (
                isinstance(inner, ast.Attribute)
                and inner.attr in PROVIDER_NAMES
            ):
                found.append((inner.attr, getattr(inner, "lineno", line)))
            elif isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                try:
                    parsed = ast.parse(inner.value, mode="eval")
                except SyntaxError:
                    continue
                for deep in ast.walk(parsed):
                    if isinstance(deep, ast.Name) and deep.id in PROVIDER_NAMES:
                        found.append((deep.id, getattr(inner, "lineno", line)))

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "") in PROVIDER_MODULES:
                found.append((node.module or "", node.lineno))
            for alias in node.names:
                if alias.name in PROVIDER_NAMES | PROVIDER_MODULES:
                    found.append((alias.name, node.lineno))
                elif node.module == LLM_PACKAGE and alias.name in OLLAMA_REEXPORTS:
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in PROVIDER_MODULES:
                    found.append((alias.name, node.lineno))
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            note(node.annotation, node.lineno)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.returns is not None:
                note(node.returns, node.lineno)
            args = node.args
            for arg in (
                *args.posonlyargs,
                *args.args,
                *args.kwonlyargs,
                args.vararg,
                args.kwarg,
            ):
                if arg is not None and arg.annotation is not None:
                    note(arg.annotation, arg.lineno)
        elif isinstance(node, ast.Call):
            note(node.func, node.lineno)

    return sorted(set(found), key=lambda pair: pair[1])


@pytest.mark.parametrize(
    "path", _python_files_above_the_boundary(), ids=lambda p: str(p.name)
)
def test_no_module_above_the_boundary_names_a_provider_or_dialect(
    path: Path,
) -> None:
    mentions = _provider_mentions(path)

    assert not mentions, (
        f"{path.relative_to(SRC.parent.parent)} names a provider or dialect "
        f"symbol at line(s) {', '.join(str(line) for _n, line in mentions)}. "
        "Use app_config.text_generate(...) for grading text and "
        "app_config.vision_reader(resolved) for transcription."
    )


def test_the_boundary_check_can_actually_see_a_violation(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from opengrader.core.llm import OllamaConfig\n"
        "cfg = OllamaConfig(model='x')\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(offender)})
    assert lines == [1, 2]


def test_an_annotation_alone_is_a_violation(tmp_path: Path) -> None:
    annotated = tmp_path / "annotated.py"
    annotated.write_text(
        "def read(config: 'OllamaConfig | None' = None) -> None: ...\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(annotated)})
    assert lines == [1]


def test_the_vision_check_sees_an_annotation_not_just_a_call(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig\n"
        "def read(config: OllamaVisionConfig | None = None) -> None: ...\n"
        "def quoted(config: 'OllamaVisionConfig | None' = None) -> None: ...\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(offender)})
    assert lines == [1, 2, 3]  # the import, the bare annotation, the quoted one


def test_naming_a_dialect_is_also_a_violation(tmp_path: Path) -> None:
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from opengrader.core.llm.dialects import get_dialect\n"
        "dialect = get_dialect('ollama')\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(offender)})
    assert lines == [1, 2]


def test_an_ollama_function_under_a_generic_name_is_a_violation(
    tmp_path: Path,
) -> None:
    """`from opengrader.core.llm import list_models` is Ollama's list_models."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from opengrader.core.llm import LLMError, list_models\n"
        "from opengrader.core.llm import generate as _generate\n"
        "from opengrader.core.llm import parse_json_object\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(offender)})
    assert lines == [1, 2]  # the error type and the JSON helper are shared


def test_naming_the_protocol_is_not_a_violation(tmp_path: Path) -> None:
    """The replacement must not trip the rule it exists to satisfy."""
    clean = tmp_path / "clean.py"
    clean.write_text(
        "from opengrader.core.ocr.vision_reader import VisionReader\n"
        "def read(vision_reader: 'VisionReader | None' = None) -> None: ...\n",
        encoding="utf-8",
    )

    assert _provider_mentions(clean) == []

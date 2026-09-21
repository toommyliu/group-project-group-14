"""The provider boundary, enforced as a test rather than as a convention.

§7.3 of About/AI_README.md states the whole layering rule in one sentence:
*nothing above the provider boundary may name a provider or a dialect.*

Finding F1 was that this was untrue for text — nine sites reached past
`ai_config` and constructed an Ollama client config by hand, so the registry
decided only which *model string to type in*, never *which client to build*.
Phase 1 closed the construction half of that, but left the rule at
construction-only: a handful of sites still read `OllamaConfig.model` /
`.timeout_seconds` / `.host` for an argparse default or a placeholder string,
because nothing else existed yet for a default to come from — the dialect
layer that lets a text consumer stop naming Ollama at all was still design,
not code.

Finding F2 was the vision half, and it was coupled by *type*, not merely by
transport: six core modules imported `ollama_vlm`'s functions directly and
named `OllamaVisionConfig` in their own annotations, so swapping the
transport underneath changed nothing for a caller whose signature said
Ollama. Phase 2 closed that by extracting `core/ocr/vision_reader.VisionReader`,
and it closed it at *full* strength — naming forbidden, not just
constructing — because the annotation **was** the coupling F2 described.
That left this file with two rules of deliberately different strengths, and
said so, because tightening the text half then would have demanded work
Phase 2 was not the phase for.

**Phase 3 is what that sentence was waiting for.** The dialects landed
(`core/llm/dialects/`), every remaining read of an Ollama constant became an
`ai_config.DEFAULT_*` read, and `ai_config.text_generate(resolved)` replaced
the last closures that built a `generate()` call over a named client. There
is now **one rule, at full strength, for both paths** — naming a provider or
a dialect above the boundary is a violation whether or not anything gets
constructed, and whether the name shows up in a call, a bare annotation, or a
quoted forward reference. The two-tier history above is kept because it is
the reason the code below looks the way it does; the code itself no longer
has a weak half.

This file exists so none of the above can quietly reopen. A grep in a design
document is a promise; a grep in a test is a constraint — without it the next
person to add an AI-capable CLI will do what the original seven (then eight;
F1's own list undercounted by one) did, because reading a constant off the
concrete client is the obvious thing to do and nothing else says otherwise.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "opengrader"

# The three modules allowed to name a concrete client or dialect: the two that
# define the clients, and the seam (ai_config) that builds them on everyone
# else's behalf. A manifest-driven provider lives here too (core/ai_config),
# which is exactly why it is exempt rather than an oversight.
BELOW_THE_BOUNDARY = (
    SRC / "core" / "llm",
    SRC / "core" / "ocr",
    SRC / "core" / "ai_config",
)

# *Naming* any of these above the boundary is forbidden — a construction call,
# a bare reference, or an annotation (real or a quoted string) all count
# equally as of Phase 3. Ollama's two client configs and two error types, the
# vision reader that wraps them, and the dialect layer one rung below
# providers: §7.3's rule is "a provider **or a dialect**", and a consumer that
# picks a dialect by name has made F1's mistake one level down.
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

# …and so does importing any of these modules by any spelling.
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

# `opengrader.core.llm` re-exports Ollama's client functions under generic
# names, so `from opengrader.core.llm import list_models` names Ollama without
# spelling it. The AI Setup screen's connection test did exactly that until
# Phase 4: it listed models with Ollama's client whatever provider was
# chosen, which for a manifest provider meant a GET to `<host>/api/tags` on
# someone else's service. `ai_config.available_models`, `text_generate` and
# `measure_generate_latency` are the seam for these three.
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
    """Every mention of a provider or dialect symbol in a file, anywhere.

    Deliberately not construction-only: F2 was that a consumer naming
    `OllamaVisionConfig` in its own signature pins the vision path to one
    provider no matter what the transport does underneath, and Phase 3 is
    exactly the finding that this was equally true of the text path — it had
    just not been fixed yet. So a call, a bare annotation and a quoted
    forward-reference annotation all count.

    String annotations are parsed rather than pattern-matched: several of
    these modules write `"OllamaConfig | None"` as a quoted forward
    reference, and a checker that only walked real AST nodes would wave those
    straight through while reporting itself clean.
    """
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
        "Above the provider boundary the seam is ai_config.text_generate(...) "
        "for grading text and core.ocr.vision_reader.VisionReader (built with "
        "ai_config.vision_reader(resolved)) for transcription — never a named "
        "client or dialect. See §7.3 of About/AI_README.md — a consumer that "
        "names a provider or a dialect in its own signature is a consumer no "
        "other provider can ever satisfy."
    )


def test_the_boundary_check_can_actually_see_a_violation(tmp_path: Path) -> None:
    """Guard the guard.

    A test that scans files and asserts a list is empty passes just as happily
    when its scanner is broken. This pins that the detector fires on a plain
    construction call — the case F1 was about.
    """
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from opengrader.core.llm import OllamaConfig\n"
        "cfg = OllamaConfig(model='x')\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(offender)})
    assert lines == [1, 2]


def test_an_annotation_alone_is_a_violation(tmp_path: Path) -> None:
    """Phase 3 retired the construction-only rule for text.

    Before Phase 3, an annotation naming `OllamaConfig` was legal above the
    boundary: text stayed at the weaker, construction-only rule Phase 1 left
    it at, because the dialects that would let a caller stop naming Ollama
    for a default value did not exist yet. Now that they have, this is F2's
    finding applied to the path it had not yet been applied to — naming the
    type at all is the coupling, whether or not anything gets constructed.
    """
    annotated = tmp_path / "annotated.py"
    annotated.write_text(
        "def read(config: 'OllamaConfig | None' = None) -> None: ...\n",
        encoding="utf-8",
    )

    lines = sorted({line for _name, line in _provider_mentions(annotated)})
    assert lines == [1]


def test_the_vision_check_sees_an_annotation_not_just_a_call(tmp_path: Path) -> None:
    """Guard the guard, again — and specifically at the case F2 was about.

    The Phase 1 checker passed happily on a file full of `OllamaVisionConfig`
    annotations, because annotations were legal then. This pins that today's
    checker does not, in all three spellings a real module used.
    """
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
    """§7.3's rule names a dialect in the same breath as a provider.

    A consumer that imports `core.llm.dialects` and picks `get_dialect("ollama")`
    by name has made exactly F1's mistake one layer down — the registry is
    supposed to decide that, not the caller.
    """
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

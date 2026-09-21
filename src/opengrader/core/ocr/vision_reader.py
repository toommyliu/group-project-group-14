"""The seam a vision provider plugs into.

`core/grading/llm_grader.py` already takes an injected `GenerateFn`: it never
imports a client, never names Ollama, and owns its own JSON schema. That is
the seam that works, and this module is the same idea for transcription.

Before it, six modules imported `ollama_vlm`'s functions directly and threaded
`OllamaVisionConfig` through their own signatures as a *declared parameter
type* — so swapping what the transport did underneath changed nothing for a
caller that named the Ollama type in its own annotation. A provider that must
be added by editing a consumer's type hints is not a provider anyone can
choose.

Three things live here:

* `VisionReader`, the protocol every consumer above the provider boundary
  names instead of a client. It carries exactly the operations those six
  modules actually call — no more, so a new provider can implement it without
  archaeology, and no fewer, so no caller needs a back door.
* `OllamaVisionReader`, a deliberately thin wrapper over today's `ollama_vlm`
  functions. Thin on purpose: the existing tests that build an
  `OllamaVisionConfig` and call those functions directly keep passing
  untouched, which is what makes this refactor safe to land in one commit.
* `DialectVisionReader`, the Phase 3 counterpart over `core/llm/dialect_client`
  — one reader for every manifest-declared provider, because the wire shape a
  hosted vision API wants (a `messages` array with base64 image content
  blocks and an auth header) is not Ollama's flat `images: []` field, and that
  difference is exactly what the dialect layer in `core/llm/dialects/` exists
  to absorb. Both readers share the same prompts, schemas and parsers from
  `vision_prompts.py`, so a hosted provider reads a page exactly as strictly
  as the local one does — only the transport underneath differs.

Every method resolves its `ollama_vlm` function through the *module* at call
time rather than importing it at class-definition time. That is load-bearing,
not style: the extraction tests monkeypatch `ollama_vlm.extract_field_text`
and friends, and a name bound at import time would step straight past the
patch.

The reader is a frozen dataclass because `ExtractionJob` carries one across a
`spawn`ed process boundary in `core/submissions/batch.py`. Any future
implementation has the same obligation: **a VisionReader must be picklable.**

Note the operation list deviates by one from §7.4 of About/AI_README.md, which
named `extract_student_field` and predates `extract_handwriting_only`. The
former is an alias of `extract_field_text` with no caller anywhere in the
tree; the latter is called by `processor.py` and arrived with the blank
template ink-isolation work. The protocol follows the call sites, not the
document.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.llm.dialect_client import DialectConfig

    from .ollama_vlm import OllamaVisionConfig

__all__ = [
    "FieldKind",
    "TitleMetadataResult",
    "VisionReader",
    "OllamaVisionReader",
    "DialectVisionReader",
]

FieldKind = Literal["name", "sid"]


@dataclass(frozen=True)
class TitleMetadataResult:
    """What a reader made of a scanned assignment title.

    Lives here rather than in `ollama_vlm` because a return type named by the
    protocol cannot belong to one provider. `ollama_vlm` re-exports it, so the
    older import path still resolves.
    """

    course_code: str | None
    assignment_title: str
    assignment_version: str | None
    raw_title: str


@runtime_checkable
class VisionReader(Protocol):
    """Read handwriting and printed text off an image, however that happens.

    Keyword-only throughout, mirroring the `ollama_vlm` functions these wrap,
    so a call site reads the same before and after the seam went in.

    An implementation may raise anything on a wire failure; every caller in
    the tree already treats a read as best-effort and falls through to
    Tesseract, which is the invariant worth keeping (a model that reads
    nothing leaves the OCR text standing).
    """

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        """Read a student name or nine-digit ID off an identity crop."""
        ...

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        """Read course, assignment and version off a title crop."""
        ...

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        """Split a response crop into (printed prompt, handwritten answer)."""
        ...

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        """Transcribe a crop that already had the printed template ink removed."""
        ...

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        """Read the page number printed at the foot of a scanned page."""
        ...

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        """Is this the cover page — does it carry a Name or Student ID field?"""
        ...


@dataclass(frozen=True)
class OllamaVisionReader:
    """`VisionReader` over the local Ollama server.

    Holds the client config rather than subclassing or replacing it, so
    `ai_config.vision_config()` stays the one place an `OllamaVisionConfig`
    is built.
    """

    config: OllamaVisionConfig

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        from . import ollama_vlm

        return ollama_vlm.extract_field_text(
            image_path=image_path, field_kind=field_kind, config=self.config
        )

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        from . import ollama_vlm

        return ollama_vlm.extract_title_metadata(
            image_path=image_path, config=self.config
        )

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        from . import ollama_vlm

        return ollama_vlm.extract_question_prompt_and_answer(
            image_path=image_path, config=self.config, math_latex=math_latex
        )

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        from . import ollama_vlm

        return ollama_vlm.extract_handwriting_only(
            image_path=image_path, config=self.config, math_latex=math_latex
        )

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        from . import ollama_vlm

        return ollama_vlm.extract_page_number(
            image_path=image_path, config=self.config
        )

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        from . import ollama_vlm

        return ollama_vlm.page_has_identity_field(
            image_path=image_path, config=self.config
        )


@dataclass(frozen=True)
class DialectVisionReader:
    """`VisionReader` over a manifest-declared provider, via `dialect_client`.

    `OllamaVisionReader` above wraps a client shaped for exactly one service;
    this is the Phase 3 counterpart for the other two shapes §7.2 ships —
    `openai-chat` and `anthropic-messages` — and for anything a manifest
    declares against one of them. The reason it cannot just *be*
    `OllamaVisionReader` with a different config is the request shape itself:
    Ollama takes a flat `images: []` field alongside a plain `prompt` string,
    while a hosted vision API wants the image folded into a `messages` array
    as a base64 content block, sent with an `Authorization` or `x-api-key`
    header `OllamaVisionConfig` has no field for. `core/llm/dialects/` is what
    absorbs exactly that difference, and `dialect_client.generate` is where
    this reader hands a prompt, an image and a config to it — this class
    never builds a request body itself.

    The prompts, JSON schemas, token budgets and reply parsing are the same
    ones `OllamaVisionReader` uses, imported from `vision_prompts.py` rather
    than reimplemented: a hosted model should be asked to read a page exactly
    as strictly as the local one is, and a second copy of that wording is a
    second place for the two to quietly drift apart.

    Holds a `DialectConfig` rather than a client for the same reason
    `OllamaVisionReader` holds an `OllamaVisionConfig`: `ai_config` stays the
    one place that builds one, and this dataclass must stay picklable, because
    `core/submissions/batch.py` carries a `VisionReader` into a `spawn`ed
    worker process. `DialectConfig` is already a frozen dataclass of strings
    and numbers for exactly that reason (see its own docstring), so nothing
    extra is required here to keep the promise.
    """

    config: DialectConfig

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.field_text_request(field_kind)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_field_text(raw, field_kind=field_kind)

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.title_metadata_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_title_metadata(raw)

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.question_prompt_and_answer_request(math_latex=math_latex)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_question_prompt_and_answer(raw)

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.handwriting_only_request(math_latex=math_latex)
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_handwriting_only(raw)

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.page_number_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_page_number(raw)

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        from opengrader.core.llm import dialect_client

        from . import vision_prompts

        request = vision_prompts.identity_field_request()
        raw = dialect_client.generate(
            config=self.config,
            prompt=request.prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_format=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_identity_field(raw)

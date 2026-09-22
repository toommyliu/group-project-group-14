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
    course_code: str | None
    assignment_title: str
    assignment_version: str | None
    raw_title: str


@runtime_checkable
class VisionReader(Protocol):

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

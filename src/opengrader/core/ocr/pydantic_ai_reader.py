"""The `VisionReader` implementation over the optional Pydantic AI backend.

Structurally this is `DialectVisionReader` (`vision_reader.py`) with
`opengrader.core.llm.dialect_client` swapped for
`opengrader.core.llm.pydantic_ai_client`: the same six operations, built from
the same `vision_prompts` request objects, parsed by the same
`vision_prompts.parse_*` functions. Nothing about the *prompts* changes
between backends — only how a reply gets fetched — so no prompt text is
duplicated here; every request is built once, in `vision_prompts.py`, for
every reader to share.

Picklable like `DialectVisionReader`, for the same reason: `batch.py` carries
a `VisionReader` into a `spawn`ed worker, so this dataclass holds only its
config (see `core/llm/pydantic_ai_client.PydanticAIConfig`) and resolves
`pydantic_ai_client` through the module at call time, never binding it at
class-definition time — the extraction tests monkeypatch functions on the
module object, and an import-time binding would walk straight past that.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .vision_reader import FieldKind, TitleMetadataResult

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.llm.pydantic_ai_client import PydanticAIConfig

__all__ = ["PydanticAIVisionReader"]


@dataclass(frozen=True)
class PydanticAIVisionReader:

    config: "PydanticAIConfig"

    def extract_field_text(
        self, *, image_path: str | Path, field_kind: FieldKind
    ) -> str:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.field_text_request(field_kind)
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_field_text(raw, field_kind=field_kind)

    def extract_title_metadata(self, *, image_path: str | Path) -> TitleMetadataResult:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.title_metadata_request()
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_title_metadata(raw)

    def extract_question_prompt_and_answer(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> tuple[str, str]:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.question_prompt_and_answer_request(math_latex=math_latex)
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_question_prompt_and_answer(raw)

    def extract_handwriting_only(
        self, *, image_path: str | Path, math_latex: bool = False
    ) -> str:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.handwriting_only_request(math_latex=math_latex)
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_handwriting_only(raw)

    def extract_page_number(self, *, image_path: str | Path) -> int | None:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.page_number_request()
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_page_number(raw)

    def page_has_identity_field(self, *, image_path: str | Path) -> bool:
        from opengrader.core.llm import pydantic_ai_client

        from . import vision_prompts

        request = vision_prompts.identity_field_request()
        raw = pydantic_ai_client.generate(
            config=self.config,
            prompt=request.prompt,
            system_prompt=request.system_prompt,
            images=[vision_prompts.image_to_base64(image_path)],
            response_schema=request.response_schema,
            num_predict=request.num_predict,
        )
        return vision_prompts.parse_identity_field(raw)

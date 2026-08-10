# Lazy re-exports (PEP 562): ``.extractor`` imports cv2 and fitz, and
# ``.ollama_vlm``/``.layout`` pull in their own moderate-cost chains, all at
# module scope. Python always runs a package's ``__init__`` before any of its
# submodules, so an eager ``from .extractor import ...`` here would tax every
# import of a sibling submodule too — including ``opengrader.core.ocr.constants``,
# which ``core/submissions/student_file.py`` imports for the dependency-free
# ``SUBSCRIPT_TRANSLATION`` table and has no other reason to load cv2/fitz for.
# Names are resolved to their real submodule only on first access.
from typing import Any

__all__ = [
    "OcrConfig",
    "OcrResult",
    "FieldExtractionResult",
    "QuestionExtractionResult",
    "SubmissionResult",
    "process_submission_pdf",
    "results_to_dict",
    "render_page_to_image",
    "crop_region",
    "DetectedFieldRegion",
    "DetectedQuestionRegion",
    "DetectedTitleRegion",
    "LayoutPrimitive",
    "PageTextLine",
    "PageTextWord",
    "SubmissionLayout",
    "scan_submission_layout",
    "OllamaVisionConfig",
    "OllamaVisionError",
    "ensure_model_available",
    "extract_field_text",
    "extract_student_field",
    "extract_title_metadata",
    "extract_question_prompt_and_answer",
    "TitleMetadataResult",
]

_EXTRACTOR_NAMES = {
    "OcrConfig",
    "OcrResult",
    "FieldExtractionResult",
    "QuestionExtractionResult",
    "SubmissionResult",
    "process_submission_pdf",
    "results_to_dict",
    "render_page_to_image",
    "crop_region",
}
_LAYOUT_NAMES = {
    "DetectedFieldRegion",
    "DetectedQuestionRegion",
    "DetectedTitleRegion",
    "LayoutPrimitive",
    "PageTextLine",
    "PageTextWord",
    "SubmissionLayout",
    "scan_submission_layout",
}
_OLLAMA_VLM_NAMES = {
    "OllamaVisionConfig",
    "OllamaVisionError",
    "ensure_model_available",
    "extract_field_text",
    "extract_student_field",
    "extract_title_metadata",
    "extract_question_prompt_and_answer",
    "TitleMetadataResult",
}


def __getattr__(name: str) -> Any:
    if name in _EXTRACTOR_NAMES:
        from . import extractor

        return getattr(extractor, name)
    if name in _LAYOUT_NAMES:
        from . import layout

        return getattr(layout, name)
    if name in _OLLAMA_VLM_NAMES:
        from . import ollama_vlm

        return getattr(ollama_vlm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)

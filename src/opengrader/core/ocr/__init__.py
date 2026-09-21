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
    "extract_handwriting_only",
    "extract_student_field",
    "extract_title_metadata",
    "extract_question_prompt_and_answer",
    "TitleMetadataResult",
    "VisionReader",
    "OllamaVisionReader",
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
    "extract_handwriting_only",
    "extract_student_field",
    "extract_title_metadata",
    "extract_question_prompt_and_answer",
    "TitleMetadataResult",
}


_VISION_READER_NAMES = {
    "VisionReader",
    "OllamaVisionReader",
}


def __getattr__(name: str) -> Any:
    if name in _EXTRACTOR_NAMES:
        from . import extractor

        return getattr(extractor, name)
    if name in _LAYOUT_NAMES:
        from . import layout

        return getattr(layout, name)
    if name in _VISION_READER_NAMES:
        from . import vision_reader

        return getattr(vision_reader, name)
    if name in _OLLAMA_VLM_NAMES:
        from . import ollama_vlm

        return getattr(ollama_vlm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(__all__)

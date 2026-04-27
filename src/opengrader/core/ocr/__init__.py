from .alignment import preprocess_for_ocr
from .extractor import (
    OcrConfig,
    OcrResult,
    FieldExtractionResult,
    QuestionExtractionResult,
    SubmissionResult,
    process_submission_pdf,
    results_to_dict,
    render_page_to_image,
    crop_region,
)
from .scanner import (
    DetectedRegion,
    scan_template,
)
from .layout import (
    DetectedFieldRegion,
    DetectedQuestionRegion,
    DetectedTitleRegion,
    LayoutPrimitive,
    PageTextLine,
    PageTextWord,
    SubmissionLayout,
    scan_submission_layout,
)
from .ollama_vlm import (
    OllamaVisionConfig,
    OllamaVisionError,
    ensure_model_available,
    extract_field_text,
    extract_student_field,
    extract_title_metadata,
    extract_question_prompt_and_answer,
    TitleMetadataResult,
)

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
    ocr_region,
)
from .scanner import (
    DetectedRegion,
    scan_template,
)
from .ollama_vlm import (
    OllamaVisionConfig,
    OllamaVisionError,
    extract_field_text,
    extract_question_prompt_and_answer,
)

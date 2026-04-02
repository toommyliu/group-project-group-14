from .spec import (
    SpecError,
    load_spec,
    validate_spec,
    page_by_number,
    all_questions,
    all_fields,
    all_anchors,
    version_anchor_map,
    lookup_version_by_anchors,
)
from .alignment import preprocess_for_ocr, align_image, AnchorRegion, detect_anchor_presence
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
    detect_version_from_anchors,
)
from .scanner import (
    DetectedRegion,
    scan_template,
    regions_to_spec_draft,
)

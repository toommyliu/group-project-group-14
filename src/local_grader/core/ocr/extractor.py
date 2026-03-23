import re
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

import fitz
import numpy as np
import pytesseract
from PIL import Image

from .alignment import AnchorRegion, detect_anchor_presence, preprocess_for_ocr
from .spec import lookup_version_by_anchors, version_anchor_map

ConfidenceThreshold = float

DEFAULT_CONFIDENCE_THRESHOLD = 60.0
MIN_TEXT_LENGTH = 1
MAX_TEXT_LENGTH = 1000 # ?

@dataclass
class OcrResult:
    text: str
    confidence: float
    bbox_px: tuple[int, int, int, int]

@dataclass
class FieldExtractionResult:
    field_id: str
    ocr_result: OcrResult | None
    expected: str | None = None
    validated: bool = False
    error: str | None = None

@dataclass
class QuestionExtractionResult:
    question_id: str
    page_number: int
    output_path: str | None = None
    skipped: bool = False
    error: str | None = None

@dataclass
class SubmissionResult:
    filename: str
    version_id: str | None = None
    version_confidence: float | None = None
    version_validated: bool = False
    version_error: str | None = None
    fields: list[FieldExtractionResult] = field(default_factory=list)
    questions: list[QuestionExtractionResult] = field(default_factory=list)
    needs_review: bool = False
    review_reasons: list[str] = field(default_factory=list)

@dataclass
class OcrConfig:
    lang: str = "eng"
    psm: int = 6
    whitelist: str | None = None
    deskew: bool = True
    confidence_threshold: ConfidenceThreshold = DEFAULT_CONFIDENCE_THRESHOLD
    text_cleanup_fn: Callable[[str], str] | None = None

def _override_config(base: OcrConfig, field: dict) -> OcrConfig:
    return OcrConfig(
        lang=field.get("lang", base.lang),
        psm=field.get("psm", base.psm),
        whitelist=field.get("whitelist", base.whitelist),
        deskew=field.get("deskew", base.deskew),
        confidence_threshold=field.get(
            "confidence_threshold", base.confidence_threshold
        ),
        text_cleanup_fn=base.text_cleanup_fn,
    )

def _normalize_text(raw: str) -> str:
    return re.sub(r"\s+", " ", raw).strip()

def _short_text_from_result(text: str, field: dict) -> str:
    ftype = field.get("type", "text")
    if ftype == "sid":
        digits = re.findall(r"\d", text)
        return "".join(digits)
    if ftype == "version_marker":
        letters = re.findall(r"[A-Za-z]", text.upper())
        expected = str(field.get("expected", "")).upper()
        if expected and expected in text.upper():
            return expected
        if expected and letters:
            expected_char = expected[0]
            for ch in letters:
                if ch == expected_char:
                    return expected_char
            # TODO: display the original text and the predicted text?
            ambiguous_map = {
                "B": {"B", "8"},
                "A": {"A", "4"},
                "C": {"C", "G", "0"},
                "D": {"D", "0"},
                "E": {"E", "F"},
            }
            candidate_set = ambiguous_map.get(expected_char, {expected_char})
            for ch in text.upper():
                if ch in candidate_set:
                    return expected_char
        if letters:
            return letters[0]
    return text

def _is_empty(text: str) -> bool:
    return len(text.strip()) < MIN_TEXT_LENGTH

def _bbox_norm_to_px(
    bbox_norm: list[float], page_w: int, page_h: int
) -> tuple[int, int, int, int]:
    x, y, w, h = bbox_norm
    x0 = int(x * page_w)
    y0 = int(y * page_h)
    x1 = int((x + w) * page_w)
    y1 = int((y + h) * page_h)
    return x0, y0, x1, y1

def render_page_to_image(
    doc: fitz.Document, page_idx: int, dpi: int = 300
) -> tuple[Image.Image, int, int]:
    page = doc[page_idx]
    zoom = dpi / 72.0
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    buf = pix.tobytes("png")
    img = Image.open(io.BytesIO(buf))
    w, h = img.size
    return img, w, h

def crop_region(image: Image.Image, bbox_px: tuple[int, int, int, int]) -> Image.Image:
    x0, y0, x1, y1 = bbox_px
    return image.crop((x0, y0, x1, y1))

def ocr_region(
    image: Image.Image,
    config: OcrConfig,
) -> OcrResult:
    processed = preprocess_for_ocr(image, deskew=config.deskew)
    lang = config.lang
    custom_config = f"--psm {config.psm}"

    # Applying a whitelist could improve accuracy for fields with a
    # well known character set
    if config.whitelist:
        custom_config += f" -c tessedit_char_whitelist={config.whitelist}"
    data = pytesseract.image_to_data(
        processed, lang=lang, config=custom_config, output_type=pytesseract.Output.DICT
    )
    words: list[tuple[str, float, tuple[int, int, int, int]]] = []
    n = len(data["text"])
    for i in range(n):
        text = data["text"][i].strip()
        conf = float(data["conf"][i])
        if text and conf > 0:
            words.append(
                (
                    text,
                    conf,
                    (
                        data["left"][i],
                        data["top"][i],
                        data["width"][i],
                        data["height"][i],
                    ),
                )
            )
    if not words:
        fallback_text = pytesseract.image_to_string(
            processed, lang=lang, config=custom_config
        )
        fallback_text = _normalize_text(fallback_text)
        if fallback_text:
            return OcrResult(text=fallback_text, confidence=50.0, bbox_px=(0, 0, 0, 0))
        return OcrResult(text="", confidence=0.0, bbox_px=(0, 0, 0, 0))
    full_text = " ".join(w[0] for w in words)
    avg_conf = sum(w[1] for w in words) / len(words)
    min_x = min(w[2][0] for w in words)
    min_y = min(w[2][1] for w in words)
    max_x = max(w[2][0] + w[2][2] for w in words)
    max_y = max(w[2][1] + w[2][3] for w in words)
    if config.text_cleanup_fn:
        full_text = config.text_cleanup_fn(full_text)
    if len(full_text) > MAX_TEXT_LENGTH:
        full_text = full_text[:MAX_TEXT_LENGTH]
    return OcrResult(
        text=_normalize_text(full_text),
        confidence=avg_conf,
        bbox_px=(min_x, min_y, max_x, max_y),
    )

def validate_field_result(
    result: OcrResult, field: dict, config: OcrConfig
) -> FieldExtractionResult:
    fid = field["id"]
    ftype = field.get("type", "text")
    if result.confidence < config.confidence_threshold:
        return FieldExtractionResult(
            field_id=fid,
            ocr_result=result,
            validated=False,
            error=f"low_ocr_confidence({result.confidence:.1f} < {config.confidence_threshold})",
        )
    text = result.text
    if _is_empty(text):
        if field.get("required", False):
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error="empty_required_field",
            )
        return FieldExtractionResult(field_id=fid, ocr_result=result, validated=True)

    # TODO: regex validate student id?

    if ftype == "version_marker":
        expected = field.get("expected", "")
        normalized = text.upper().strip()
        if normalized != expected.upper():
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                expected=expected,
                validated=False,
                error=f"version_mismatch(expected={expected!r}, got={text!r})",
            )

    if ftype == "name":
        if not re.match(r"^[A-Za-z\s\-'\.]+$", text):
            return FieldExtractionResult(
                field_id=fid,
                ocr_result=result,
                validated=False,
                error=f"invalid_name_characters(got={text!r})",
            )

    return FieldExtractionResult(field_id=fid, ocr_result=result, validated=True)

def extract_fields_from_page(
    page_image: Image.Image,
    fields: list[dict],
    config: OcrConfig,
    page_w: int,
    page_h: int,
) -> list[FieldExtractionResult]:
    results = []
    for f in fields:
        bbox_px = _bbox_norm_to_px(f["bbox"], page_w, page_h)
        region = crop_region(page_image, bbox_px)
        field_config = _override_config(config, f)
        ocr_result = ocr_region(region, field_config)
        ocr_result.text = _short_text_from_result(ocr_result.text, f)
        field_result = validate_field_result(ocr_result, f, field_config)
        results.append(field_result)
    return results

def extract_version_field(spec: dict) -> dict | None:
    for page_dict in spec.get("pages", []):
        for f in page_dict.get("fields", []):
            if f.get("type") == "version_marker":
                return f
    return None


def detect_version_from_anchors(
    page_image: Image.Image,
    page_anchors: list[dict],
    spec: dict,
) -> str | None:
    """
    Detect template version by checking which anchors are present in the image.

    Args:
        page_image: The rendered page image.
        page_anchors: List of anchor definitions from the spec for this page.
        spec: The full spec dictionary, used to look up version mappings.

    Returns:
        The detected version ID, or None if detection failed.
    """
    if not version_anchor_map(spec):
        return None
    if not page_anchors:
        return None

    arr = np.array(page_image)
    page_h, page_w = arr.shape[:2]
    detected_ids: set[str] = set()
    for anchor_dict in page_anchors:
        anchor = AnchorRegion(
            id=anchor_dict["id"],
            x_norm=anchor_dict["bbox"][0],
            y_norm=anchor_dict["bbox"][1],
            w_norm=anchor_dict["bbox"][2],
            h_norm=anchor_dict["bbox"][3],
        )
        if detect_anchor_presence(arr, anchor, page_w, page_h):
            detected_ids.add(anchor.id)

    return lookup_version_by_anchors(detected_ids, spec)


def make_review_reason(result: SubmissionResult, config: OcrConfig) -> None:
    result.needs_review = False
    result.review_reasons = []
    if not result.version_validated:
        result.needs_review = True
        result.review_reasons.append(result.version_error or "version_invalid")
    for fr in result.fields:
        if not fr.validated:
            result.needs_review = True
            fid = fr.field_id
            err = fr.error or "unknown"
            if fid not in result.review_reasons:
                result.review_reasons.append(f"{fid}: {err}")


def process_submission_pdf(
    pdf_path: str | Path,
    spec: dict,
    config: OcrConfig,
    output_dir: str | Path,
    response_format: Literal["png", "pdf"] = "png",
    dpi: int = 300,
) -> SubmissionResult:
    path = Path(pdf_path)
    result = SubmissionResult(filename=path.name)

    doc = fitz.open(str(path))
    all_field_results: list[FieldExtractionResult] = []
    all_question_results: list[QuestionExtractionResult] = []
    anchor_version_id: str | None = None

    for page_dict in spec.get("pages", []):
        page_num = page_dict["page_number"]
        page_idx = page_num - 1
        if page_idx < 0 or page_idx >= len(doc):
            continue

        page_image, page_w, page_h = render_page_to_image(doc, page_idx, dpi)

        # Try anchor-based version detection on the first page
        if page_num == 1 and anchor_version_id is None:
            anchor_version_id = detect_version_from_anchors(
                page_image, page_dict.get("anchors", []), spec
            )

        field_results = extract_fields_from_page(
            page_image, page_dict.get("fields", []), config, page_w, page_h
        )
        all_field_results.extend(field_results)

        questions = page_dict.get("questions", [])
        for q in questions:
            qid = q["id"]
            bbox_px = _bbox_norm_to_px(q["bbox"], page_w, page_h)
            region = crop_region(page_image, bbox_px)
            sub_dir = Path(output_dir) / "responses" / path.stem
            sub_dir.mkdir(parents=True, exist_ok=True)
            ext = response_format
            out_path = sub_dir / f"{qid}.{ext}"
            region.save(out_path)
            all_question_results.append(
                QuestionExtractionResult(
                    question_id=qid, page_number=page_num, output_path=str(out_path)
                )
            )

    # Version detection: prefer anchor-based, fall back to OCR version_marker
    if anchor_version_id is not None:
        result.version_id = anchor_version_id
        result.version_confidence = 100.0
        result.version_validated = True
        result.version_error = None
    else:
        version_field = extract_version_field(spec)
        version_result = next(
            (
                r
                for r in all_field_results
                if r.field_id == (version_field or {}).get("id")
            ),
            None,
        )
        if version_result:
            result.version_id = (
                version_result.ocr_result.text if version_result.ocr_result else None
            )
            result.version_confidence = (
                version_result.ocr_result.confidence
                if version_result.ocr_result
                else None
            )
            result.version_validated = version_result.validated
            result.version_error = version_result.error

    version_field = extract_version_field(spec)
    result.fields = [
        r for r in all_field_results if r.field_id != (version_field or {}).get("id")
    ]
    result.questions = all_question_results
    make_review_reason(result, config)

    return result

def results_to_dict(result: SubmissionResult) -> dict:
    return {
        "filename": result.filename,
        "version_id": result.version_id,
        "version_confidence": result.version_confidence,
        "version_validated": result.version_validated,
        "version_error": result.version_error,
        "needs_review": result.needs_review,
        "review_reasons": result.review_reasons,
        "fields": [
            {
                "field_id": r.field_id,
                "text": r.ocr_result.text if r.ocr_result else "",
                "confidence": r.ocr_result.confidence if r.ocr_result else 0.0,
                "validated": r.validated,
                "error": r.error,
            }
            for r in result.fields
        ],
        "questions": [
            {
                "question_id": q.question_id,
                "page_number": q.page_number,
                "output_path": q.output_path,
                "skipped": q.skipped,
                "error": q.error,
            }
            for q in result.questions
        ],
    }

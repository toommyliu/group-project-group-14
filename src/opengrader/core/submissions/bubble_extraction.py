from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from PIL import Image

from opengrader.core.bubble_sheet import (
    BubbleReading,
    BubbleSheetLayout,
    MarkConfig,
    QuestionRow,
)
from opengrader.core.pdf_utils import crop_norm, render_page_to_image
from opengrader.core.question_types import MULTIPLE_CHOICE
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions.transcription import SOURCE_BUBBLE_SHEET
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)

CROP_DPI = 150

CROP_PAD_X = 0.006
CROP_PAD_Y = 0.004

AMBIGUOUS_CONFIDENCE = 0.0


@dataclass(frozen=True)
class BubbleExtractionResult:
    document: StudentSubmissionDocument
    readings: tuple[BubbleReading, ...]
    warnings: tuple[str, ...] = ()

    @property
    def marked_count(self) -> int:
        return sum(1 for reading in self.readings if reading.status == "marked")

    @property
    def blank_count(self) -> int:
        return sum(1 for reading in self.readings if reading.status == "blank")

    @property
    def ambiguous_count(self) -> int:
        return sum(1 for reading in self.readings if reading.status == "ambiguous")


def _confidence_for(reading: BubbleReading) -> float:
    if reading.status == "ambiguous":
        return AMBIGUOUS_CONFIDENCE
    return reading.confidence


def _write_row_crop(
    row: QuestionRow,
    page_images: dict[int, Image.Image],
    responses_dir: Path,
) -> str | None:
    image = page_images.get(row.page)
    if image is None:
        return None
    crop = crop_norm(image, row.bbox, pad_x=CROP_PAD_X, pad_y=CROP_PAD_Y)
    if crop is None:
        return None
    responses_dir.mkdir(parents=True, exist_ok=True)
    crop_path = responses_dir / f"{row.question_id}.png"
    crop.save(crop_path)
    return f"responses/{crop_path.name}"


def build_bubble_submission_questions(
    layout: BubbleSheetLayout,
    readings: Sequence[BubbleReading],
    page_images: dict[int, Image.Image],
    responses_dir: Path,
) -> list[StudentSubmissionQuestion]:
    by_question = {reading.question_id: reading for reading in readings}
    questions: list[StudentSubmissionQuestion] = []

    for companion in layout.companions:
        questions.append(
            StudentSubmissionQuestion(
                question_id=companion.question_id,
                prompt="",
                possible_points=companion.points,
                response_image_path=None,
                response_text="",
                question_type=companion.question_type,
                source_segments=(),
                response_confidence=None,
            )
        )

    for row in layout.rows:
        reading = by_question.get(row.question_id)
        image_path = _write_row_crop(row, page_images, responses_dir)
        questions.append(
            StudentSubmissionQuestion(
                question_id=row.question_id,
                prompt="",
                possible_points=row.points,
                response_image_path=image_path,
                response_text=reading.response_text if reading else "",
                question_type=MULTIPLE_CHOICE,
                source_segments=(
                    QuestionSourceSegment(page_number=row.page, bbox_norm=row.bbox),
                ),
                response_confidence=_confidence_for(reading) if reading else None,
                response_source=SOURCE_BUBBLE_SHEET if reading else None,
            )
        )

    if layout.question_order:
        position = {qid: index for index, qid in enumerate(layout.question_order)}
        questions.sort(key=lambda q: position.get(q.question_id, len(position)))
    return questions


def build_bubble_sheet_document(
    pdf_path: Path,
    submission_dir: Path,
    layout: BubbleSheetLayout,
    *,
    assignment_title: str = "",
    course_code: str | None = None,
    assignment_version: str | None = None,
    student_id: str = "",
    student_name: str = "",
    read_dpi: int | None = None,
    mark_config: MarkConfig | None = None,
    read_marks: bool = True,
    progress_callback: Callable[[str], None] | None = None,
) -> BubbleExtractionResult:
    import fitz

    from opengrader.core.ocr.omr import DEFAULT_READ_DPI, read_sheet_images

    def report(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    read_dpi = read_dpi or DEFAULT_READ_DPI
    if read_marks:
        report(f"Reading bubble sheet marks from {pdf_path.name}…")

    read_images: list[Image.Image] = []
    crop_images: dict[int, Image.Image] = {}
    doc = fitz.open(str(pdf_path))
    try:
        page_total = min(len(doc), layout.page_count)
        for page_index in range(page_total):
            image, _width, _height = render_page_to_image(doc, page_index, dpi=read_dpi)
            read_images.append(image)
            crop_image, _cw, _ch = render_page_to_image(doc, page_index, dpi=CROP_DPI)
            crop_images[page_index + 1] = crop_image
    finally:
        doc.close()

    warnings: list[str] = []
    if not read_marks:
        warnings.append(
            f"{pdf_path.name}: question order unknown, marks not read"
        )
        result_readings: tuple[BubbleReading, ...] = ()
    elif not read_images:
        warnings.append(f"{pdf_path.name} has no readable pages")
        result_readings = ()
    else:
        sheet = read_sheet_images(read_images, layout, mark_config=mark_config)
        result_readings = sheet.readings
        warnings.extend(sheet.failures)

    responses_dir = submission_dir / "responses"
    questions = build_bubble_submission_questions(
        layout, result_readings, crop_images, responses_dir
    )

    document = StudentSubmissionDocument(
        student_name=student_name,
        student_id=student_id,
        course_code=course_code,
        assignment_title=assignment_title,
        assignment_version=assignment_version,
        source_pdf=str(pdf_path),
        questions=questions,
    )

    if read_marks:
        ambiguous = sum(1 for r in result_readings if r.status == "ambiguous")
        blank = sum(1 for r in result_readings if r.status == "blank")
        report(
            f"Read {len(result_readings)} question(s): "
            f"{len(result_readings) - blank - ambiguous} marked, {blank} blank, "
            f"{ambiguous} with multiple marks"
        )

    return BubbleExtractionResult(
        document=document,
        readings=tuple(result_readings),
        warnings=tuple(warnings),
    )

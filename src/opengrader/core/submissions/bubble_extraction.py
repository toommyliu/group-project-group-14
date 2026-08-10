"""Turn a scanned bubble sheet into an ordinary student submission document.

The fourth extraction mode, beside template-alignment, born-digital text layer
and dark-scan OCR. What makes it worth its own module is how *little* it has to
do: because the sheet's geometry is known (`core/bubble_sheet.py`) and reading
it is a measurement (`core/ocr/omr.py`), there is no layout detection, no OCR
and no vision model in this path at all.

The design rule here is the whole point of the feature: **the document this
produces is indistinguishable from any other extraction's**. Same
``.submission.md``, same ``responses/qN.png`` crops, same ``Source segments``,
same ``Confidence``. So the grading TUI, auto-grading, item analysis, the
review queue, the return bundle and the gradebook all work on bubble sheets
without knowing bubble sheets exist. The one thing that would have broken that
— treating an unreadable mark as its own new concept — is instead expressed in
the vocabulary that is already there: an empty response with a low confidence,
which the needs-review queue already knows how to raise.
"""

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

# Crops are rendered at a lower DPI than the marks are *read* at: the crop only
# has to let a grader see which circle is filled, while the measurement wants
# every pixel it can get.
CROP_DPI = 150

# A row crop gets a little breathing room so the question number and the last
# choice are not flush against the edge when a grader looks at it.
CROP_PAD_X = 0.006
CROP_PAD_Y = 0.004

# Confidence recorded for a mark we refused to interpret (two bubbles filled).
# Zero rather than "unknown": unlike a vision-model reading, we measured this
# one and the measurement is what says it is unusable, so it belongs below the
# review queue's floor rather than in the unverified bucket.
AMBIGUOUS_CONFIDENCE = 0.0


@dataclass(frozen=True)
class BubbleExtractionResult:
    """A built document plus what the reader could not do."""

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
    """Map a reading onto the 0-100 scale the rest of the app already uses.

    An ambiguous mark reports 0 so it lands under
    ``review.DEFAULT_OCR_CONFIDENCE_FLOOR`` and shows up in the needs-review
    queue — which is exactly the right destination, since a human has to look
    at the sheet to decide what the student meant.
    """
    if reading.status == "ambiguous":
        return AMBIGUOUS_CONFIDENCE
    return reading.confidence


def _write_row_crop(
    row: QuestionRow,
    page_images: dict[int, Image.Image],
    responses_dir: Path,
) -> str | None:
    """Save the question's row of bubbles as its response image.

    Handing the grader the actual row — not a blank box — is what makes a
    bubble sheet gradeable by eye when the reader is unsure. Returns the
    markdown-relative path, or ``None`` when the page was not rendered.
    """
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
    """Build the question blocks for a read bubble sheet.

    Every question in the *layout* produces a block, including ones the reader
    could not resolve: a missing block would silently drop the question from
    the gradebook, while an empty one is visible everywhere a grader looks.
    """
    by_question = {reading.question_id: reading for reading in readings}
    questions: list[StudentSubmissionQuestion] = []

    # Answer-key questions with no row on the sheet (a written section handed
    # in on separate paper). They get an empty, un-captured block so the
    # submission's question set still matches the answer key — without it the
    # grading TUI cannot save a grade for them at all.
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
                # Nothing was measured here, so the confidence is unknown
                # rather than bad — the same meaning it carries for a
                # vision-model reading.
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
                # Carried from the answer key via the layout, so the gradebook's
                # `possible` column is right before anyone opens the TUI.
                possible_points=row.points,
                response_image_path=image_path,
                response_text=reading.response_text if reading else "",
                question_type=MULTIPLE_CHOICE,
                source_segments=(
                    QuestionSourceSegment(page_number=row.page, bbox_norm=row.bbox),
                ),
                # No reading at all (a page the scanner dropped) is *unverified*,
                # not zero-confidence: nothing was measured, so claiming a
                # confident anything would be a lie. That is the same meaning
                # `None` already carries for a vision-model transcription.
                response_confidence=_confidence_for(reading) if reading else None,
                # Recorded for the same reason the confidence is: a mark that
                # was *measured* off a sheet we drew ourselves is a different
                # kind of evidence from a model's guess at handwriting, and
                # every report that compares readers has to be able to tell
                # them apart. A page with no reading names no reader.
                response_source=SOURCE_BUBBLE_SHEET if reading else None,
            )
        )

    if layout.question_order:
        # Answer-key order, so a grader stepping through the exam meets the
        # questions in the order the paper asks them rather than bubbles first.
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
    """Read one scanned bubble sheet into a submission document.

    ``read_marks=False`` builds the document *without* reading any bubbles —
    the questions, their crops and their points, but every answer unread and
    unverified. That is the honest output when the sheet's question order is
    unknown (a scrambled batch whose form key would not scan): the alternative
    is to measure marks against the wrong rows and report a confident, wrong
    set of answers.
    """
    import fitz

    from opengrader.core.ocr.omr import DEFAULT_READ_DPI, read_sheet_images

    def report(message: str) -> None:
        if progress_callback is not None:
            progress_callback(message)

    read_dpi = read_dpi or DEFAULT_READ_DPI
    if read_marks:
        report(f"Reading bubble sheet marks from {pdf_path.name}…")

    # Render each page once at the higher read DPI and once for the crops. Two
    # passes over an already-open document is cheaper than re-opening the file,
    # and keeps the measurement resolution independent of the crop resolution.
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

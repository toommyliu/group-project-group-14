"""Cut a scanned submission into question crops using a region map.

Answers are never read. The name and ID crops are read only to match a roster.
"""

import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import fitz
from PIL import Image

from opengrader.core.pdf_utils import crop_norm, render_page_to_image
from opengrader.core.region_map import RegionBox, RegionMap
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions.student_file import (
    IdentityCandidate,
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    render_student_submission_markdown,
)

CROP_DPI = 200


@dataclass(frozen=True)
class RosterStudent:
    student_id: str
    name: str


@dataclass(frozen=True)
class CropInputs:
    regions: RegionMap
    title: str
    version: str | None
    roster: tuple[RosterStudent, ...] = field(default=())
    student: RosterStudent | None = None


@dataclass(frozen=True)
class Identity:
    student_name: str = ""
    student_id: str = ""
    match_percent: float | None = None
    candidates: tuple[IdentityCandidate, ...] = ()


def identify(name_crop: Path | None, id_crop: Path | None, roster: tuple[RosterStudent, ...]) -> Identity:
    """Read the name and ID crops and keep only what matches a student on the roster."""
    from opengrader.core.pages.printed import read_field
    from opengrader.core.submissions.processor import _RosterEntry, resolve_student_identity

    def read(crop: Path | None, kind: str) -> str:
        if crop is None:
            return ""
        with Image.open(crop) as image:
            return read_field(image, kind)[0]

    name, student_id = read(name_crop, "name"), read(id_crop, "sid")
    digits = re.sub(r"\D", "", name)
    if not student_id and len(digits) >= 7:
        name, student_id = "", digits
    match = resolve_student_identity(
        student_id, name, [_RosterEntry(student_id=item.student_id, name=item.name) for item in roster]
    )
    if match.status == "matched" and match.entry is not None:
        return Identity(match.entry.name, match.entry.student_id, round((match.score or 0) * 100))
    return Identity(
        candidates=tuple(IdentityCandidate(item.student_id, item.name) for item in match.candidates)
    )


def crop_submission(pdf: Path, inputs: CropInputs, output: Path) -> Path:
    """Write `<output>/<pdf stem>/` with crops and a submission file; return that directory."""
    regions = inputs.regions
    directory = output / pdf.stem
    shutil.rmtree(directory, ignore_errors=True)
    (directory / "responses").mkdir(parents=True)
    (directory / "identifiers").mkdir()
    with fitz.open(pdf) as document:
        page_count = len(document)
        wanted = {
            box.page
            for box in (
                regions.name_field,
                regions.student_id_field,
                *(segment for question in regions.questions for segment in question.segments),
            )
            if box is not None and box.page <= page_count
        }
        pages = {
            page: render_page_to_image(document, page - 1, dpi=CROP_DPI, cache=False)[0]
            for page in sorted(wanted)
        }

    def save(boxes: tuple[RegionBox, ...], path: Path) -> tuple[QuestionSourceSegment, ...]:
        crops = [
            (box, crop.convert("RGB"))
            for box in boxes
            if box.page in pages and (crop := crop_norm(pages[box.page], box.bbox)) is not None
        ]
        if not crops:
            return ()
        image = crops[0][1]
        if len(crops) > 1:
            image = Image.new(
                "RGB",
                (max(crop.width for _, crop in crops), sum(crop.height for _, crop in crops)),
                color=(255, 255, 255),
            )
            top = 0
            for _, crop in crops:
                image.paste(crop, (0, top))
                top += crop.height
        image.save(path)
        return tuple(QuestionSourceSegment(box.page, box.bbox) for box, _ in crops)

    def identifier(box: RegionBox | None, name: str) -> str | None:
        path = directory / "identifiers" / f"{name}.png"
        return f"identifiers/{name}.png" if box and save((box,), path) else None

    questions = []
    for question in regions.questions:
        path = directory / "responses" / f"{question.question_id}.png"
        segments = save(question.segments, path)
        questions.append(
            StudentSubmissionQuestion(
                question_id=question.question_id,
                prompt="",
                possible_points=None,
                response_image_path=f"responses/{path.name}" if segments else None,
                response_text="",
                source_segments=segments,
            )
        )
    name_image = identifier(regions.name_field, "name")
    id_image = identifier(regions.student_id_field, "id")
    found = (
        Identity(inputs.student.name, inputs.student.student_id)
        if inputs.student
        else identify(
            directory / name_image if name_image else None,
            directory / id_image if id_image else None,
            inputs.roster,
        )
        if inputs.roster and (name_image or id_image)
        else Identity()
    )
    submission = StudentSubmissionDocument(
        student_name=found.student_name,
        student_id=found.student_id,
        course_code=None,
        assignment_title=inputs.title,
        assignment_version=inputs.version,
        source_pdf=str(pdf.resolve()),
        name_image_path=name_image,
        id_image_path=id_image,
        name_confidence=found.match_percent,
        id_candidates=found.candidates,
        extra_pages=tuple(range(regions.source_pages + 1, page_count + 1)),
        questions=questions,
    )
    (directory / f"{pdf.stem}.submission.md").write_text(
        render_student_submission_markdown(submission), encoding="utf-8"
    )
    return directory

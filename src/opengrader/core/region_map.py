"""Grade an assignment Opengrader did not generate — draw the boxes once.

The rest of the pipeline is at its strongest when *it* produced the assignment:
``qa-file generate-template`` writes a blank PDF whose text layer carries
``N. (M points) …`` on every question, and ``submission-extractor
--blank-template`` reads those lines back to learn where each answer lives. An
instructor who already has an assignment — a quiz written in Word, a problem
set typeset in LaTeX, a final photocopied from a paper original with no text
layer at all — has none of that, and until this module existed they had no way
in.

A **region map** is the way in. The instructor opens a blank copy of their own
assignment, draws a box around each answer area once (or corrects the boxes
this module proposes), and saves the result next to the PDF as
``<assignment-stem>.regions.json``. From then on ``submission-extractor
--region-map`` crops every student's scan against those boxes. The assignment
never needs a text layer, and the map is written once per assignment, not once
per student.

How it plugs in
---------------

It does **not** add an extraction path. ``core/submissions/template.py`` defines
:class:`AssignmentTemplate`, the single structural shape the scan-matching
pipeline consumes, and :func:`region_map_to_blank_template` is simply a second
producer of it. Everything downstream — cropping, OCR, the vision-model pass,
``--llm-escalate``, ``--resume``, the grading TUI — runs unchanged and cannot
drift, because there is only one of it.

Decisions recorded here, not to be reversed
-------------------------------------------

**A map is bound to one assignment file, and the binding is enforced on load.**
The map stores the blank PDF's SHA-256 (``manifest.file_fingerprint`` — there is
exactly one hasher in this codebase and this is not a second one), its page
count and its page size. Applying a map to the wrong PDF, or to a re-export of
the right one whose pagination shifted by a line, silently mis-crops *every*
answer of *every* student: the run succeeds, the crops are of the wrong parts of
the page, the transcriptions are plausible nonsense, and nothing anywhere says
so. That is the single most expensive failure this feature could have, so a
fingerprint mismatch is a **refusal with an explanation**, never a warning and
never a best-effort guess. There is no safe guess to make: if the instructor
re-exported the assignment, the boxes moved, and only they can say by how much.

**Normalized ``[x, y, w, h]``, 1-based pages, everywhere.** Same convention as
``pdf_utils.bbox_norm_to_px`` / ``crop_norm`` and ``QuestionSourceSegment``, so a
map is DPI- and paper-size-independent and no coordinate is ever converted more
than once.

**Boxes are clamped on load, not rejected.** A file someone hand-edited (or a
map written by a future version with a wider idea of a page) must never crash an
overnight extraction of 200 scans. Clamping to the page turns a nonsense box
into a small or empty one, which :func:`validate_region_map` then reports.
Validation runs on the in-memory value, so a box the *editor* has dragged off
the page is still flagged as such before it is ever saved.

**Validation lives here, not in the UI.** Both ``region-map validate`` and the
TUI editor render the same :class:`RegionMapFinding` list, so they can never
disagree about whether a map is usable. Severity is the whole contract: an
``error`` means the map would produce wrong output and saving is refused; a
``warning`` is something an instructor may legitimately mean — question 7 left
unmapped because it is extra credit is an ordinary Tuesday.

**Continuations are first-class.** A map that cannot express "question 4's
answer runs onto page 3" is a map an instructor abandons on their first
two-page question. Continuation segments are cropped and stacked into the one
response image the rest of the pipeline expects (``processor.
_question_with_mapped_crop``).

Format version policy
---------------------

``version`` is an integer, currently :data:`REGION_MAP_VERSION`. A map written
by an **older** version is upgraded on load: unknown-to-it fields simply take
their defaults, which is why every field added after v1 must have one. A map
written by a **newer** version is refused rather than partially understood —
half-reading a format that has learned to express something new (rotated boxes,
per-page scaling) is how a map silently loses the half it did not understand.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Mapping, Sequence

from opengrader.core.pdf_utils import clamp_bbox_norm
from opengrader.core.question_types import FREE_RESPONSE, QuestionType
from opengrader.core.source_location import QuestionSourceSegment
from opengrader.core.submissions.manifest import file_fingerprint
from opengrader.core.submissions.student_file import StudentSubmissionQuestion
from opengrader.core.submissions.template import AssignmentTemplate, FieldLocation

if TYPE_CHECKING:  # pragma: no cover - typing only
    from PIL import Image

    from opengrader.core.qa_file import QADocument


# ==============================================================================
# TUNABLES
# ==============================================================================

#: The format this module reads and writes. Bumping it is a promise that
#: :func:`load_region_map` still understands every earlier integer.
REGION_MAP_VERSION = 1

#: Written into every file so a stray JSON opened by mistake is rejected by
#: name rather than by a confusing missing-key error.
REGION_MAP_FORMAT = "opengrader-region-map"

#: What a region map is called next to the assignment it maps. A suffix rather than a
#: sidecar directory, because the two files are only useful together and an
#: instructor emailing "the assignment and its boxes" should be able to see both.
REGION_MAP_SUFFIX = ".regions.json"

#: DPI the proposal renders each page at before running layout detection. The
#: detector reads printed question numbers and rules, not handwriting, so it
#: needs far less resolution than the 300 DPI extraction crops — and this pass
#: runs over every page of the assignment while an instructor waits.
PROPOSAL_DPI = 200

#: DPI for the preview/editor page renders. A US-Letter page at 150 DPI is
#: ~1275x1650: enough to see whether a box contains the answer area, small
#: enough that re-rendering while paging feels instant and a multi-page preview
#: PDF stays a few megabytes rather than a few dozen.
PREVIEW_DPI = 150

#: How far outside the page a box may sit before it is reported. Rounding to
#: four decimals (what every bbox here is stored at) can put an edge a
#: ten-thousandth past 1.0, which is not a mistake anybody made.
_OFF_PAGE_TOLERANCE = 0.002

#: Fraction of the *smaller* of two boxes that must be covered by the other
#: before the overlap is reported. Answer regions genuinely touch — a box drawn
#: to the same margins as its neighbour shares an edge — so a small overlap is
#: normal. A third of the smaller box inside the larger is not: that is two
#: answers cropped into each other, which reaches the grader as question 4's
#: crop containing half of question 5.
_OVERLAP_REPORT_RATIO = 0.35

#: Default size of a freshly added box, as a fraction of the page. Wide enough
#: to hold a written answer and tall enough to grab, so "add a region" lands
#: something visible that the instructor then drags into place rather than
#: something they have to hunt for.
NEW_REGION_SIZE = (0.80, 0.12)

#: Left/right and top/bottom margins a uniform grid leaves. Matches the answer
#: column the text-layer path uses (``_response_bbox_from_positions`` crops
#: x 0.08..0.92), so a grid map and a generated-template map frame an answer the
#: same way.
GRID_MARGIN_X = 0.08
GRID_MARGIN_Y = 0.10

#: Colour of the emphasized (selected) region in a rendered preview. Distinct in
#: hue from ``pdf_utils.HIGHLIGHT_RGB``'s warm orange so the two never read as
#: the same box at a glance, and legible on white paper in a half-block
#: terminal, where fine detail is lost but hue survives.
EMPHASIS_RGB = (94, 160, 214)

#: Severities, ordered. The contract: ``error`` blocks a save, everything else
#: informs one. Mirrors the shape of ``core/submissions/scan_check.ScanIssue``
#: while using this feature's vocabulary — "error" is what the editor and the
#: CLI both talk about, and a map with a duplicate question id is not "high
#: severity", it is wrong.
SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

_SEVERITY_RANK = {SEVERITY_ERROR: 0, SEVERITY_WARNING: 1, SEVERITY_INFO: 2}


class RegionMapError(ValueError):
    """A region map could not be read, or does not describe the given PDF.

    A ``ValueError`` subclass so the CLIs' existing ``except (FileNotFoundError,
    ValueError)`` handling around structural inputs treats it as the bad-input
    error it is.
    """


# ==============================================================================
# DATA
# ==============================================================================


@dataclass(frozen=True)
class RegionBox:
    """One rectangle on one page of the blank assignment."""

    page: int  # 1-based
    bbox: tuple[float, float, float, float]  # normalized [x, y, w, h]


@dataclass(frozen=True)
class MappedQuestion:
    """One question's answer region, plus what grading needs to know about it.

    ``continuations`` are further rectangles belonging to this same answer, in
    reading order after the primary box — the "continued on the back" case.

    ``confidence`` is provenance, not truth: it is the detector's own 0-1 score
    when the box was proposed by :func:`propose_region_map`, and ``None`` once a
    human drew or moved it. Kept so the editor can walk an instructor straight
    to the boxes least likely to be right (its ``u`` key) instead of making them
    check twenty confident ones to find the one bad guess.
    """

    question_id: str
    number: int
    page: int
    bbox: tuple[float, float, float, float]
    possible_points: float | None = None
    question_type: QuestionType = FREE_RESPONSE
    continuations: tuple[RegionBox, ...] = ()
    prompt: str = ""
    confidence: float | None = None

    @property
    def segments(self) -> tuple[RegionBox, ...]:
        """Every rectangle of this answer, primary first."""
        return (RegionBox(self.page, self.bbox), *self.continuations)


@dataclass(frozen=True)
class RegionMap:
    """The persisted answer to "where are the answers on this assignment?".

    ``source_sha256``/``source_pages``/``source_page_size`` identify the exact
    blank PDF the boxes were drawn on — see :func:`verify_region_map` for why
    that identification is enforced rather than advisory.
    """

    source_name: str
    source_sha256: str
    source_pages: int
    source_page_size: tuple[float, float]  # PDF points (width, height) of page 1
    assignment_title: str = ""
    name_field: RegionBox | None = None
    student_id_field: RegionBox | None = None
    title_field: RegionBox | None = None
    questions: tuple[MappedQuestion, ...] = ()
    version: int = REGION_MAP_VERSION
    created_at: str = ""

    def question_at(self, index: int) -> MappedQuestion | None:
        if 0 <= index < len(self.questions):
            return self.questions[index]
        return None

    def boxes_on_page(self, page: int) -> tuple[tuple[str, RegionBox], ...]:
        """Every labelled rectangle on ``page``, in drawing order.

        The one place that decides what a page's overlay shows, so the CLI
        preview and the TUI editor cannot disagree about it.
        """
        found: list[tuple[str, RegionBox]] = []
        for label, box in (
            ("Name", self.name_field),
            ("ID", self.student_id_field),
            ("Title", self.title_field),
        ):
            if box is not None and box.page == page:
                found.append((label, box))
        for question in self.questions:
            for index, segment in enumerate(question.segments):
                if segment.page != page:
                    continue
                suffix = "" if index == 0 else f" cont.{index}"
                found.append((f"{question.question_id}{suffix}", segment))
        return tuple(found)


@dataclass(frozen=True)
class RegionMapFinding:
    """One thing wrong (or worth knowing) about a map.

    ``kind`` is a stable machine-readable slug for tests and ``--json``;
    ``message`` is what a human reads. ``question_id``/``page`` locate it when
    they apply.
    """

    kind: str
    severity: str
    message: str
    question_id: str | None = None
    page: int | None = None

    @property
    def is_error(self) -> bool:
        return self.severity == SEVERITY_ERROR


@dataclass(frozen=True)
class RegionMapVerification:
    """Whether a map may be applied to a PDF, and if not, precisely why.

    Structured rather than a bare bool because the caller has to *say* what is
    wrong: "this map is for midterm-v1.pdf (4 pages), you pointed it at
    midterm-v2.pdf (5 pages)" is actionable, and "invalid region map" is not.
    """

    ok: bool
    kind: str = "ok"  # "ok" | "missing_pdf" | "fingerprint" | "page_count"
    message: str = ""

    def __bool__(self) -> bool:
        return self.ok


@dataclass(frozen=True)
class RegionMapProposal:
    """A draft map plus what the detector was unsure about.

    ``review_reasons`` come straight from ``SubmissionLayout`` (missing question
    markers, a name field it could not find, non-monotonic numbering). They are
    the honest answer to "why does this proposal look thin?", and they are what
    tells an instructor to start from a grid instead of correcting guesses.
    """

    region_map: RegionMap
    review_reasons: tuple[str, ...] = ()


# ==============================================================================
# IDENTIFYING THE ASSIGNMENT
# ==============================================================================


def default_region_map_path(pdf_path: str | Path) -> Path:
    """``assignment.pdf`` -> ``assignment.regions.json``, beside the assignment."""
    path = Path(pdf_path)
    return path.with_name(path.stem + REGION_MAP_SUFFIX)


def _pdf_identity(pdf_path: str | Path) -> tuple[str, str, int, tuple[float, float]]:
    """Read the identifying facts about a blank assignment PDF.

    Deliberately does **not** touch the text layer: a region map exists exactly
    so that an image-only scan of a paper assignment can be graded, and asking for
    text here would refuse the very files this feature is for.
    """
    import fitz

    path = Path(pdf_path)
    if not path.exists():
        raise RegionMapError(f"blank assignment PDF not found: {path}")
    with fitz.open(str(path)) as doc:
        page_count = len(doc)
        if page_count == 0:
            raise RegionMapError(f"blank assignment PDF has no pages: {path}")
        rect = doc[0].rect
        page_size = (round(float(rect.width), 2), round(float(rect.height), 2))
    return path.name, file_fingerprint(path), page_count, page_size


def verify_region_map(
    region_map: RegionMap, pdf_path: str | Path
) -> RegionMapVerification:
    """Is this map the one that was drawn on this PDF?

    Checked in the order that produces the most useful message: a missing file
    first, then the bytes, then the page count (which is what a fingerprint
    mismatch usually *means* to a human — "you re-exported it and it grew a
    page"). The fingerprint alone is authoritative; the page count is reported
    separately only because it explains the mismatch in the instructor's terms.
    """
    path = Path(pdf_path)
    if not path.exists():
        return RegionMapVerification(
            False, "missing_pdf", f"blank assignment PDF not found: {path}"
        )

    try:
        _name, digest, page_count, _size = _pdf_identity(path)
    except RegionMapError as exc:
        return RegionMapVerification(False, "missing_pdf", str(exc))

    if page_count != region_map.source_pages:
        return RegionMapVerification(
            False,
            "page_count",
            f"this region map was drawn on a {region_map.source_pages}-page assignment "
            f"({region_map.source_name}), but {path.name} has {page_count} pages. "
            "Re-map the current assignment with: region-map propose --input "
            f"{path.name} --output {default_region_map_path(path).name}",
        )

    if not digest or digest != region_map.source_sha256:
        return RegionMapVerification(
            False,
            "fingerprint",
            f"{path.name} is not the file this region map was drawn on "
            f"(expected {region_map.source_name}). Even a re-export of the same "
            "assignment moves the boxes, so applying this map would mis-crop every "
            "answer of every student. Re-map the current file with: region-map "
            f"propose --input {path.name} --output "
            f"{default_region_map_path(path).name}",
        )

    return RegionMapVerification(True)


# ==============================================================================
# BUILDING A MAP
# ==============================================================================


def blank_region_map(
    pdf_path: str | Path, *, assignment_title: str = ""
) -> RegionMap:
    """An empty map bound to ``pdf_path`` — the start-from-scratch path.

    A first-class starting point, not a fallback: on an assignment whose layout the
    detector cannot read at all, drawing four boxes by hand is faster than
    correcting four wrong ones, and an editor that hides this behind a failure
    is an editor nobody can start.
    """
    name, digest, pages, size = _pdf_identity(pdf_path)
    return RegionMap(
        source_name=name,
        source_sha256=digest,
        source_pages=pages,
        source_page_size=size,
        assignment_title=assignment_title or Path(pdf_path).stem,
        created_at=_now(),
    )


def grid_region_map(
    pdf_path: str | Path,
    *,
    per_page: int = 4,
    pages: Sequence[int] | None = None,
    columns: int = 1,
    start_number: int = 1,
    assignment_title: str = "",
) -> RegionMap:
    """A map of evenly spaced boxes — the other honest starting point.

    Plenty of real assignments *are* a grid: four equal answer spaces to a page, or
    two columns of short-answer boxes. Laying that out arithmetically and
    nudging a few edges beats drawing twenty rectangles, and it needs no
    detection at all, so it works on the assignments detection cannot read.
    """
    name, digest, page_count, size = _pdf_identity(pdf_path)
    target_pages = list(pages) if pages else list(range(1, page_count + 1))
    per_page = max(1, per_page)
    columns = max(1, columns)
    rows = max(1, -(-per_page // columns))  # ceiling division

    usable_w = 1.0 - 2 * GRID_MARGIN_X
    usable_h = 1.0 - 2 * GRID_MARGIN_Y
    cell_w = usable_w / columns
    cell_h = usable_h / rows
    # Breathing room between cells so two adjacent boxes never share an edge and
    # trip the overlap check — and so an instructor can see where one ends.
    gap_x = cell_w * 0.04
    gap_y = cell_h * 0.06

    questions: list[MappedQuestion] = []
    number = start_number
    for page in target_pages:
        if page < 1 or page > page_count:
            continue
        for index in range(per_page):
            row, column = divmod(index, columns)
            x = GRID_MARGIN_X + column * cell_w
            y = GRID_MARGIN_Y + row * cell_h
            questions.append(
                MappedQuestion(
                    question_id=f"q{number}",
                    number=number,
                    page=page,
                    bbox=clamp_bbox_norm(
                        (x, y, cell_w - gap_x, cell_h - gap_y)
                    ),
                )
            )
            number += 1

    return RegionMap(
        source_name=name,
        source_sha256=digest,
        source_pages=page_count,
        source_page_size=size,
        assignment_title=assignment_title or Path(pdf_path).stem,
        questions=tuple(questions),
        created_at=_now(),
    )


def propose_region_map(
    pdf_path: str | Path,
    *,
    dpi: int = PROPOSAL_DPI,
    assignment_title: str = "",
    split_subquestions: bool = False,
) -> RegionMapProposal:
    """Detect a draft map so the instructor *corrects* boxes instead of drawing them.

    Reuses ``core/ocr/layout.scan_submission_layout`` — the same detector the
    no-text-layer extraction path already trusts — rather than growing a second
    one. Its output is exactly the three things a map needs (question regions
    with continuations, name/id fields, a title region), which is why this
    function is a translation and not an algorithm.

    Subquestion splitting is **off** by default here: on someone else's assignment a
    detected sub-part is as likely to be a stray rule as a real (a) / (b), and a
    map with phantom questions is worse to correct than one with too few.

    A poor or empty proposal is an acceptable outcome on an unusual assignment —
    ``review_reasons`` says so, and :func:`grid_region_map` and
    :func:`blank_region_map` are the alternatives, not the consolation prizes.
    """
    # Imported here, not at module scope: `core.ocr.layout` pulls in OpenCV and
    # pytesseract, which cost hundreds of milliseconds to import. `region-map
    # validate` and `--region-map` extraction never propose anything, and the
    # TUI editor should not pay for the detector until someone asks to detect.
    import fitz

    from opengrader.core.ocr import OcrConfig
    from opengrader.core.ocr.layout import scan_submission_layout
    from opengrader.core.pdf_utils import render_page_to_image

    name, digest, page_count, size = _pdf_identity(pdf_path)

    rendered: dict[int, tuple["Image.Image", int, int]] = {}
    with fitz.open(str(pdf_path)) as doc:
        for index in range(len(doc)):
            image, width, height = render_page_to_image(doc, index, dpi=dpi)
            rendered[index + 1] = (image, width, height)

    layout = scan_submission_layout(
        rendered, OcrConfig(), split_subquestions=split_subquestions
    )

    name_field: RegionBox | None = None
    id_field: RegionBox | None = None
    for field in layout.fields:
        box = RegionBox(field.page_number, clamp_bbox_norm(field.crop_bbox_norm))
        if field.field_kind == "name" and name_field is None:
            name_field = box
        elif field.field_kind == "sid" and id_field is None:
            id_field = box

    title_field = (
        RegionBox(
            layout.title_region.page_number,
            clamp_bbox_norm(layout.title_region.crop_bbox_norm),
        )
        if layout.title_region is not None
        else None
    )

    questions = tuple(
        sorted(
            (
                MappedQuestion(
                    question_id=region.question_id,
                    number=region.question_number,
                    page=region.page_number,
                    bbox=clamp_bbox_norm(region.crop_bbox_norm),
                    question_type=region.question_type,
                    continuations=tuple(
                        RegionBox(page, clamp_bbox_norm(bbox))
                        for page, bbox in region.continuation_segments
                    ),
                    confidence=round(float(region.confidence), 3),
                )
                for region in layout.questions
            ),
            key=lambda question: (question.number, question.page, question.bbox[1]),
        )
    )

    region_map = RegionMap(
        source_name=name,
        source_sha256=digest,
        source_pages=page_count,
        source_page_size=size,
        assignment_title=assignment_title or Path(pdf_path).stem,
        name_field=name_field,
        student_id_field=id_field,
        title_field=title_field,
        questions=questions,
        created_at=_now(),
    )
    return RegionMapProposal(region_map, tuple(layout.review_reasons))


def seed_region_map_from_qa(
    region_map: RegionMap, qa_document: "QADocument"
) -> RegionMap:
    """Copy points, question types and prompts from an answer key onto a map.

    The instructor's rubric already knows what question 4 is worth and whether
    it is multiple choice; making them retype that into a geometry editor is
    busywork with a chance of a typo. Matched by question id first (``q3`` to
    ``q3``, which is what both sides call them), falling back to position for a
    map whose ids were renumbered by hand — the ordering of both lists is the
    question order, so the fallback is a real match rather than a guess.

    Geometry is never touched: this fills in *what* each box is, never *where*.
    """
    from opengrader.core.qa_file import possible_points_by_question, qa_question_type

    points = possible_points_by_question(qa_document)
    types: dict[str, QuestionType] = {}
    prompts: dict[str, str] = {}
    ordered_ids: list[str] = []

    def record(question) -> None:  # noqa: ANN001 - QAQuestion | QASubquestion
        if question.id in types:
            return
        types[question.id] = qa_question_type(question)
        prompts[question.id] = question.prompt.strip()
        ordered_ids.append(question.id)

    # The shared questions first, then each version's own, so a multi-version QA
    # file seeds every id it knows while the common order stays the reading
    # order. A parent with subquestions is not itself graded — its parts are —
    # so the parts are what a map's boxes correspond to.
    version_lists = [qa_document.questions] + [
        qa_document.version_questions.get(version, [])
        for version in qa_document.versions
    ]
    for questions in version_lists:
        for question in questions:
            if question.subquestions:
                for subquestion in question.subquestions:
                    record(subquestion)
            else:
                record(question)

    seeded: list[MappedQuestion] = []
    for index, question in enumerate(region_map.questions):
        key = question.question_id
        if key not in points and index < len(ordered_ids):
            key = ordered_ids[index]
        if key not in points and key not in types:
            seeded.append(question)
            continue
        seeded.append(
            replace(
                question,
                possible_points=points.get(key, question.possible_points),
                question_type=types.get(key, question.question_type),
                prompt=prompts.get(key) or question.prompt,
            )
        )
    return replace(region_map, questions=tuple(seeded))


# ==============================================================================
# PERSISTENCE
# ==============================================================================


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _box_to_json(box: RegionBox | None) -> dict[str, Any] | None:
    if box is None:
        return None
    return {"page": box.page, "bbox": [round(value, 4) for value in box.bbox]}


def _box_from_json(raw: Any) -> RegionBox | None:
    """Read one rectangle, clamping it onto the page. ``None`` when absent/junk.

    Tolerant on purpose — see the module docstring on why a hand-edited file
    must degrade rather than crash. A box that cannot be understood at all is
    dropped, and the resulting "no name field" is reported by validation.
    """
    if not isinstance(raw, Mapping):
        return None
    try:
        page = int(raw.get("page", 1))
        bbox = clamp_bbox_norm([float(value) for value in raw.get("bbox", ())])
    except (TypeError, ValueError):
        return None
    return RegionBox(page=max(1, page), bbox=bbox)


def region_map_to_json(region_map: RegionMap) -> dict[str, Any]:
    """The on-disk shape, as a plain dict (also what ``--json`` prints)."""
    return {
        "format": REGION_MAP_FORMAT,
        "version": region_map.version,
        "created_at": region_map.created_at,
        "assignment_title": region_map.assignment_title,
        "source": {
            "filename": region_map.source_name,
            "sha256": region_map.source_sha256,
            "pages": region_map.source_pages,
            "page_size": list(region_map.source_page_size),
        },
        "fields": {
            "name": _box_to_json(region_map.name_field),
            "student_id": _box_to_json(region_map.student_id_field),
            "title": _box_to_json(region_map.title_field),
        },
        "questions": [
            {
                "id": question.question_id,
                "number": question.number,
                "points": question.possible_points,
                "type": question.question_type,
                "page": question.page,
                "bbox": [round(value, 4) for value in question.bbox],
                "continuations": [
                    _box_to_json(segment) for segment in question.continuations
                ],
                "prompt": question.prompt,
                "confidence": question.confidence,
            }
            for question in region_map.questions
        ],
    }


def region_map_from_json(data: Any, *, source: str = "<memory>") -> RegionMap:
    """Rebuild a map from parsed JSON, clamping and refusing a future format."""
    if not isinstance(data, Mapping):
        raise RegionMapError(f"{source} is not a region map (expected a JSON object)")
    if str(data.get("format", "")) != REGION_MAP_FORMAT:
        raise RegionMapError(
            f"{source} is not an Opengrader region map "
            f"(its \"format\" is {data.get('format')!r}, expected "
            f"{REGION_MAP_FORMAT!r})"
        )
    try:
        version = int(data.get("version", 0))
    except (TypeError, ValueError):
        version = 0
    if version > REGION_MAP_VERSION:
        raise RegionMapError(
            f"{source} is a version {version} region map and this Opengrader "
            f"understands up to version {REGION_MAP_VERSION}. Upgrade Opengrader "
            "rather than editing the file — a newer format can describe regions "
            "this version would silently drop."
        )
    if version < 1:
        raise RegionMapError(f"{source} has no usable region-map version")

    source_block = data.get("source")
    if not isinstance(source_block, Mapping):
        raise RegionMapError(f"{source} has no \"source\" block naming its assignment")
    try:
        pages = int(source_block.get("pages", 0))
    except (TypeError, ValueError):
        pages = 0
    raw_size = source_block.get("page_size", (612.0, 792.0))
    try:
        page_size = (float(raw_size[0]), float(raw_size[1]))
    except (TypeError, ValueError, IndexError):
        page_size = (612.0, 792.0)

    fields = data.get("fields") if isinstance(data.get("fields"), Mapping) else {}
    raw_questions = data.get("questions")
    questions: list[MappedQuestion] = []
    for index, raw in enumerate(raw_questions if isinstance(raw_questions, list) else []):
        if not isinstance(raw, Mapping):
            continue
        question = _question_from_json(raw, index)
        if question is not None:
            questions.append(question)

    return RegionMap(
        source_name=str(source_block.get("filename", "")),
        source_sha256=str(source_block.get("sha256", "")),
        source_pages=max(0, pages),
        source_page_size=page_size,
        assignment_title=str(data.get("assignment_title", "")),
        name_field=_box_from_json(fields.get("name")),
        student_id_field=_box_from_json(fields.get("student_id")),
        title_field=_box_from_json(fields.get("title")),
        questions=tuple(questions),
        version=version,
        created_at=str(data.get("created_at", "")),
    )


def _question_from_json(raw: Mapping[str, Any], index: int) -> MappedQuestion | None:
    try:
        number = int(raw.get("number", index + 1))
    except (TypeError, ValueError):
        number = index + 1
    question_id = str(raw.get("id", "") or f"q{number}")
    try:
        bbox = clamp_bbox_norm([float(value) for value in raw.get("bbox", ())])
    except (TypeError, ValueError):
        return None
    try:
        page = max(1, int(raw.get("page", 1)))
    except (TypeError, ValueError):
        page = 1

    points_raw = raw.get("points")
    try:
        points = None if points_raw is None else float(points_raw)
    except (TypeError, ValueError):
        points = None

    confidence_raw = raw.get("confidence")
    try:
        confidence = None if confidence_raw is None else float(confidence_raw)
    except (TypeError, ValueError):
        confidence = None

    continuations = tuple(
        box
        for box in (
            _box_from_json(segment)
            for segment in (
                raw.get("continuations")
                if isinstance(raw.get("continuations"), list)
                else []
            )
        )
        if box is not None
    )

    question_type = str(raw.get("type", FREE_RESPONSE)) or FREE_RESPONSE
    return MappedQuestion(
        question_id=question_id,
        number=number,
        page=page,
        bbox=bbox,
        possible_points=points,
        question_type=question_type,  # type: ignore[arg-type]
        continuations=continuations,
        prompt=str(raw.get("prompt", "")),
        confidence=confidence,
    )


def save_region_map(region_map: RegionMap, path: str | Path) -> Path:
    """Write the map as indented JSON (it is meant to be diffable by hand)."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(region_map_to_json(region_map), indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def load_region_map(path: str | Path) -> RegionMap:
    """Read a map from disk. Raises :class:`RegionMapError` on anything unusable."""
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise RegionMapError(f"could not read region map {source}: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RegionMapError(f"{source} is not valid JSON: {exc}") from exc
    return region_map_from_json(data, source=str(source))


def load_verified_region_map(
    map_path: str | Path, pdf_path: str | Path | None = None
) -> RegionMap:
    """Load a map and, when a PDF is named, refuse it unless it matches.

    The one call site an extraction should use, so "loaded" and "checked against
    the assignment" cannot come apart.
    """
    region_map = load_region_map(map_path)
    if pdf_path is not None:
        verification = verify_region_map(region_map, pdf_path)
        if not verification:
            raise RegionMapError(verification.message)
    return region_map


# ==============================================================================
# VALIDATION
# ==============================================================================


def _overlap_ratio(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> float:
    """Intersection area as a fraction of the smaller box's area."""
    ax0, ay0, aw, ah = first
    bx0, by0, bw, bh = second
    ix0, iy0 = max(ax0, bx0), max(ay0, by0)
    ix1, iy1 = min(ax0 + aw, bx0 + bw), min(ay0 + ah, by0 + bh)
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    intersection = (ix1 - ix0) * (iy1 - iy0)
    smaller = min(aw * ah, bw * bh)
    return 0.0 if smaller <= 0 else intersection / smaller


def validate_region_map(
    region_map: RegionMap, *, page_count: int | None = None
) -> tuple[RegionMapFinding, ...]:
    """Everything wrong with a map, worst first.

    Runs on the in-memory value so the TUI editor can check a box the moment it
    is dragged, before anything is written. ``page_count`` defaults to the page
    count the map recorded for its assignment; pass the real PDF's when you have it.

    Severity is the contract the editor enforces: an ``error`` means extraction
    would produce wrong output (a box that crops nothing, two answers sharing an
    id, a page that does not exist), and saving is refused. Everything else is
    an observation an instructor is allowed to overrule.
    """
    pages = page_count if page_count is not None else region_map.source_pages
    findings: list[RegionMapFinding] = []

    if not region_map.questions:
        findings.append(
            RegionMapFinding(
                "no_questions",
                SEVERITY_ERROR,
                "No answer regions are mapped, so extraction would produce a "
                "submission with no questions. Add a region, or start from a "
                "grid.",
            )
        )

    seen_ids: dict[str, int] = {}
    numbers: list[int] = []
    for question in region_map.questions:
        qid = question.question_id.strip()
        if not qid:
            findings.append(
                RegionMapFinding(
                    "missing_question_id",
                    SEVERITY_ERROR,
                    f"A region on page {question.page} has no question id; the "
                    "crop it produces could not be matched to a rubric entry.",
                    page=question.page,
                )
            )
        seen_ids[qid] = seen_ids.get(qid, 0) + 1
        numbers.append(question.number)

        if question.number <= 0:
            findings.append(
                RegionMapFinding(
                    "bad_question_number",
                    SEVERITY_ERROR,
                    f"{qid or '(unnamed)'} has question number "
                    f"{question.number}; numbering starts at 1.",
                    question_id=qid or None,
                    page=question.page,
                )
            )

        if question.possible_points is None:
            findings.append(
                RegionMapFinding(
                    "missing_points",
                    SEVERITY_WARNING,
                    f"{qid} has no point value. Grading can still proceed (the "
                    "answer key may supply it), or seed them with "
                    "`region-map from-qa`.",
                    question_id=qid or None,
                    page=question.page,
                )
            )

        for index, segment in enumerate(question.segments):
            where = qid if index == 0 else f"{qid} continuation {index}"
            findings.extend(_validate_box(where, segment, pages, question_id=qid))

    for qid, count in seen_ids.items():
        if count > 1 and qid:
            findings.append(
                RegionMapFinding(
                    "duplicate_question_id",
                    SEVERITY_ERROR,
                    f"{qid} is used by {count} regions. Each crop is written to "
                    f"responses/{qid}.png, so all but one would be overwritten.",
                    question_id=qid,
                )
            )

    findings.extend(_validate_numbering(numbers))
    findings.extend(_validate_overlaps(region_map))

    if region_map.name_field is None:
        findings.append(
            RegionMapFinding(
                "missing_name_field",
                SEVERITY_WARNING,
                "No name region is mapped. Without it nothing reads the "
                "student's name off the paper, and every submission lands in "
                "the needs-review queue for manual identification.",
            )
        )
    else:
        findings.extend(
            _validate_box("name field", region_map.name_field, pages)
        )

    if region_map.student_id_field is None:
        findings.append(
            RegionMapFinding(
                "missing_id_field",
                SEVERITY_WARNING,
                "No student-id region is mapped. Roster matching then rests on "
                "the handwritten name alone, which is the weaker signal.",
            )
        )
    else:
        findings.extend(
            _validate_box("student id field", region_map.student_id_field, pages)
        )

    findings.sort(key=lambda finding: _SEVERITY_RANK.get(finding.severity, 9))
    return tuple(findings)


def _validate_box(
    where: str,
    box: RegionBox,
    page_count: int,
    *,
    question_id: str | None = None,
) -> list[RegionMapFinding]:
    findings: list[RegionMapFinding] = []
    x, y, w, h = box.bbox

    if page_count and (box.page < 1 or box.page > page_count):
        findings.append(
            RegionMapFinding(
                "page_out_of_range",
                SEVERITY_ERROR,
                f"{where} is on page {box.page}, but the assignment has {page_count} "
                "page(s). Nothing would be cropped for it.",
                question_id=question_id,
                page=box.page,
            )
        )

    if w <= 0 or h <= 0:
        findings.append(
            RegionMapFinding(
                "empty_region",
                SEVERITY_ERROR,
                f"{where} has no area (width {w:g}, height {h:g}). A box drawn "
                "backwards or entirely off the page collapses to this.",
                question_id=question_id,
                page=box.page,
            )
        )
    elif x + w > 1.0 + _OFF_PAGE_TOLERANCE or y + h > 1.0 + _OFF_PAGE_TOLERANCE:
        findings.append(
            RegionMapFinding(
                "region_off_page",
                SEVERITY_WARNING,
                f"{where} runs past the edge of the page and will be cropped "
                "short at the paper's edge.",
                question_id=question_id,
                page=box.page,
            )
        )
    elif x < -_OFF_PAGE_TOLERANCE or y < -_OFF_PAGE_TOLERANCE:
        findings.append(
            RegionMapFinding(
                "region_off_page",
                SEVERITY_WARNING,
                f"{where} starts before the edge of the page and will be "
                "cropped short.",
                question_id=question_id,
                page=box.page,
            )
        )
    return findings


def _validate_numbering(numbers: Sequence[int]) -> list[RegionMapFinding]:
    """Report gaps and repeats in the question numbering.

    A warning, never an error: an instructor mapping only the handwritten half
    of a mixed assignment, or leaving the extra-credit question out, is doing
    something ordinary. It is still worth saying, because the commonest cause is
    a question everyone forgot to draw a box around.
    """
    findings: list[RegionMapFinding] = []
    usable = sorted(number for number in numbers if number > 0)
    if not usable:
        return findings

    missing = [
        number
        for number in range(usable[0], usable[-1] + 1)
        if number not in set(usable)
    ]
    if missing:
        listed = ", ".join(str(number) for number in missing[:8])
        more = "" if len(missing) <= 8 else f" (+{len(missing) - 8} more)"
        findings.append(
            RegionMapFinding(
                "question_number_gap",
                SEVERITY_WARNING,
                f"Question number(s) {listed}{more} are not mapped. If those "
                "questions exist on the paper, their answers will not be "
                "extracted.",
            )
        )

    repeats = sorted({number for number in usable if usable.count(number) > 1})
    if repeats:
        findings.append(
            RegionMapFinding(
                "duplicate_question_number",
                SEVERITY_WARNING,
                "Question number(s) "
                + ", ".join(str(number) for number in repeats)
                + " are mapped more than once. Use continuation segments for one "
                "answer spread over two places.",
            )
        )
    return findings


def _validate_overlaps(region_map: RegionMap) -> list[RegionMapFinding]:
    """Report answer regions that substantially cover each other.

    Almost always a real mistake: the grader is handed question 4's crop with
    half of question 5's answer inside it, marks what they see, and the two
    questions cross-contaminate. Cheap enough to check pairwise — an assignment has
    tens of questions, not thousands.
    """
    findings: list[RegionMapFinding] = []
    flattened: list[tuple[str, RegionBox]] = []
    for question in region_map.questions:
        for index, segment in enumerate(question.segments):
            label = (
                question.question_id
                if index == 0
                else f"{question.question_id} cont.{index}"
            )
            flattened.append((label, segment))

    for first in range(len(flattened)):
        label_a, box_a = flattened[first]
        for second in range(first + 1, len(flattened)):
            label_b, box_b = flattened[second]
            if box_a.page != box_b.page:
                continue
            ratio = _overlap_ratio(box_a.bbox, box_b.bbox)
            if ratio >= _OVERLAP_REPORT_RATIO:
                findings.append(
                    RegionMapFinding(
                        "overlapping_regions",
                        SEVERITY_WARNING,
                        f"{label_a} and {label_b} overlap by {ratio * 100:.0f}% "
                        f"of the smaller box on page {box_a.page}; each crop "
                        "would contain part of the other answer.",
                        page=box_a.page,
                    )
                )
    return findings


def region_map_errors(
    findings: Iterable[RegionMapFinding],
) -> tuple[RegionMapFinding, ...]:
    """Just the error-level findings — what blocks a save or fails a check."""
    return tuple(finding for finding in findings if finding.is_error)


# ==============================================================================
# THE SEAM: A REGION MAP IS AN ASSIGNMENT TEMPLATE
# ==============================================================================


def region_map_to_blank_template(region_map: RegionMap) -> AssignmentTemplate:
    """Convert a region map into the structure extraction already consumes.

    This function is the entire integration: after it, ``--region-map`` and
    ``--blank-template`` are the same code path. ``exact_regions=True`` tells the
    processor these rectangles are the answer areas themselves rather than
    y-anchors to align against (see ``core/submissions/template.py``).

    Questions come out in question-number order, which is the order the grading
    TUI walks and the order a ``.submission.md`` lists them in.
    """
    questions = tuple(
        StudentSubmissionQuestion(
            question_id=question.question_id,
            prompt=question.prompt,
            possible_points=question.possible_points,
            response_image_path=None,
            response_text="",
            question_type=question.question_type,
            source_segments=tuple(
                QuestionSourceSegment(
                    page_number=segment.page, bbox_norm=segment.bbox
                )
                for segment in question.segments
            ),
        )
        for question in sorted(
            region_map.questions,
            key=lambda question: (question.number, question.page, question.bbox[1]),
        )
    )

    return AssignmentTemplate(
        assignment_title=(
            region_map.assignment_title
            or Path(region_map.source_name or "assignment").stem
        ),
        name_field=(
            FieldLocation(region_map.name_field.page, region_map.name_field.bbox)
            if region_map.name_field is not None
            else None
        ),
        student_id_field=(
            FieldLocation(
                region_map.student_id_field.page, region_map.student_id_field.bbox
            )
            if region_map.student_id_field is not None
            else None
        ),
        questions=questions,
        total_pages=region_map.source_pages,
        exact_regions=True,
    )


# ==============================================================================
# RENDERING — the same picture for the CLI preview and the TUI editor
# ==============================================================================


def _label_font(size: int):  # noqa: ANN202 - PIL font type is version-dependent
    """A readable label font, degrading to PIL's bitmap default.

    The bundled CMU face ships with Opengrader (it is what the generated
    templates print in), so a preview looks the same on a machine with no fonts
    installed. If it cannot be loaded the labels are still drawn, just smaller.
    """
    from PIL import ImageFont

    try:
        from opengrader.core.pdf.templates.fonts import CMU_BOLD_FILE

        if CMU_BOLD_FILE.exists():
            return ImageFont.truetype(str(CMU_BOLD_FILE), size)
    except Exception:
        pass
    return ImageFont.load_default()


def render_region_map_page(
    pdf_path: str | Path,
    region_map: RegionMap,
    page: int,
    *,
    dpi: int = PREVIEW_DPI,
    emphasis: tuple[float, float, float, float] | None = None,
    base: "tuple[Image.Image, int, int] | None" = None,
) -> "Image.Image":
    """Render one page of the blank assignment with its mapped regions outlined.

    The single most valuable thing this feature produces: it is how an
    instructor checks a map *before* extracting 200 students against it. Shared
    by ``region-map preview`` and the TUI editor precisely so that what the
    editor shows and what the preview PDF shows can never differ.

    ``emphasis`` outlines one box in a second colour — the editor's selection.
    It is matched by geometry rather than by index so callers need not agree on
    an ordering.

    ``base`` supplies an already-rendered ``(image, width, height)`` for this
    page. The interactive editor keeps one per page and passes it on every
    keystroke: rasterizing a PDF page takes tens of milliseconds, and doing it
    again for each arrow-key nudge is the difference between an editor that
    feels direct and one that feels broken. The page is never mutated — every
    overlay step composites onto a copy — so one cached render serves any number
    of redraws.
    """
    from PIL import ImageDraw

    from opengrader.core.pdf_utils import (
        bbox_norm_to_px,
        draw_page_overlays,
        render_pdf_page,
    )

    if base is not None:
        image, width, height = base
    else:
        image, width, height = render_pdf_page(str(pdf_path), page, dpi=dpi)
    labelled = region_map.boxes_on_page(page)

    highlights = [
        box.bbox for _label, box in labelled if emphasis is None or box.bbox != emphasis
    ]
    rendered = draw_page_overlays(image, width, height, highlights=highlights)

    draw = ImageDraw.Draw(rendered)
    line_width = max(2, round(min(width, height) / 300))
    font = _label_font(max(12, round(min(width, height) / 70)))

    if emphasis is not None:
        x0, y0, x1, y1 = bbox_norm_to_px(emphasis, width, height)
        # Drawn twice, one pixel apart, so the selection reads as a heavier
        # outline even in a half-block terminal where a one-cell line is all the
        # detail that survives.
        draw.rectangle([x0, y0, x1 - 1, y1 - 1], outline=EMPHASIS_RGB, width=line_width)
        draw.rectangle(
            [x0 + line_width, y0 + line_width, x1 - 1 - line_width, y1 - 1 - line_width],
            outline=EMPHASIS_RGB,
            width=max(1, line_width // 2),
        )

    for label, box in labelled:
        x0, y0, _x1, _y1 = bbox_norm_to_px(box.bbox, width, height)
        selected = emphasis is not None and box.bbox == emphasis
        colour = EMPHASIS_RGB if selected else (208, 135, 112)
        try:
            text_box = draw.textbbox((0, 0), label, font=font)
        except Exception:  # pragma: no cover - ancient PIL without textbbox
            text_box = (0, 0, 8 * len(label), 12)
        pad = line_width
        text_w = text_box[2] - text_box[0] + 2 * pad
        text_h = text_box[3] - text_box[1] + 2 * pad
        # The chip sits above the box where there is room, and inside it at the
        # top of the page, so a region at y=0 never has its label clipped away.
        chip_y = y0 - text_h if y0 - text_h >= 0 else y0
        draw.rectangle(
            [x0, chip_y, x0 + text_w, chip_y + text_h], fill=colour
        )
        draw.text((x0 + pad, chip_y + pad - text_box[1]), label, fill=(255, 255, 255), font=font)

    return rendered


def write_region_map_preview(
    pdf_path: str | Path,
    region_map: RegionMap,
    output: str | Path,
    *,
    dpi: int = PREVIEW_DPI,
) -> tuple[Path, ...]:
    """Write an outlined copy of the whole assignment — a PDF, or one PNG per page.

    A ``.pdf`` output is one file the instructor can flick through (and print,
    and email to a TA); any other extension writes ``<stem>-page-N.png``. The
    PDF is assembled from the same rendered pages, so the two are identical
    pictures in different wrappers.
    """
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)

    pages = [
        render_region_map_page(pdf_path, region_map, page, dpi=dpi)
        for page in range(1, max(1, region_map.source_pages) + 1)
    ]
    if not pages:
        return ()

    if destination.suffix.lower() == ".pdf":
        first, rest = pages[0], pages[1:]
        first.save(
            destination, "PDF", resolution=float(dpi), save_all=True, append_images=rest
        )
        return (destination,)

    written: list[Path] = []
    for index, image in enumerate(pages, start=1):
        target = destination.with_name(
            f"{destination.stem}-page-{index}{destination.suffix or '.png'}"
        )
        image.save(target)
        written.append(target)
    return tuple(written)

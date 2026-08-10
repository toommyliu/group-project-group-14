"""Bubble-sheet geometry and mark decisions — the pure half of the OMR path.

Opengrader's normal extraction path crops each answer box and reads the
handwriting inside it, one vision-model call per answer. That is the right
design for a written exam and the wrong one for a 200-seat lecture's multiple
choice: it is slow, it needs a model, and it is fuzzy about something that is
not fuzzy at all — a filled circle.

A bubble sheet removes the guessing. Because *we* generate the sheet, we know
exactly where every bubble is, so reading it is a measurement rather than a
recognition problem: sample the disc, compare the darkness. No OCR, no LLM, no
per-question crops to classify.

This module is the shared truth both halves depend on:

* :func:`build_bubble_sheet_layout` decides where every bubble goes, and is
  used **both** by the PDF generator (`core/pdf/templates/bubble_sheet.py`) and
  by the reader (`core/ocr/omr.py`). One definition, so a sheet can never be
  drawn to one geometry and read against another — the failure that would
  silently mis-score an entire cohort.
* :func:`decide_question_mark` turns measured fill fractions into an answer.
  It is a pure function over numbers, so the thresholds that decide "filled",
  "blank" and "the student marked two" are unit-testable with no image, no
  scanner and no PDF.

Coordinates are **normalized to the page** (0-1 on both axes), the same
convention as ``QuestionSourceSegment``, so nothing here depends on DPI, page
size or the units the caller happens to be working in. Radii are normalized to
the page *width* and converted by callers with the page aspect in hand, which
is what keeps a circle round on a non-square page.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field, replace
from typing import Literal, Sequence

# The answer a reader settled on for one question.
#
# ``ambiguous`` is deliberately its own outcome rather than a guess: two filled
# bubbles is a real thing students do, and picking the darker one would quietly
# invent an answer the student did not give. It flows out as an empty response
# with a low confidence, which lands it in the needs-review queue where a human
# looks at the actual sheet.
MarkStatus = Literal["marked", "blank", "ambiguous"]

DEFAULT_CHOICE_LABELS: tuple[str, ...] = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class BubbleSheetConfig:
    """Normalized page geometry for a generated answer sheet.

    Every value is a fraction of the page (width for horizontal measures and
    radii, height for vertical ones). The defaults are tuned for US Letter with
    five choices and fit 50 questions on one page in two columns, which covers
    the great majority of scantron-style exams in a single sheet.
    """

    choice_labels: tuple[str, ...] = DEFAULT_CHOICE_LABELS
    columns: int = 2

    # Vertical space reserved at the top of page 1 for the title block, the
    # name/id fields and the identity QR. Continuation pages need far less.
    first_page_top: float = 0.185
    continuation_top: float = 0.075
    bottom_margin: float = 0.055

    left_margin: float = 0.075
    right_margin: float = 0.075
    column_gap: float = 0.045

    row_pitch: float = 0.0265  # vertical distance between question rows
    label_width: float = 0.050  # room for the "12." row label, page-width units
    bubble_pitch: float = 0.032  # horizontal distance between bubble centers
    bubble_radius: float = 0.0092  # page-width units

    # Registration marks. Solid squares in the page corners give the reader
    # four known points to map layout space onto pixel space, which corrects
    # translation, scale, rotation and the keystone of a phone photo. They sit
    # outside the answer area so a stray pen stroke cannot land on one.
    fiducial_size: float = 0.024  # page-width units, the square's side
    fiducial_inset_x: float = 0.032
    fiducial_inset_y: float = 0.026

    def __post_init__(self) -> None:
        if self.columns < 1:
            raise ValueError("a bubble sheet needs at least one column")
        if not self.choice_labels:
            raise ValueError("a bubble sheet needs at least one choice label")
        if self.row_pitch <= 0 or self.bubble_pitch <= 0:
            raise ValueError("row and bubble pitch must be positive")

    @property
    def choice_count(self) -> int:
        return len(self.choice_labels)

    def column_width(self) -> float:
        """Normalized width of one question column, gaps excluded."""
        usable = 1.0 - self.left_margin - self.right_margin
        total_gap = self.column_gap * (self.columns - 1)
        return (usable - total_gap) / self.columns

    def row_width(self) -> float:
        """Normalized width one question's label + bubbles actually occupies."""
        return self.label_width + self.bubble_pitch * (self.choice_count - 1) + (
            2 * self.bubble_radius
        )


@dataclass(frozen=True)
class BubbleQuestion:
    """One question to place on the sheet.

    ``display_label`` is what gets printed beside the row ("12", "5a"); it
    defaults to the question id with a leading ``q`` stripped, which is how
    every other surface in the app humanizes an id.
    """

    question_id: str
    choice_labels: tuple[str, ...] = DEFAULT_CHOICE_LABELS
    display_label: str = ""
    # The question's point value, carried from the answer key. Not printed on
    # the sheet — a bubble sheet shows only circles — but travelling with the
    # layout means the extractor can fill in `Possible points` without the
    # caller having to open the QA file a second time.
    points: float | None = None

    def label(self) -> str:
        if self.display_label:
            return self.display_label
        stripped = self.question_id.strip()
        return stripped[1:] if stripped[:1].lower() == "q" else stripped


@dataclass(frozen=True)
class Bubble:
    """One answer circle, centred at ``(cx, cy)`` in normalized page space."""

    question_id: str
    choice_label: str
    page: int
    cx: float
    cy: float
    radius: float  # page-width units

    def bbox(self, aspect: float) -> tuple[float, float, float, float]:
        """Normalized ``(x, y, w, h)`` box around the circle.

        ``aspect`` is page width / page height: the radius is a width fraction,
        so the vertical extent has to be scaled by it or the box is an ellipse
        on anything but a square page.
        """
        rx = self.radius
        ry = self.radius * aspect
        return (self.cx - rx, self.cy - ry, 2 * rx, 2 * ry)


@dataclass(frozen=True)
class QuestionRow:
    """One question's printed row: its label and its choice bubbles."""

    question_id: str
    page: int
    label: str
    bubbles: tuple[Bubble, ...]
    # The row's own rectangle, normalized (x, y, w, h). Handed to the extractor
    # as the question's source segment, so the grading TUI shows the student's
    # actual marked row instead of a blank crop.
    bbox: tuple[float, float, float, float]
    points: float | None = None

    def choice_labels(self) -> tuple[str, ...]:
        return tuple(bubble.choice_label for bubble in self.bubbles)


@dataclass(frozen=True)
class CompanionQuestion:
    """An answer-key question the bubble sheet does not carry.

    A real exam is often a bubble sheet *plus* a written section. Those written
    questions have no row of circles, but they still belong in the submission:
    leaving them out produces a file whose questions don't match the answer
    key, and the grading TUI then refuses to save a grade for one ("question
    not found in submission"). Carrying them here means the extractor can emit
    an empty, un-captured block for each — which is exactly what an
    un-extracted question already looks like everywhere else in the app, and
    which the needs-review queue already flags as having no source region.
    """

    question_id: str
    points: float | None = None
    question_type: str = "free_response"


@dataclass(frozen=True)
class Fiducial:
    """A corner registration square, in normalized page space."""

    page: int
    corner: Literal["tl", "tr", "bl", "br"]
    cx: float
    cy: float
    size: float  # page-width units, the square's side


@dataclass(frozen=True)
class BubbleSheetLayout:
    """Everything the reader needs to turn a scanned sheet into a submission.

    Mostly geometry — where each bubble and registration mark sits — but it
    also carries the small amount of *exam* description the reader cannot get
    anywhere else (the title, each question's point value, and the answer-key
    questions the sheet does not cover). Those live here because the layout is
    the one object handed to both the generator and the reader, so anything
    stored elsewhere could disagree with the sheet that was actually printed.
    """

    page_count: int
    rows: tuple[QuestionRow, ...]
    fiducials: tuple[Fiducial, ...]
    config: BubbleSheetConfig = field(default_factory=BubbleSheetConfig)
    # What the sheet is titled. Part of the sheet's description rather than its
    # geometry, but it belongs here for the same reason `QuestionRow.points`
    # does: the reader needs the assignment's identity and the layout is the
    # one thing both the generator and the reader are handed.
    assignment_title: str = ""
    # Answer-key questions with no row on this sheet (a written section).
    companions: tuple[CompanionQuestion, ...] = ()
    # Every question id in answer-key order, bubbled or not. Lets the extractor
    # emit the submission's blocks in the order the exam asks them rather than
    # bubbles-first, which is what a grader stepping through expects.
    question_order: tuple[str, ...] = ()

    def rows_on_page(self, page: int) -> tuple[QuestionRow, ...]:
        return tuple(row for row in self.rows if row.page == page)

    def fiducials_on_page(self, page: int) -> tuple[Fiducial, ...]:
        return tuple(mark for mark in self.fiducials if mark.page == page)

    def row_for(self, question_id: str) -> QuestionRow | None:
        for row in self.rows:
            if row.question_id == question_id:
                return row
        return None

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(row.question_id for row in self.rows)


def rows_per_column(config: BubbleSheetConfig, *, first_page: bool) -> int:
    """How many question rows fit in one column of one page.

    Page 1 gives up vertical space to the title and identity block, so it holds
    fewer rows than a continuation page. Returning at least 1 keeps a
    pathological config (a huge header, a tiny page) from producing a layout
    with zero capacity and looping forever.
    """
    top = config.first_page_top if first_page else config.continuation_top
    usable = 1.0 - top - config.bottom_margin
    return max(1, int(usable / config.row_pitch))


def _fiducials_for_page(page: int, config: BubbleSheetConfig) -> tuple[Fiducial, ...]:
    size = config.fiducial_size
    left = config.fiducial_inset_x
    right = 1.0 - config.fiducial_inset_x
    top = config.fiducial_inset_y
    bottom = 1.0 - config.fiducial_inset_y
    return (
        Fiducial(page=page, corner="tl", cx=left, cy=top, size=size),
        Fiducial(page=page, corner="tr", cx=right, cy=top, size=size),
        Fiducial(page=page, corner="bl", cx=left, cy=bottom, size=size),
        Fiducial(page=page, corner="br", cx=right, cy=bottom, size=size),
    )


def build_bubble_sheet_layout(
    questions: Sequence[BubbleQuestion],
    *,
    config: BubbleSheetConfig | None = None,
    aspect: float = 612.0 / 792.0,
    assignment_title: str = "",
    companions: Sequence[CompanionQuestion] = (),
    question_order: Sequence[str] = (),
) -> BubbleSheetLayout:
    """Place every question's bubbles, filling columns then pages.

    Questions are laid out **column-major**: down the first column, then the
    second, then onto the next page. That is the order a student reads a
    scantron, and it keeps consecutive question numbers adjacent, which is what
    makes a mis-shifted answer visible to the person filling it in.

    ``aspect`` (page width / height) is only used to size each row's bounding
    box vertically; bubble centers are page-normalized and independent of it.
    """
    config = config or BubbleSheetConfig()
    if not questions:
        return BubbleSheetLayout(
            page_count=1,
            rows=(),
            fiducials=_fiducials_for_page(1, config),
            config=config,
            assignment_title=assignment_title,
            companions=tuple(companions),
            question_order=tuple(question_order),
        )

    column_width = config.column_width()
    if config.row_width() > column_width + 1e-9:
        raise ValueError(
            "bubble rows do not fit the configured column width — reduce "
            "bubble_pitch/label_width or use fewer columns"
        )

    rows: list[QuestionRow] = []
    fiducials: list[Fiducial] = []

    page = 1
    column = 0
    row_index = 0
    capacity = rows_per_column(config, first_page=True)
    fiducials.extend(_fiducials_for_page(page, config))

    for question in questions:
        if row_index >= capacity:
            row_index = 0
            column += 1
            if column >= config.columns:
                column = 0
                page += 1
                capacity = rows_per_column(config, first_page=False)
                fiducials.extend(_fiducials_for_page(page, config))

        top = config.first_page_top if page == 1 else config.continuation_top
        # Center the row inside its pitch band so the printed circle and the
        # sampled disc agree even when the pitch is loose.
        cy = top + (row_index + 0.5) * config.row_pitch
        column_x = config.left_margin + column * (column_width + config.column_gap)
        first_bubble_x = column_x + config.label_width + config.bubble_radius

        labels = question.choice_labels or config.choice_labels
        bubbles = tuple(
            Bubble(
                question_id=question.question_id,
                choice_label=label,
                page=page,
                cx=first_bubble_x + offset * config.bubble_pitch,
                cy=cy,
                radius=config.bubble_radius,
            )
            for offset, label in enumerate(labels)
        )

        row_half_height = (config.row_pitch / 2.0) * 0.92
        row_x1 = bubbles[-1].cx + config.bubble_radius * 1.6
        rows.append(
            QuestionRow(
                question_id=question.question_id,
                page=page,
                label=question.label(),
                bubbles=bubbles,
                points=question.points,
                bbox=(
                    column_x,
                    cy - row_half_height,
                    row_x1 - column_x,
                    row_half_height * 2.0,
                ),
            )
        )
        row_index += 1

    return BubbleSheetLayout(
        page_count=page,
        rows=tuple(rows),
        fiducials=tuple(fiducials),
        config=config,
        assignment_title=assignment_title,
        companions=tuple(companions),
        question_order=tuple(question_order),
    )


# ---------------------------------------------------------------------------
# Scrambled forms — a different question order on every sheet.
# ---------------------------------------------------------------------------
#
# Handing neighbouring students the same questions in the same order is the
# oldest copying opportunity there is, and a bubble sheet makes it worse: an
# answer is a *position*, so a glance sideways is enough.
#
# The hard part is not shuffling — it is reading the sheet back. A scrambled
# exam is only safe if, months later, a scan can still be mapped to the order
# that particular sheet was printed in. Two designs were possible:
#
#   1. Record every form's permutation in a sidecar file next to the PDF.
#   2. **Derive** the permutation from a short *form key* printed (and QR'd)
#      on the sheet itself.
#
# This module does (2). The permutation is a pure function of the question ids
# and the form key, so the sheet carries everything needed to read it: there is
# no map to lose, to get out of sync with a re-generated PDF, or to forget to
# copy alongside the scans. A lost sidecar would mean a cohort of exams that
# can never be graded — an unacceptable failure mode for a file-only tool.
#
# The derivation is deliberately **not** secret. Shuffling defends against a
# glance at the next desk, not against an adversary who has the answer key; a
# secret salt would buy nothing and would become one more thing to lose.

# Form keys are short so they fit a printed corner label and a QR field, but
# long enough that two sheets in a class never collide.
FORM_KEY_LENGTH = 6
_FORM_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no O/0, I/1 confusion


def form_key_for(seed: str, *, salt: str = "") -> str:
    """A short, stable form key derived from any seed string.

    Same seed ⇒ same key, on any machine and any run, which is what lets a
    re-generated batch stay readable against sheets already printed. The
    alphabet omits the character pairs a human miskeys (O/0, I/1) because this
    is printed on the sheet for a person to read back when a QR will not scan.
    """
    digest = hashlib.sha256(f"{salt}|{seed}".encode("utf-8")).digest()
    value = int.from_bytes(digest[:8], "big")
    characters = []
    for _ in range(FORM_KEY_LENGTH):
        value, index = divmod(value, len(_FORM_KEY_ALPHABET))
        characters.append(_FORM_KEY_ALPHABET[index])
    return "".join(characters)


def shuffle_questions(
    questions: Sequence[BubbleQuestion], form_key: str
) -> list[BubbleQuestion]:
    """Order ``questions`` for one form, and renumber them 1..N as printed.

    Deterministic in ``form_key`` — the same key always yields the same order,
    which is the entire basis for reading a scrambled sheet back. An empty key
    means "the unscrambled form" and returns the questions untouched, so the
    default path is bit-for-bit what it was before scrambling existed.

    Each question keeps its **real** ``question_id`` and only gains a printed
    ``display_label`` of its position on this sheet. That split is what makes
    scrambling invisible downstream: the row still knows it is ``q7`` while the
    student sees it as number 3, so the extracted submission, the answer key,
    auto-grading and the gradebook never learn that forms exist.

    ``random.Random`` is used for the permutation, not for anything security
    bearing — see the module note above on why the derivation is public.
    """
    ordered = list(questions)
    if not form_key:
        return ordered

    # Seed from the key *and* the question ids, so adding or removing a
    # question reshuffles rather than leaving most of the old order in place.
    fingerprint = "|".join(question.question_id for question in ordered)
    seed = hashlib.sha256(f"{form_key}|{fingerprint}".encode("utf-8")).digest()
    random.Random(int.from_bytes(seed, "big")).shuffle(ordered)

    return [
        replace(question, display_label=str(position))
        for position, question in enumerate(ordered, start=1)
    ]


# ---------------------------------------------------------------------------
# Mark decisions — pure functions over measured darkness.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MarkConfig:
    """Thresholds that turn measured fill fractions into an answer.

    Calibrated against rendered sheets with pencil-weight marks (see
    ``tests/unit/core/test_bubble_sheet.py`` and the generator's own round
    trip): an empty printed circle samples ~0.10-0.18 fill — the ring itself is
    ink — while a filled one samples above 0.55. ``min_fill`` sits between
    those, far enough from the empty case that a smudge or a scanner's grey
    cast cannot cross it.
    """

    # Below this a bubble counts as untouched. Well above the darkness of the
    # printed outline, which is present on *every* bubble.
    min_fill: float = 0.35
    # A runner-up this close to the winner means we cannot tell them apart. A
    # student who erased one answer and marked another typically leaves a
    # ghost around 0.3-0.5 of the real mark, so 0.75 accepts the erasure while
    # still catching two genuine marks.
    ambiguous_ratio: float = 0.75
    # Fill at which a mark is considered unmistakable; confidence saturates.
    confident_fill: float = 0.70


@dataclass(frozen=True)
class BubbleReading:
    """What the reader concluded for one question."""

    question_id: str
    status: MarkStatus
    choice_label: str = ""
    confidence: float = 0.0  # 0-100, comparable with OCR confidence
    fills: tuple[tuple[str, float], ...] = ()

    @property
    def response_text(self) -> str:
        """The text written into the submission's ``Response text`` field.

        Blank and ambiguous both produce an empty response on purpose: the
        student's answer is genuinely unknown, and an empty response is
        already the thing the review queue and the grader's screen know how to
        show. The confidence carries *why*.
        """
        return self.choice_label if self.status == "marked" else ""

    @property
    def detail(self) -> str:
        if self.status == "marked":
            return f"{self.choice_label} ({self.confidence:.0f}%)"
        if self.status == "ambiguous":
            marked = [label for label, fill in self.fills if fill >= MarkConfig().min_fill]
            return f"multiple marks: {', '.join(marked)}" if marked else "multiple marks"
        return "no mark"


def decide_question_mark(
    question_id: str,
    fills: Sequence[tuple[str, float]],
    *,
    config: MarkConfig | None = None,
) -> BubbleReading:
    """Decide one question's answer from its bubbles' measured fill fractions.

    ``fills`` is ``(choice_label, fill_fraction)`` per bubble, where the
    fraction is the share of the sampled disc that is ink (0-1). The rules, in
    order:

    1. Nothing reaches ``min_fill`` → ``blank``. The student skipped it.
    2. The runner-up is within ``ambiguous_ratio`` of the winner →
       ``ambiguous``. Two marks; refuse to choose.
    3. Otherwise the darkest bubble wins, with a confidence built from how
       filled it is *and* how far clear of the runner-up it sits — both matter,
       since a faint-but-unopposed mark and a dark-but-contested one are
       uncertain for different reasons.
    """
    config = config or MarkConfig()
    measured = tuple((str(label), float(fill)) for label, fill in fills)
    if not measured:
        return BubbleReading(question_id=question_id, status="blank", fills=())

    ranked = sorted(measured, key=lambda item: item[1], reverse=True)
    best_label, best_fill = ranked[0]
    runner_fill = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_fill < config.min_fill:
        # Confidence in a *blank* rises as the darkest bubble gets lighter: a
        # sheet with nothing on it is a confident blank, one with a faint smudge
        # just under the threshold is not.
        clearance = (config.min_fill - best_fill) / max(config.min_fill, 1e-6)
        return BubbleReading(
            question_id=question_id,
            status="blank",
            confidence=round(50.0 + 50.0 * min(1.0, clearance), 1),
            fills=measured,
        )

    if runner_fill >= config.ambiguous_ratio * best_fill:
        return BubbleReading(
            question_id=question_id,
            status="ambiguous",
            confidence=0.0,
            fills=measured,
        )

    fill_score = min(
        1.0,
        (best_fill - config.min_fill)
        / max(config.confident_fill - config.min_fill, 1e-6),
    )
    separation = 1.0 - (runner_fill / best_fill if best_fill > 0 else 0.0)
    # Weighted toward separation: an unambiguous choice is what actually makes
    # the reading trustworthy, while a lightly-but-clearly filled bubble is
    # still a perfectly readable answer.
    confidence = 100.0 * (0.4 * fill_score + 0.6 * min(1.0, separation / 0.6))
    return BubbleReading(
        question_id=question_id,
        status="marked",
        choice_label=best_label,
        confidence=round(max(0.0, min(100.0, confidence)), 1),
        fills=measured,
    )

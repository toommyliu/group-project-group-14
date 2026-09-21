from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field, replace
from typing import Literal, Sequence

MarkStatus = Literal["marked", "blank", "ambiguous"]

DEFAULT_CHOICE_LABELS: tuple[str, ...] = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class BubbleSheetConfig:
    choice_labels: tuple[str, ...] = DEFAULT_CHOICE_LABELS
    columns: int = 2

    first_page_top: float = 0.185
    continuation_top: float = 0.075
    bottom_margin: float = 0.055

    left_margin: float = 0.075
    right_margin: float = 0.075
    column_gap: float = 0.045

    row_pitch: float = 0.0265
    label_width: float = 0.050  # Page-width units.
    bubble_pitch: float = 0.032
    bubble_radius: float = 0.0092  # Page-width units.

    fiducial_size: float = 0.024  # Square side in page-width units.
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
        usable = 1.0 - self.left_margin - self.right_margin
        total_gap = self.column_gap * (self.columns - 1)
        return (usable - total_gap) / self.columns

    def row_width(self) -> float:
        return self.label_width + self.bubble_pitch * (self.choice_count - 1) + (
            2 * self.bubble_radius
        )


@dataclass(frozen=True)
class BubbleQuestion:
    question_id: str
    choice_labels: tuple[str, ...] = DEFAULT_CHOICE_LABELS
    display_label: str = ""
    points: float | None = None

    def label(self) -> str:
        if self.display_label:
            return self.display_label
        stripped = self.question_id.strip()
        return stripped[1:] if stripped[:1].lower() == "q" else stripped


@dataclass(frozen=True)
class Bubble:
    question_id: str
    choice_label: str
    page: int
    cx: float
    cy: float
    radius: float  # Page-width units.

    def bbox(self, aspect: float) -> tuple[float, float, float, float]:
        rx = self.radius
        ry = self.radius * aspect
        return (self.cx - rx, self.cy - ry, 2 * rx, 2 * ry)


@dataclass(frozen=True)
class QuestionRow:
    question_id: str
    page: int
    label: str
    bubbles: tuple[Bubble, ...]
    # Normalized (x, y, w, h).
    bbox: tuple[float, float, float, float]
    points: float | None = None

    def choice_labels(self) -> tuple[str, ...]:
        return tuple(bubble.choice_label for bubble in self.bubbles)


@dataclass(frozen=True)
class CompanionQuestion:
    question_id: str
    points: float | None = None
    question_type: str = "free_response"


@dataclass(frozen=True)
class Fiducial:
    page: int
    corner: Literal["tl", "tr", "bl", "br"]
    cx: float
    cy: float
    size: float  # Square side in page-width units.


@dataclass(frozen=True)
class BubbleSheetLayout:
    page_count: int
    rows: tuple[QuestionRow, ...]
    fiducials: tuple[Fiducial, ...]
    config: BubbleSheetConfig = field(default_factory=BubbleSheetConfig)
    assignment_title: str = ""
    companions: tuple[CompanionQuestion, ...] = ()
    question_order: tuple[str, ...] = ()

    def rows_on_page(self, page: int) -> tuple[QuestionRow, ...]:
        return tuple(row for row in self.rows if row.page == page)

    def fiducials_on_page(self, page: int) -> tuple[Fiducial, ...]:
        return tuple(mark for mark in self.fiducials if mark.page == page)

    @property
    def question_ids(self) -> tuple[str, ...]:
        return tuple(row.question_id for row in self.rows)


def rows_per_column(config: BubbleSheetConfig, *, first_page: bool) -> int:
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


FORM_KEY_LENGTH = 6
_FORM_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def form_key_for(seed: str, *, salt: str = "") -> str:
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
    """Preserves question IDs; only display labels change. An empty form key preserves order."""
    ordered = list(questions)
    if not form_key:
        return ordered

    fingerprint = "|".join(question.question_id for question in ordered)
    seed = hashlib.sha256(f"{form_key}|{fingerprint}".encode("utf-8")).digest()
    random.Random(int.from_bytes(seed, "big")).shuffle(ordered)

    return [
        replace(question, display_label=str(position))
        for position, question in enumerate(ordered, start=1)
    ]


@dataclass(frozen=True)
class MarkConfig:
    # Unmarked printed outlines must fall below this threshold.
    min_fill: float = 0.35
    # 0.75 tolerates erasure ghosts while detecting two filled marks.
    ambiguous_ratio: float = 0.75
    confident_fill: float = 0.70


@dataclass(frozen=True)
class BubbleReading:
    question_id: str
    status: MarkStatus
    choice_label: str = ""
    confidence: float = 0.0  # 0-100, on the OCR confidence scale.
    fills: tuple[tuple[str, float], ...] = ()

    @property
    def response_text(self) -> str:
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
    config = config or MarkConfig()
    measured = tuple((str(label), float(fill)) for label, fill in fills)
    if not measured:
        return BubbleReading(question_id=question_id, status="blank", fills=())

    ranked = sorted(measured, key=lambda item: item[1], reverse=True)
    best_label, best_fill = ranked[0]
    runner_fill = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_fill < config.min_fill:
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
    confidence = 100.0 * (0.4 * fill_score + 0.6 * min(1.0, separation / 0.6))
    return BubbleReading(
        question_id=question_id,
        status="marked",
        choice_label=best_label,
        confidence=round(max(0.0, min(100.0, confidence)), 1),
        fills=measured,
    )

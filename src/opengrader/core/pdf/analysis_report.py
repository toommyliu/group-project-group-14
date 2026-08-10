"""Render a cohort analysis report to a standalone PDF.

The instructor-facing companion to the per-student annotated PDFs: one document
summarizing how the *assignment* performed. It pairs the score distribution
(how the students did) with per-item psychometrics from
:mod:`core.analytics.item_analysis` (how each question did its job), so it can be
filed alongside a returned exam or kept for course assessment / accreditation.

Self-contained, like :mod:`core.pdf.annotations`: PyMuPDF base-14 fonts (no font
registration) plus a single matplotlib histogram rasterized to a PNG. Pure
w.r.t. grading — it only reads.
"""

from __future__ import annotations

import statistics as _stats
from dataclasses import dataclass
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING

from opengrader.core.analytics.item_analysis import (
    DifficultyBand,
    ItemAnalysisReport,
    OutcomeReport,
    SeparationBand,
)
from opengrader.core.pdf.text_fonts import load_text_fonts
from opengrader.core.question_types import humanize_question_label

if TYPE_CHECKING:  # pragma: no cover - typing only
    # fitz (PyMuPDF, ~110ms cold) is only needed once a report PDF is
    # actually rendered (see render_analysis_report_pdf/_draw_distribution
    # below) — this module is pulled in by the grade-screen data provider at
    # startup, well before any report is produced.
    import fitz

_PAGE_WIDTH = 612.0
_PAGE_HEIGHT = 792.0
_MARGIN = 54.0
# Bold is asked for as a flag, not a font name: this report prints text the
# instructor typed (the assignment title, outcome names, question labels), which
# can be in any script, so the actual font is only decided once the string is in
# hand — see `core/pdf/text_fonts.py` for why the built-in base-14 fonts cannot
# be used for that.
_FONT_REGULAR = False
_FONT_BOLD = True

# Column layout (x offsets from the left margin) for the per-item table.
_COL_QUESTION = 0.0
_COL_TYPE = 150.0
_COL_N = 250.0
_COL_DIFFICULTY = 300.0
_COL_SEPARATION = 420.0

# Column offsets for the learning-outcome table. Wider first column than the
# item table's: an outcome name is an instructor's own phrase ("Applies Bayes'
# rule"), not a short question id.
_COL_OUTCOME_QUESTIONS = 170.0
_COL_OUTCOME_WEIGHT = 300.0
_COL_OUTCOME_MEAN = 370.0
_COL_OUTCOME_MASTERED = 440.0

_BAND_LABELS = {
    DifficultyBand.VERY_EASY: "very easy",
    DifficultyBand.EASY: "easy",
    DifficultyBand.MODERATE: "moderate",
    DifficultyBand.HARD: "hard",
    DifficultyBand.VERY_HARD: "very hard",
    SeparationBand.EXCELLENT: "excellent",
    SeparationBand.GOOD: "good",
    SeparationBand.FAIR: "fair",
    SeparationBand.WEAK: "weak",
    SeparationBand.NEGATIVE: "negative",
    SeparationBand.INSUFFICIENT_DATA: "n/a",
}

_TYPE_LABELS = {
    "free_response": "Free response",
    "multiple_choice": "Multiple choice",
    "multiple_answer": "Multiple answer",
    "numeric": "Numeric",
}


@dataclass(frozen=True)
class ScoreDistribution:
    """The cohort's total-score spread, as percentages of each student's max."""

    percentages: tuple[float, ...]

    @property
    def count(self) -> int:
        return len(self.percentages)

    @property
    def mean(self) -> float | None:
        return _stats.fmean(self.percentages) if self.percentages else None

    @property
    def median(self) -> float | None:
        return _stats.median(self.percentages) if self.percentages else None

    @property
    def stdev(self) -> float | None:
        return _stats.pstdev(self.percentages) if len(self.percentages) >= 2 else None

    @property
    def minimum(self) -> float | None:
        return min(self.percentages) if self.percentages else None

    @property
    def maximum(self) -> float | None:
        return max(self.percentages) if self.percentages else None


def score_distribution_from_totals(
    totals: list[tuple[float, float]],
) -> ScoreDistribution:
    """Build a distribution from ``(awarded, possible)`` pairs, one per student.

    Students whose attainable total is zero (nothing graded yet) are skipped so
    they don't sink the percentage stats to zero.
    """
    percentages = [
        (awarded / possible) * 100.0
        for awarded, possible in totals
        if possible and possible > 0
    ]
    return ScoreDistribution(percentages=tuple(percentages))


def _histogram_png(distribution: ScoreDistribution) -> bytes | None:
    """Rasterize a score-percentage histogram to PNG bytes (Agg, headless)."""
    if distribution.count == 0:
        return None
    # Lazy import so the whole module doesn't pay matplotlib's import cost unless
    # an analysis PDF is actually produced.
    from matplotlib.figure import Figure

    figure = Figure(figsize=(6.6, 2.9), dpi=150)
    axes = figure.subplots()
    axes.hist(
        distribution.percentages,
        bins=[0, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        color="#5E81AC",
        edgecolor="white",
    )
    axes.set_xlabel("Score (%)")
    axes.set_ylabel("Students")
    axes.set_xlim(0, 100)
    axes.spines["top"].set_visible(False)
    axes.spines["right"].set_visible(False)
    figure.tight_layout()
    buffer = BytesIO()
    figure.savefig(buffer, format="png")
    return buffer.getvalue()


def _percent(value: float | None) -> str:
    return f"{value:.0f}%" if value is not None else "—"


def _one_decimal(value: float | None) -> str:
    return f"{value:.1f}" if value is not None else "—"


class _PageCursor:
    """A running write cursor that appends a fresh page when it runs out of room."""

    def __init__(self, document: fitz.Document) -> None:
        self._document = document
        self._page = document.new_page(-1, width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        self.y = _MARGIN
        self._fonts = load_text_fonts()

    @property
    def page(self) -> fitz.Page:
        return self._page

    @property
    def bottom(self) -> float:
        return _PAGE_HEIGHT - _MARGIN

    def ensure(self, needed: float) -> None:
        if self.y + needed > self.bottom:
            self._page = self._document.new_page(
                -1, width=_PAGE_WIDTH, height=_PAGE_HEIGHT
            )
            self.y = _MARGIN

    def write(
        self,
        text: str,
        *,
        font: bool = _FONT_REGULAR,
        size: float = 11.0,
        color: tuple[float, float, float] = (0.0, 0.0, 0.0),
        dx: float = 0.0,
        advance: bool = True,
    ) -> None:
        # Resolve a font that can actually draw this string, then declare it on
        # the page before use. Only the fonts a page really needs get declared,
        # so an all-ASCII report embeds nothing extra.
        drawable = self._fonts.choose(text, bold=font)
        fontfile = self._fonts.fontfile_for(drawable.fontname)
        if fontfile is None:
            self._page.insert_font(fontname=drawable.fontname)
        else:
            self._page.insert_font(fontname=drawable.fontname, fontfile=fontfile)
        self._page.insert_text(
            (_MARGIN + dx, self.y + size),
            drawable.text,
            fontname=drawable.fontname,
            fontsize=size,
            color=color,
        )
        if advance:
            self.y += size * 1.5

    def rule(self) -> None:
        self._page.draw_line(
            (_MARGIN, self.y),
            (_PAGE_WIDTH - _MARGIN, self.y),
            color=(0.7, 0.7, 0.7),
            width=0.8,
        )
        self.y += 10


def render_analysis_report_pdf(
    output_path: Path,
    *,
    report: ItemAnalysisReport,
    distribution: ScoreDistribution,
    title: str,
    generated_on: date | None = None,
    outcomes: OutcomeReport | None = None,
) -> Path:
    """Write the cohort analysis report PDF to ``output_path``.

    Sections: a header, a score-distribution summary with a histogram, and a
    per-item psychometrics table (question, type, N, difficulty, separation) with
    flagged items called out. Returns the written path.

    When ``outcomes`` is supplied and the answer key actually carries
    ``**Outcome**`` tags, a learning-outcome mastery section is added between
    the two: this is the page an accreditation review asks for, and it answers a
    different question from the item table ("did they learn it?" rather than
    "is this question any good?"). Omitted entirely when nothing is tagged, so
    an ordinary assignment's report is unchanged.
    """
    import fitz  # see the TYPE_CHECKING import above for why this is deferred

    generated_on = generated_on or date.today()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    document = fitz.open()
    try:
        cursor = _PageCursor(document)

        cursor.write("Item Analysis Report", font=_FONT_BOLD, size=18)
        cursor.y += 2
        cursor.write(title, size=12, color=(0.3, 0.3, 0.3))
        cursor.write(
            f"Class: {report.class_size} student(s)    "
            f"Generated: {generated_on.isoformat()}",
            size=10,
            color=(0.35, 0.35, 0.35),
        )
        cursor.y += 4
        cursor.rule()

        _draw_distribution(cursor, distribution)
        if outcomes is not None and outcomes.outcomes:
            _draw_outcomes_table(cursor, outcomes)
        _draw_items_table(cursor, report)

        document.save(output_path)
    finally:
        document.close()
    return output_path


def _draw_distribution(cursor: _PageCursor, distribution: ScoreDistribution) -> None:
    cursor.write("Score distribution", font=_FONT_BOLD, size=13)
    if distribution.count == 0:
        cursor.write(
            "No graded submissions yet — no distribution to show.",
            size=10,
            color=(0.4, 0.4, 0.4),
        )
        cursor.y += 6
        return

    cursor.write(
        "Mean {mean}   Median {median}   Std dev {std}   Range {lo}–{hi}".format(
            mean=_percent(distribution.mean),
            median=_percent(distribution.median),
            std=_one_decimal(distribution.stdev),
            lo=_percent(distribution.minimum),
            hi=_percent(distribution.maximum),
        ),
        size=10,
        color=(0.2, 0.2, 0.2),
    )

    png = _histogram_png(distribution)
    if png is not None:
        import fitz  # see render_analysis_report_pdf for why this is deferred

        image_height = 190.0
        cursor.ensure(image_height + 10)
        rect = fitz.Rect(
            _MARGIN,
            cursor.y,
            _PAGE_WIDTH - _MARGIN,
            cursor.y + image_height,
        )
        cursor.page.insert_image(rect, stream=png)
        cursor.y += image_height + 10


def _draw_outcomes_table(cursor: _PageCursor, report: OutcomeReport) -> None:
    """Per-outcome mastery: how many students met the bar, and on what evidence.

    Coverage is printed *beside* mastery on purpose. A 100% mastery rate on an
    outcome carried by 2 of 100 points is not evidence that the outcome was
    achieved, and separating the two numbers onto different pages is how that
    misreading happens.
    """
    cursor.ensure(70)
    cursor.write("Learning outcomes", font=_FONT_BOLD, size=13)
    cursor.write(
        f"Mastery bar: {report.mastery_threshold:.0%} of the points carrying "
        "each outcome.",
        size=10,
        color=(0.35, 0.35, 0.35),
    )
    tagged = report.tagged_share
    if tagged is not None and tagged < 1.0:
        # Stated before the table, not after it: a reader who stops at the
        # numbers must already know what fraction of the assignment they cover.
        cursor.write(
            f"{tagged:.0%} of this assignment's points carry an outcome tag; "
            f"the remaining {report.untagged_points:g} point(s) are untagged "
            "and counted in no outcome below.",
            size=9.5,
            color=(0.75, 0.45, 0.1),
        )
    cursor.y += 2

    def header() -> None:
        cursor.ensure(18)
        for text, dx in (
            ("Outcome", _COL_QUESTION),
            ("Questions", _COL_OUTCOME_QUESTIONS),
            ("Weight", _COL_OUTCOME_WEIGHT),
            ("Mean", _COL_OUTCOME_MEAN),
            ("Mastered", _COL_OUTCOME_MASTERED),
        ):
            cursor.write(text, font=_FONT_BOLD, size=9.5, dx=dx, advance=False)
        cursor.y += 9.5 * 1.5
        cursor.rule()

    header()
    for outcome in report.outcomes:
        cursor.ensure(16)
        questions = ", ".join(
            humanize_question_label(q).replace("Question ", "Q")
            for q in outcome.question_ids
        )
        rate = outcome.mastery_rate
        # Red when most of the class missed the bar — the one row an instructor
        # is meant to act on.
        row_color = (0.75, 0.2, 0.2) if rate is not None and rate < 0.5 else (0.0, 0.0, 0.0)

        cursor.write(
            outcome.outcome, size=9.5, dx=_COL_QUESTION, advance=False, color=row_color
        )
        cursor.write(
            questions or "-",
            size=9.5,
            dx=_COL_OUTCOME_QUESTIONS,
            advance=False,
            color=(0.25, 0.25, 0.25),
        )
        cursor.write(
            _percent(outcome.assignment_weight),
            size=9.5,
            dx=_COL_OUTCOME_WEIGHT,
            advance=False,
            color=(0.25, 0.25, 0.25),
        )
        cursor.write(
            _percent(outcome.mean_mastery),
            size=9.5,
            dx=_COL_OUTCOME_MEAN,
            advance=False,
            color=row_color,
        )
        cursor.write(
            f"{outcome.students_mastered}/{outcome.students_measured}"
            f"  ({_percent(rate)})",
            size=9.5,
            dx=_COL_OUTCOME_MASTERED,
            advance=False,
            color=row_color,
        )
        cursor.y += 9.5 * 1.45
    cursor.y += 6


def _draw_items_table(cursor: _PageCursor, report: ItemAnalysisReport) -> None:
    cursor.ensure(60)
    cursor.write("Per-question analysis", font=_FONT_BOLD, size=13)

    if not report.items:
        cursor.write(
            "No graded questions to analyze.", size=10, color=(0.4, 0.4, 0.4)
        )
        return

    if report.flagged_count:
        cursor.write(
            f"{report.flagged_count} item(s) flagged for review "
            "(negative or weak separation).",
            size=10,
            color=(0.75, 0.2, 0.2),
        )
        cursor.y += 2

    def header() -> None:
        cursor.ensure(18)
        for text, dx in (
            ("Question", _COL_QUESTION),
            ("Type", _COL_TYPE),
            ("N", _COL_N),
            ("Difficulty", _COL_DIFFICULTY),
            ("Separation", _COL_SEPARATION),
        ):
            cursor.write(text, font=_FONT_BOLD, size=9.5, dx=dx, advance=False)
        cursor.y += 9.5 * 1.5
        cursor.rule()

    header()
    current_version: str | None = ""  # sentinel distinct from None
    for item in report.items:
        if item.assignment_version != current_version:
            current_version = item.assignment_version
            if current_version:
                cursor.ensure(20)
                cursor.write(
                    f"Version {current_version}",
                    font=_FONT_BOLD,
                    size=10,
                    color=(0.3, 0.3, 0.3),
                )

        cursor.ensure(16)
        flagged = item.needs_attention
        label = humanize_question_label(item.question_id)
        if flagged:
            label = f"! {label}"
        row_color = (0.75, 0.2, 0.2) if flagged else (0.0, 0.0, 0.0)

        cursor.write(label, size=9.5, dx=_COL_QUESTION, advance=False, color=row_color)
        cursor.write(
            _TYPE_LABELS.get(item.question_type, item.question_type),
            size=9.5,
            dx=_COL_TYPE,
            advance=False,
            color=(0.25, 0.25, 0.25),
        )
        cursor.write(str(item.responses_count), size=9.5, dx=_COL_N, advance=False)

        if item.difficulty is not None:
            difficulty = f"{item.difficulty * 100:.0f}%"
            if item.difficulty_band is not None:
                difficulty += f" ({_BAND_LABELS[item.difficulty_band]})"
        else:
            difficulty = "—"
        cursor.write(difficulty, size=9.5, dx=_COL_DIFFICULTY, advance=False)

        if item.separation is not None:
            separation = f"{item.separation:+.2f} ({_BAND_LABELS[item.separation_band]})"
        else:
            separation = _BAND_LABELS[item.separation_band]
        cursor.write(
            separation,
            size=9.5,
            dx=_COL_SEPARATION,
            advance=False,
            color=row_color,
        )
        cursor.y += 9.5 * 1.6

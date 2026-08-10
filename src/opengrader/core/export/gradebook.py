"""Class gradebook table: one row per student, one column per question.

The pure core of the ``grades-export`` CLI, extracted so the return-bundle
engine and the CLI share exactly one definition of the gradebook layout (no
duplicated column logic). Read-only over the persisted ``.submission.md`` files;
curve-aware (a transparent, reversible overlay on the raw totals).
"""

from __future__ import annotations

import csv
import re
from pathlib import Path

from opengrader.core.analytics.item_analysis import outcome_sort_key
from opengrader.core.curve import Curve, apply_curve
from opengrader.core.submissions import StudentSubmissionDocument

_QUESTION_ID_RE = re.compile(r"^q(\d+[a-z]?)$", re.IGNORECASE)

# Characters a spreadsheet (Excel, Google Sheets, LibreOffice) treats as the
# start of a formula. A cell that begins with one of these is *executed* when the
# file is opened, so an OCR'd student name like ``=HYPERLINK("http://evil")`` or
# ``@SUM(...)`` becomes a live formula — the classic CSV-injection vector. Since
# student names/ids come from scanned handwriting (untrusted), we neutralize them
# on the way out. Tab and carriage return are included because a leading control
# char is another smuggling trick.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CSV_CONTROL_PREFIXES = ("\t", "\r")


def escape_csv_field(value: str) -> str:
    """Neutralize spreadsheet formula injection in a single CSV cell.

    Prefixes a value with a single quote (the widely-recognized "treat as text"
    escape) when it would otherwise be interpreted as a formula. A genuine signed
    number (``-5``, ``+3``) is left untouched so numeric columns stay numeric;
    only non-numeric formula-like text is escaped.
    """
    if not value:
        return value
    first = value[0]
    if first in _CSV_CONTROL_PREFIXES:
        return "'" + value
    if first in _CSV_FORMULA_PREFIXES:
        try:
            float(value)
        except ValueError:
            return "'" + value
    return value


def discover_submission_paths(path: Path) -> list[Path]:
    """Discover submission files, matching SubmissionFilesProvider's rules.

    Flat plus one directory level down — the single definition of "which files
    are this cohort's submissions" shared across every exporter.
    """
    if path.is_file():
        return [path]
    if path.is_dir():
        direct = sorted(path.glob("*.submission.md"))
        nested = sorted(path.glob("*/*.submission.md"))
        return direct + nested
    raise FileNotFoundError(f"submissions path not found: {path}")


def _question_label(question_id: str) -> str:
    """Compact column header for a question id ("q5a" -> "Q5a")."""
    match = _QUESTION_ID_RE.match(question_id.strip())
    return f"Q{match.group(1)}" if match else question_id


def _number(value: float | None) -> str:
    return f"{value:g}" if value is not None else ""


def total_awarded(doc: StudentSubmissionDocument) -> float:
    """One student's earned points — ungraded questions contribute nothing.

    Public because it is the *definition* of a student's score, not a detail of
    the CSV: the release snapshot and the course roll-up both read it from here
    so a grade can never mean one thing in the gradebook the student was handed
    and another somewhere else.
    """
    return sum(q.points_awarded for q in doc.questions if q.points_awarded is not None)


def total_possible(doc: StudentSubmissionDocument) -> float:
    """One student's available points (see :func:`total_awarded`)."""
    return sum(q.possible_points for q in doc.questions if q.possible_points is not None)


def ordered_question_ids(docs: list[StudentSubmissionDocument]) -> list[str]:
    """Union of question ids across all students, in first-seen order.

    Multi-version assignments can have different question sets per student, so a
    student who lacks a column gets a blank cell rather than dropping it.
    """
    seen: dict[str, None] = {}
    for doc in docs:
        for question in doc.questions:
            seen.setdefault(question.question_id, None)
    return list(seen)


def outcome_columns(
    outcomes_by_question: dict[str, tuple[str, ...]] | None,
) -> list[str]:
    """The outcome names to add as columns, in natural order.

    Empty when the answer key carries no ``**Outcome**`` tags, which is what
    keeps the gradebook byte-identical for the assignments that don't use them.
    """
    if not outcomes_by_question:
        return []
    names: set[str] = set()
    for outcomes in outcomes_by_question.values():
        names.update(outcomes)
    return sorted(names, key=outcome_sort_key)


def _student_outcome_percent(
    doc: StudentSubmissionDocument,
    outcome: str,
    outcomes_by_question: dict[str, tuple[str, ...]],
    possible_by_question: dict[str, float] | None,
) -> str:
    """One student's mastery of one outcome, as a percentage string.

    Blank when nothing carrying that outcome has been graded for this student —
    the same rule the rest of the gradebook uses for an ungraded question, and
    the reason a half-marked assignment can't report a false 0%.
    """
    earned = 0.0
    possible = 0.0
    for question in doc.questions:
        if outcome not in outcomes_by_question.get(question.question_id, ()):
            continue
        if question.points_awarded is None:
            continue
        points = question.possible_points
        if points is None and possible_by_question is not None:
            points = possible_by_question.get(question.question_id)
        if points is None:
            continue
        earned += question.points_awarded
        possible += float(points)
    if possible <= 0:
        return ""
    return f"{earned / possible * 100:.1f}"


def build_gradebook_rows(
    docs: list[StudentSubmissionDocument],
    curve: Curve = Curve(),
    *,
    outcomes_by_question: dict[str, tuple[str, ...]] | None = None,
    possible_points_by_question: dict[str, float] | None = None,
) -> list[list[str]]:
    """Build the full gradebook table (header + one row per student).

    Columns: ``student_id, student_name, <Q labels>, total[, curved], possible``.
    ``curve`` defaults to the inactive identity curve (no ``curved`` column);
    when active the raw ``total`` is kept and a ``curved`` column is added beside
    it, so nothing is hidden.

    When ``outcomes_by_question`` is supplied and non-empty, one
    ``outcome:<NAME> %`` column per learning outcome is appended **after** the
    totals — appended rather than interleaved so an existing import mapping into
    an LMS keeps working, and expressed as a **percentage** because an outcome's
    point pool differs from every other column's and a raw number would invite
    the wrong comparison. A student with nothing graded for an outcome gets a
    blank cell, exactly like an ungraded question.
    """
    question_ids = ordered_question_ids(docs)
    outcomes = outcome_columns(outcomes_by_question)
    header = ["student_id", "student_name", *[_question_label(q) for q in question_ids]]
    header += ["total", "possible"]
    if curve.is_active:
        header.insert(header.index("possible"), "curved")
    header += [f"outcome:{name} %" for name in outcomes]
    rows: list[list[str]] = [header]
    for doc in docs:
        awarded_by_id = {q.question_id: q.points_awarded for q in doc.questions}
        graded = {q.question_id for q in doc.questions if q.points_awarded is not None}
        cells = [doc.student_id, doc.student_name]
        for question_id in question_ids:
            if question_id in graded:
                cells.append(_number(awarded_by_id[question_id]))
            else:
                cells.append("")
        raw_total = total_awarded(doc)
        possible = total_possible(doc)
        cells.append(_number(raw_total))
        if curve.is_active:
            cells.append(_number(apply_curve(raw_total, possible, curve)))
        cells.append(_number(possible))
        for name in outcomes:
            cells.append(
                _student_outcome_percent(
                    doc,
                    name,
                    outcomes_by_question or {},
                    possible_points_by_question,
                )
            )
        rows.append(cells)
    return rows


def write_gradebook_rows(rows: list[list[str]], handle) -> None:
    """Serialize gradebook rows to an open text handle (stdout or a file).

    Every cell passes through :func:`escape_csv_field` first, so the single
    formula-injection defense covers both the file and stdout output paths.
    """
    csv.writer(handle).writerows(
        [escape_csv_field(cell) for cell in row] for row in rows
    )


def write_gradebook_csv(rows: list[list[str]], output_path: Path) -> None:
    """Write pre-built gradebook rows to ``output_path`` (creating parents)."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        write_gradebook_rows(rows, handle)

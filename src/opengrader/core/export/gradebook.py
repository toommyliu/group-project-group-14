from __future__ import annotations

import csv
import re
from pathlib import Path

from opengrader.core.analytics.item_analysis import outcome_sort_key
from opengrader.core.curve import Curve, apply_curve
from opengrader.core.submissions import StudentSubmissionDocument

_QUESTION_ID_RE = re.compile(r"^q(\d+[a-z]?)$", re.IGNORECASE)

# Spreadsheet apps execute cells starting with these characters.
_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")
_CSV_CONTROL_PREFIXES = ("\t", "\r")


def escape_csv_field(value: str) -> str:
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
    if path.is_file():
        return [path]
    if path.is_dir():
        direct = sorted(path.glob("*.submission.md"))
        nested = sorted(path.glob("*/*.submission.md"))
        return direct + nested
    raise FileNotFoundError(f"submissions path not found: {path}")


def _question_label(question_id: str) -> str:
    match = _QUESTION_ID_RE.match(question_id.strip())
    return f"Q{match.group(1)}" if match else question_id


def _number(value: float | None) -> str:
    return f"{value:g}" if value is not None else ""


def total_awarded(doc: StudentSubmissionDocument) -> float:
    return sum(q.points_awarded for q in doc.questions if q.points_awarded is not None)


def total_possible(doc: StudentSubmissionDocument) -> float:
    return sum(q.possible_points for q in doc.questions if q.possible_points is not None)


def ordered_question_ids(docs: list[StudentSubmissionDocument]) -> list[str]:
    seen: dict[str, None] = {}
    for doc in docs:
        for question in doc.questions:
            seen.setdefault(question.question_id, None)
    return list(seen)


def outcome_columns(
    outcomes_by_question: dict[str, tuple[str, ...]] | None,
) -> list[str]:
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
    """Columns: student_id, student_name, question scores, total, optional curved total,
    possible, then outcome percentages. Ungraded outcome cells are blank.
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
    csv.writer(handle).writerows(
        [escape_csv_field(cell) for cell in row] for row in rows
    )


def write_gradebook_csv(rows: list[list[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        write_gradebook_rows(rows, handle)

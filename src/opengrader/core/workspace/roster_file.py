"""Read a class folder's roster file, and change one student in it at a time.

A roster comes in one of two shapes, told apart by the file's extension:

* **tab-separated** (`.tsv`, `.txt`): no heading row, one student per line as
  `ID<TAB>NAME`. What the pipeline has always read.
* **comma-separated** (`.csv`): a heading row with First Name, Last Name and
  SID, and optionally Email. What the browser's People page has always
  imported. A learning-management system's export is read too — a Canvas
  gradebook's `Student` (`Last, First`) and `SIS User ID` columns — but a
  roster with one name column is not edited here (`_csv_layout` says why).

Both are read into the same `StudentDetails`, so classic's name matching and
the browser's roster work from one file whichever it is.

The editing functions take the file's text and return new text with a single
student added, changed or removed. Everything else — every other line, the
heading row, columns this module does not know, the line endings, a
byte-order mark — is returned exactly as it was: a roster is often kept in a
spreadsheet too, and saving one name must not reformat the rest. Nothing
here touches the disk; `folder_roster` decides when a file is written.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from opengrader.core.workspace.models import StudentDetails

__all__ = [
    "ROSTER_SUFFIXES",
    "RosterFormat",
    "RosterFile",
    "roster_format",
    "csv_has_roster_headings",
    "split_name",
    "parse_roster",
    "with_student_added",
    "with_student_changed",
    "with_student_removed",
]

RosterFormat = Literal["tsv", "csv"]

ROSTER_SUFFIXES = (".tsv", ".txt", ".csv")

_BOM = "﻿"
# The same three line endings the csv module recognises, so a line counted
# here is a line counted there.
_LINE = re.compile(r"[^\r\n]*(?:\r\n|\n|\r)|[^\r\n]+")


def roster_format(path: str | Path) -> RosterFormat:
    return "csv" if Path(path).suffix.lower() == ".csv" else "tsv"


def split_name(name: str) -> tuple[str, str]:
    """A single name as (first, last): the last word is the last name.

    Only for storing a one-column name in a two-column roster; the two are
    always shown joined again, so nothing a person sees depends on where the
    split falls.
    """
    words = name.split()
    if len(words) < 2:
        return " ".join(words), ""
    return " ".join(words[:-1]), words[-1]


@dataclass(frozen=True)
class RosterFile:
    """A roster file as read. `skipped` counts rows that are not a student
    with an ID — a blank ID, a name with no ID, a row the format rejects, or
    an ID that an earlier row already has."""

    format: RosterFormat
    students: tuple[StudentDetails, ...]
    has_email: bool = False
    skipped: int = 0


def _unbom(text: str) -> tuple[str, str]:
    return (_BOM, text[1:]) if text.startswith(_BOM) else ("", text)


def _newline(body: str) -> str:
    return "\r\n" if "\r\n" in body else "\n"


def _terminated(lines: list[str], newline: str) -> list[str]:
    # A last line with no line ending cannot have another appended after it.
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines = [*lines[:-1], lines[-1] + newline]
    return lines


# ---------------------------------------------------------------------------
# Tab-separated: ID<TAB>NAME
# ---------------------------------------------------------------------------


def _tsv_student(line: str) -> StudentDetails | None:
    parts = line.rstrip("\r\n").split("\t")
    if len(parts) < 2:
        return None
    first, last = split_name(parts[1])
    try:
        return StudentDetails(sid=parts[0], first_name=first, last_name=last)
    except ValidationError:
        return None


def _parse_tsv(body: str) -> RosterFile:
    students: dict[str, StudentDetails] = {}
    skipped = 0
    for line in _LINE.findall(body):
        if not line.strip():
            continue
        student = _tsv_student(line)
        if student is None or student.sid in students:
            skipped += 1
            continue
        students[student.sid] = student
    return RosterFile("tsv", tuple(students.values()), skipped=skipped)


def _tsv_index(lines: list[str], sid: str) -> int:
    for index, line in enumerate(lines):
        student = _tsv_student(line) if line.strip() else None
        if student is not None and student.sid == sid:
            return index
    raise LookupError(sid)


def _tsv_line(details: StudentDetails, previous: str | None, newline: str) -> str:
    ending = newline
    extra: list[str] = []
    if previous is not None:
        stripped = previous.rstrip("\r\n")
        ending = previous[len(stripped) :] or newline
        # Columns past the name belong to whoever added them.
        extra = stripped.split("\t")[2:]
    return "\t".join([details.sid, details.name, *extra]) + ending


# ---------------------------------------------------------------------------
# Comma-separated, with a heading row
# ---------------------------------------------------------------------------


def _columns(headings: list[str]) -> dict[str, int]:
    from opengrader.core.workspace.roster import _COLUMNS

    found: dict[str, int] = {}
    for index, heading in enumerate(headings):
        field = _COLUMNS.get(" ".join(heading.split()).casefold())
        if field is not None:
            found.setdefault(field, index)
    return found


def csv_has_roster_headings(text: str) -> bool:
    """Whether a `.csv`'s first row names the three columns a roster needs."""
    _, body = _unbom(text)
    try:
        headings = next(csv.reader(io.StringIO(body, newline="")), [])
    except csv.Error:
        return False
    from opengrader.core.workspace.roster import missing_roster_columns

    return not missing_roster_columns(_columns(headings))


@dataclass(frozen=True)
class _Record:
    cells: list[str]
    start: int  # index of its first line
    end: int  # one past its last line (a quoted value may span lines)


def _csv_records(body: str) -> tuple[list[str], list[_Record]]:
    lines = _LINE.findall(body)
    reader = csv.reader(io.StringIO(body, newline=""), strict=True)
    records: list[_Record] = []
    start = 0
    try:
        for cells in reader:
            records.append(_Record(cells, start, reader.line_num))
            start = reader.line_num
    except csv.Error as exc:
        raise ValueError(
            f"Row {len(records) + 1} of the roster has a quotation mark that "
            "is out of place or never closed."
        ) from exc
    return lines, records


def _parse_csv(body: str) -> RosterFile:
    from opengrader.core.workspace.roster import read_roster_csv

    rows, problems = read_roster_csv(body.encode("utf-8"))
    if not rows and any(problem.row is None for problem in problems):
        # No usable heading row (or an empty file): not a roster at all.
        if "no students" not in problems[0].message:
            raise ValueError(problems[0].message)
    students: dict[str, StudentDetails] = {}
    skipped = len({problem.row for problem in problems if problem.row is not None})
    for row in rows:
        if row.student.sid in students:
            skipped += 1
            continue
        students[row.student.sid] = row.student
    try:
        headings = next(csv.reader(io.StringIO(body, newline="")), [])
    except csv.Error:
        headings = []
    return RosterFile(
        "csv",
        tuple(students.values()),
        has_email="email" in _columns(headings),
        skipped=skipped,
    )


def _csv_row(
    details: StudentDetails,
    columns: dict[str, int],
    width: int,
    previous: list[str] | None,
    newline: str,
) -> str:
    cells = list(previous or [])
    cells += [""] * (width - len(cells))
    cells[columns["sid"]] = details.sid
    cells[columns["first_name"]] = details.first_name
    cells[columns["last_name"]] = details.last_name
    if "email" in columns:
        cells[columns["email"]] = details.email or ""
    out = io.StringIO(newline="")
    csv.writer(out, lineterminator=newline).writerow(cells)
    return out.getvalue()


def _csv_layout(body: str) -> tuple[list[str], list[_Record], dict[str, int]]:
    lines, records = _csv_records(body)
    if not records:
        raise ValueError("The roster file is empty.")
    columns = _columns(records[0].cells)
    if not {"first_name", "last_name", "sid"} <= set(columns):
        if "sid" in columns and "name" in columns:
            raise ValueError(
                "This roster keeps each name in one column (as a Canvas export "
                "does), so it is read here but not changed. Change it where it "
                "came from, or add First Name and Last Name columns to it."
            )
        raise ValueError(
            "The roster's first row needs First Name, Last Name, and SID "
            "column headings."
        )
    return lines, records, columns


def _csv_record(records: list[_Record], columns: dict[str, int], sid: str) -> _Record:
    index = columns["sid"]
    for record in records[1:]:
        if index < len(record.cells) and record.cells[index].strip() == sid:
            return record
    raise LookupError(sid)


# ---------------------------------------------------------------------------
# Either
# ---------------------------------------------------------------------------


def parse_roster(text: str, format: RosterFormat) -> RosterFile:
    """The students in a roster file's text.

    A `.csv` without the three heading columns raises `ValueError`: it is
    not a roster this can read, which is different from a roster with a bad
    row in it (counted in `skipped`).
    """
    _, body = _unbom(text)
    return _parse_csv(body) if format == "csv" else _parse_tsv(body)


def _refuse_email(details: StudentDetails, has_email: bool) -> None:
    if details.email and not has_email:
        raise ValueError(
            "This roster file has no Email column, so an email cannot be "
            "saved in it."
        )


def with_student_added(text: str, format: RosterFormat, details: StudentDetails) -> str:
    bom, body = _unbom(text)
    newline = _newline(body)
    if format == "tsv":
        _refuse_email(details, False)
        lines = _terminated(_LINE.findall(body), newline)
        return bom + "".join([*lines, _tsv_line(details, None, newline)])
    lines, records, columns = _csv_layout(body)
    _refuse_email(details, "email" in columns)
    row = _csv_row(details, columns, len(records[0].cells), None, newline)
    return bom + "".join([*_terminated(lines, newline), row])


def with_student_changed(
    text: str, format: RosterFormat, sid: str, details: StudentDetails
) -> str:
    """The text with the student whose ID is `sid` replaced by `details`.

    Raises `LookupError` when no row has that ID.
    """
    bom, body = _unbom(text)
    newline = _newline(body)
    if format == "tsv":
        _refuse_email(details, False)
        lines = _LINE.findall(body)
        index = _tsv_index(lines, sid)
        lines[index] = _tsv_line(details, lines[index], newline)
        return bom + "".join(lines)
    lines, records, columns = _csv_layout(body)
    _refuse_email(details, "email" in columns)
    record = _csv_record(records, columns, sid)
    row = _csv_row(details, columns, len(records[0].cells), record.cells, newline)
    if record.end == len(lines) and not lines[-1].endswith(("\n", "\r")):
        row = row[: -len(newline)]  # the file ended without a line ending; keep it so
    lines[record.start : record.end] = [row]
    return bom + "".join(lines)


def with_student_removed(text: str, format: RosterFormat, sid: str) -> str:
    bom, body = _unbom(text)
    if format == "tsv":
        lines = _LINE.findall(body)
        del lines[_tsv_index(lines, sid)]
        return bom + "".join(lines)
    lines, records, columns = _csv_layout(body)
    record = _csv_record(records, columns, sid)
    del lines[record.start : record.end]
    return bom + "".join(lines)

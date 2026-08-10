from __future__ import annotations

import csv
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path


def read_roster(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = {
                str(key).strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            if any(normalized.values()):
                rows.append(normalized)
        return rows


@dataclass(frozen=True)
class RosterStudent:
    """One roster row: the canonical id + name the pipeline matches against."""

    student_id: str
    name: str


def load_roster_students(
    path: str | Path, *, allow_missing_id: bool = False
) -> list[RosterStudent]:
    """Load the pipeline roster: a tab-separated ``ID<TAB>NAME`` line per student.

    This is the same ``.tsv``/``.ids`` format ``submission-extractor --id-file``
    consumes, kept here (a dependency-light leaf module) so both the extractor
    and the pure needs-review engine share one parser instead of two. Blank
    lines and rows without both fields are skipped; a UTF-8 BOM is tolerated.

    ``allow_missing_id`` (opt-in, off by default so existing strict consumers are
    unchanged) also keeps *name-only* rows — a line with no tab, or an empty id
    column — as a student with a blank ``student_id``. Those are the rows
    :func:`assign_student_ids` mints a deterministic 6-digit id for when
    generating personalized QR templates from a names-only roster.
    """
    students: list[RosterStudent] = []
    text = Path(path).read_text(encoding="utf-8-sig")
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 2:
            students.append(
                RosterStudent(student_id=parts[0].strip(), name=parts[1].strip())
            )
        elif allow_missing_id:
            # Name-only row (no tab): keep the name for deterministic id minting.
            name = parts[0].strip()
            if name:
                students.append(RosterStudent(student_id="", name=name))
    return students


# Generated ids occupy the full 6-digit space [100000, 999999] — always six
# digits (no leading-zero ambiguity when OCR'd or printed) and 900k distinct
# slots, ample for any class.
_ID_MIN = 100_000
_ID_SPAN = 900_000


def _derive_student_id(name: str) -> int:
    """Deterministically map a name to a starting 6-digit id.

    A stable SHA-256 hash (not Python's salted ``hash``) so the *same* roster
    always mints the *same* ids across runs and machines — the teacher's master
    key stays valid if templates are regenerated.
    """
    normalized = re.sub(r"\s+", " ", name.strip().lower())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return _ID_MIN + (int(digest, 16) % _ID_SPAN)


def assign_student_ids(students: list[RosterStudent]) -> list[RosterStudent]:
    """Return the roster with a stable 6-digit id filled in for every blank id.

    Rows that already carry an id keep it untouched. Blank-id rows get a
    deterministic id derived from the name; collisions (with an existing id or a
    previously minted one) are resolved by a deterministic linear probe within
    the 6-digit range, so the result is stable and every student ends up unique.
    """
    taken: set[str] = {
        s.student_id.strip() for s in students if s.student_id.strip()
    }
    resolved: list[RosterStudent] = []
    for student in students:
        existing = student.student_id.strip()
        if existing:
            resolved.append(RosterStudent(student_id=existing, name=student.name))
            continue
        start = _derive_student_id(student.name)
        candidate = start
        while str(candidate) in taken:
            candidate = _ID_MIN + ((candidate - _ID_MIN + 1) % _ID_SPAN)
            if candidate == start:  # full wrap — pathological, give up probing
                break
        new_id = str(candidate)
        taken.add(new_id)
        resolved.append(RosterStudent(student_id=new_id, name=student.name))
    return resolved

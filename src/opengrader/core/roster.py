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


def parse_roster_arg(value: str) -> tuple[Path, str | None]:
    """`TSV[=SECTION]` → (roster path, explicit section name or None).

    A path that exists is taken whole, so a filename containing `=` still works.
    """
    if Path(value).exists() or "=" not in value:
        return Path(value), None
    path_str, name = value.rsplit("=", 1)
    return Path(path_str), (name.strip() or None)


@dataclass(frozen=True)
class RosterStudent:
    student_id: str
    name: str


def load_roster_students(
    path: str | Path, *, allow_missing_id: bool = False
) -> list[RosterStudent]:
    """Accepts tab-separated ID/name rows and a UTF-8 BOM. allow_missing_id retains
    name-only rows; other incomplete rows are skipped.
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
            name = parts[0].strip()
            if name:
                students.append(RosterStudent(student_id="", name=name))
    return students


_ID_MIN = 100_000
_ID_SPAN = 900_000


def _derive_student_id(name: str) -> int:
    normalized = re.sub(r"\s+", " ", name.strip().lower())
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return _ID_MIN + (int(digest, 16) % _ID_SPAN)


def assign_student_ids(students: list[RosterStudent]) -> list[RosterStudent]:
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
            if candidate == start:
                break
        new_id = str(candidate)
        taken.add(new_id)
        resolved.append(RosterStudent(student_id=new_id, name=student.name))
    return resolved

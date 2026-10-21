"""Grades follow their student when the scan is split again.

Splitting a stack again (other settings, a fixed page count) can give every
file a different student: the graded `submission-0002.submission.md` may be
for the student whose pages are now `submission-0003.pdf`. Reading the new
`submission-0002.pdf` over that file would put the old student's grades on
the new student's answers, or, as before this module, drop them.

So when a graded file's PDF changed, the extractor reads the new PDF aside,
and once every file of the run is read:

1. `back_up` copies each displaced graded file into
   `.opengrader/displaced-grades/<when>/`, before anything is replaced;
2. `install_staged` puts the new readings in place;
3. `carry_grades` lays each displaced student's work (grades, typed-in
   readings, the name and ID a person set) on the one file that now names
   that student, by the same rule as `reread.same_student`.

A student it cannot place — no file names them, or two do, or the file that
does already holds someone's work — keeps their copy in the backup, and the
run says where. Placed students' copies are removed from it.

The split-fixing tools (`merge_split`) move grades between files on their
own; this is for a split that changed some other way.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .reread import has_teacher_work, keep_teacher_work, same_student
from .student_file import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
    set_submission_frontmatter_markdown,
)

BACKUP_DIRNAME = "displaced-grades"


@dataclass(frozen=True)
class Displaced:
    """A graded file whose PDF changed, as it was before the new reading."""

    stem: str
    markdown: str
    # None when the file could not be parsed: kept, but nobody can be found for it.
    document: StudentSubmissionDocument | None


@dataclass
class CarryReport:
    backup_dir: Path | None = None
    # (from stem, to stem) for every student whose grades found their file.
    carried: list[tuple[str, str]] = field(default_factory=list)
    unplaced: list[str] = field(default_factory=list)


def _write_atomically(path: Path, text: str) -> None:
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(text)
    os.replace(temporary, path)


def _who(document: StudentSubmissionDocument | None) -> str:
    if document is None:
        return "an unreadable file"
    name = (document.student_name or "").strip()
    student_id = (document.student_id or "").strip()
    if name and student_id:
        return f"{name} ({student_id})"
    return name or student_id or "an unnamed student"


def back_up(displaced: Sequence[Displaced], output_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    folder = output_dir / ".opengrader" / BACKUP_DIRNAME / stamp
    folder.mkdir(parents=True, exist_ok=True)
    for item in displaced:
        # Not *.submission.md: nothing that looks for submissions may find these.
        (folder / f"{item.stem}.md").write_text(item.markdown)
    return folder


def install_staged(
    staging_dir: Path,
    output_dir: Path,
    outputs: Iterable[str],
    *,
    source_pdf: str,
    markdown: str | None = None,
) -> None:
    """Move a reading made in `staging_dir` to the same place under
    `output_dir`. The .submission.md is replaced in one step, with
    `source_pdf` pointing at the scan from where it now is, and with
    `markdown` as its text when given."""
    for relative in outputs:
        source = staging_dir / relative
        if not source.exists():
            continue
        destination = output_dir / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.name.endswith(".submission.md"):
            text = markdown if markdown is not None else source.read_text()
            _write_atomically(
                destination, set_submission_frontmatter_markdown(text, source_pdf=source_pdf)
            )
        else:
            shutil.move(str(source), str(destination))


def carry_grades(
    displaced: Sequence[Displaced],
    candidates: Mapping[str, Path],
    fresh: Iterable[str],
    backup_dir: Path,
    *,
    emit: Callable[[str], None],
) -> CarryReport:
    """Put each displaced student's work on the file among `candidates`
    (stem to .submission.md) that now names them. `fresh` are the stems just
    read again, whose old work is in `displaced`: they hold nobody's work yet."""
    report = CarryReport(backup_dir=backup_dir)
    free = set(fresh)
    documents: dict[str, StudentSubmissionDocument] = {}
    for stem, path in candidates.items():
        try:
            documents[stem] = load_student_submission_markdown(path)
        except (OSError, ValueError):
            continue

    for item in displaced:
        who = _who(item.document)
        matches = (
            [stem for stem, document in documents.items() if same_student(item.document, document)]
            if item.document is not None
            else []
        )
        if len(matches) != 1:
            reason = "no file names them now" if not matches else f"{len(matches)} files name them"
            emit(f"  {who}'s grades from {item.stem}: {reason}; kept in the backup.")
            report.unplaced.append(item.stem)
            continue
        target = matches[0]
        if target not in free and has_teacher_work(documents[target]):
            emit(
                f"  {who}'s grades from {item.stem}: {target} names them but already holds "
                "someone's grades; kept in the backup."
            )
            report.unplaced.append(item.stem)
            continue

        path = candidates[target]
        kept = keep_teacher_work(item.document, documents[target], path.read_text())
        _write_atomically(path, kept.markdown)
        documents[target] = load_student_submission_markdown(path)
        free.discard(target)
        (backup_dir / f"{item.stem}.md").unlink(missing_ok=True)
        report.carried.append((item.stem, target))
        where = "stayed on" if target == item.stem else f"moved from {item.stem} to"
        emit(f"  {who}'s grades {where} {target}.")
        for warning in kept.warnings:
            emit(f"    {warning}")

    if not report.unplaced:
        shutil.rmtree(backup_dir, ignore_errors=True)
        report.backup_dir = None
    return report

"""Which papers are not matched to the class roster, and where the roster is.

A paper is *unmatched* when it has no student ID, or an ID that is not on the
roster: Canvas would not find that student, or the grade would go to someone
else. Classic mode, the pipeline and the release check all ask this one
module, so they agree on what needs a person to look.

The roster of a class folder is the one the pipeline card saved for it
(`core/pipeline_settings`), or else the single roster-shaped file in the
folder (`class_folder.find_roster`) — the same rule as the browser's People
page (`workspace/folder_roster.roster_source`, which builds on this).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from opengrader.core.roster import RosterStudent, load_roster_students, parse_roster_arg
from opengrader.core.submissions.student_file import StudentSubmissionDocument

__all__ = [
    "RosterCoverage",
    "class_roster_paths",
    "find_class_roster",
    "folder_roster_coverage",
    "is_unmatched",
    "load_class_roster",
    "roster_coverage",
    "roster_ids",
    "unmatched_papers",
]


def class_roster_paths(folder: str | Path) -> tuple[Path, ...]:
    """The roster file(s) a class folder uses: what the pipeline card saved
    (one per section, or one), else the one file that looks like a roster."""
    from opengrader.core.class_folder import find_roster
    from opengrader.core.pipeline_settings import load_folder_settings

    folder = Path(folder)
    saved = load_folder_settings(folder)
    if saved is not None and saved.use_section_rosters and saved.section_rosters:
        paths = []
        for entry in saved.section_rosters:
            path = parse_roster_arg(entry)[0]
            path = path if path.is_absolute() else folder / path
            if path.is_file():
                paths.append(path.resolve())
        if paths:
            return tuple(dict.fromkeys(paths))
    if saved is not None and saved.use_roster and saved.roster:
        path = Path(saved.roster)
        path = path if path.is_absolute() else folder / path
        if path.is_file():
            return (path.resolve(),)
    try:
        found = find_roster(folder)
    except OSError:
        found = None
    return (found.resolve(),) if found is not None else ()


def find_class_roster(
    submissions_path: str | Path, qa_path: str | Path | None = None
) -> tuple[Path, tuple[Path, ...]] | None:
    """The class folder holding these submissions and its roster file(s), or
    None when none of the folders it could be has a roster.

    The pipeline writes `<class folder>/submissions/`; a folder opened by
    hand may keep its papers anywhere, so the answer key's folder (when it
    and the submissions share one) is tried too.
    """
    submissions = Path(submissions_path).expanduser().resolve()
    candidates = [submissions.parent, submissions]
    if qa_path is not None:
        qa = Path(qa_path).expanduser().resolve()
        try:
            common = Path(os.path.commonpath([qa.parent, submissions]))
        except ValueError:  # different drives
            common = None
        if common is not None and common != Path(common.anchor):
            candidates.insert(0, common)
    for folder in dict.fromkeys(candidates):
        if not folder.is_dir():
            continue
        paths = class_roster_paths(folder)
        if paths:
            return folder, paths
    return None


def load_class_roster(paths: Iterable[Path]) -> list[RosterStudent] | None:
    """Every student in these roster files, or None when none can be read
    (a roster that cannot be read says nothing about who is on it)."""
    students: dict[str, RosterStudent] = {}
    read_any = False
    for path in paths:
        try:
            found = load_roster_students(path)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        read_any = True
        for student in found:
            students.setdefault(student.student_id.strip(), student)
    return list(students.values()) if read_any else None


def is_unmatched(student_id: str, roster_ids: "set[str] | frozenset[str]") -> bool:
    """A paper with this ID needs a person: no ID, or one not on the roster."""
    return student_id.strip().casefold() not in roster_ids


def roster_ids(roster: Iterable[RosterStudent]) -> frozenset[str]:
    """The roster's IDs as `is_unmatched` compares them."""
    return frozenset(
        key for student in roster if (key := student.student_id.strip().casefold())
    )


def unmatched_papers(
    documents: Sequence[StudentSubmissionDocument], roster: Sequence[RosterStudent]
) -> list[StudentSubmissionDocument]:
    """The papers with no student ID or one not on the roster. An empty
    roster matches nothing, so it flags nothing."""
    ids = roster_ids(roster)
    if not ids:
        return []
    return [document for document in documents if is_unmatched(document.student_id, ids)]


@dataclass(frozen=True)
class RosterCoverage:
    """How many papers there are and how many are not matched to the roster."""

    total: int
    count: int

    def summary(self) -> str:
        verb = "isn't" if self.count == 1 else "aren't"
        return f"{self.count} of {self.total} papers {verb} matched to the class roster."


def roster_coverage(
    documents: Sequence[StudentSubmissionDocument], roster: Sequence[RosterStudent]
) -> RosterCoverage:
    return RosterCoverage(len(documents), len(unmatched_papers(documents, roster)))


def folder_roster_coverage(
    submissions_path: str | Path, qa_path: str | Path | None = None
) -> RosterCoverage | None:
    """`roster_coverage` for a submissions folder on disk, with its class
    folder's roster; None when there is no roster or nothing to check."""
    from opengrader.core.export.gradebook import discover_submission_paths
    from opengrader.core.submissions.student_file import load_student_submission_markdown

    found = find_class_roster(submissions_path, qa_path)
    if found is None:
        return None
    roster = load_class_roster(found[1])
    if not roster:
        return None
    try:
        paths = discover_submission_paths(Path(submissions_path))
    except (OSError, FileNotFoundError):
        return None
    documents = []
    for path in paths:
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError):
            continue
    return roster_coverage(documents, roster) if documents else None

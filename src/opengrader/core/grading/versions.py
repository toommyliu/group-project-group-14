"""Which version of the answer key a paper is — or that it cannot be told.

An answer key with `## Version A` / `## Version B` sections has a different
key per version, so grading a Version B paper against Version A gives it a
wrong score that looks right. A paper's version comes from, in order:

1. what extraction read off the paper (`assignment_version`: the QR code,
   else the printed title), or what a person picked in the review queue;
2. a version marker the paper prints in its title or questions ("[B]", "(B)"),
   when exactly one of the key's versions appears.

When neither names one of the key's versions, the version is *unknown*.
Nothing then guesses: the auto-grader grades nothing on that paper, the
review queue lists it (`ReviewReason.VERSION_UNKNOWN`), and the grading app
refuses to save a grade on it until someone picks its version. (This used to
fall back to the first version silently.)

A key with no version sections, or with only one, has nothing to confuse.

Imports nothing from `core.submissions` at module scope, so the review and
solutions modules can use it without an import cycle; the document is only
read through its attributes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.qa_file import QADocument
    from opengrader.core.submissions import StudentSubmissionDocument

__all__ = ["VersionMatch", "VersionUnknownError", "match_version"]

_VERSION_MARKER_RE = re.compile(r"\[([A-Za-z0-9]+)\]|\(([A-Za-z0-9]+)\)")


class VersionUnknownError(ValueError):
    """A grade was refused because the paper's version is unknown."""


@dataclass(frozen=True)
class VersionMatch:
    # The key's own spelling of the version to grade against; None when the
    # key has no versions, or when this paper's version is unknown.
    version: str | None
    known: bool
    # What the paper itself said, as read ("" when nothing was read).
    read: str = ""
    # The key's versions, in its order.
    available: tuple[str, ...] = ()

    def describe(self) -> str:
        """Why the version is unknown, worded for the review queue."""
        versions = ", ".join(self.available)
        if self.read:
            return (
                f"The paper says version {self.read!r}, which is not in the "
                f"answer key ({versions}). Left ungraded: pick its version."
            )
        return (
            f"Which version this paper is could not be read (the answer key "
            f"has {versions}). Left ungraded: pick its version."
        )


def match_version(
    qa: "QADocument", document: "StudentSubmissionDocument"
) -> VersionMatch:
    versions = tuple(qa.version_questions)
    if not versions:
        return VersionMatch(version=None, known=True)
    if len(versions) == 1:
        return VersionMatch(version=versions[0], known=True, available=versions)

    by_name = {name.strip().lower(): name for name in versions}
    read = (document.assignment_version or "").strip()
    explicit = by_name.get(read.lower()) if read else None
    if explicit is not None:
        return VersionMatch(explicit, True, read=read, available=versions)

    printed = {
        by_name[raw.strip().lower()]
        for text in [document.assignment_title, *(q.prompt for q in document.questions)]
        for match in _VERSION_MARKER_RE.finditer(text or "")
        if (raw := match.group(1) or match.group(2)).strip().lower() in by_name
    }
    if len(printed) == 1:
        return VersionMatch(next(iter(printed)), True, read=read, available=versions)
    return VersionMatch(version=None, known=False, read=read, available=versions)

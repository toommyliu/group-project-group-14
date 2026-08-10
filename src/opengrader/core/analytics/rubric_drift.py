"""Who was graded under the *old* rubric?

A rubric edit made on the grade screen propagates: the QA file is the one answer
key, so changing an option changes the instrument every student's answer is
measured against — including the eighty already marked. That is usually the
point (an instructor adds a partial-credit option precisely because the existing
ones were wrong), and it is also the one fairness problem none of the existing
audits can see:

* the **consistency audit** compares students who gave the *same* answer, so two
  students who wrote different things and were graded under different rubrics
  never meet;
* the **grader agreement** report compares people, not versions of the rubric;
* the **review queue** reads only the persisted `.submission.md`, which records
  the grade but not when the rubric under it last moved.

The evidence has been on disk since 2026-07-20 and nothing read it: the audit
journal records every rubric edit with its timestamp, and every grade with
*its* timestamp. This module is that one comparison — a question's grades that
were recorded **before** its rubric last changed.

Three rules keep it from crying wolf:

1. **Only on evidence.** A student is flagged only where the journal actually
   holds a grade event for that answer, timestamped before the rubric change.
   Grading that predates journalling (or happened in a tool that did not
   journal) leaves no trace, and inventing a verdict from its absence would
   flag whole cohorts on the strength of nothing.

2. **A no-op edit is not a change.** Saving the rubric editor without altering
   anything appends an entry whose ``old`` and ``new`` option lists are equal.
   Treating that as a change would mark a cohort stale because somebody opened
   a dialog.

3. **The latest state is what counts.** A student regraded *after* the edit is
   not stale, however many times they were graded before it — so the comparison
   uses each student's most recent grade for that question, not their first.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from opengrader.core.history.journal import GradeJournalEntry
from opengrader.core.history.restore import student_key


@dataclass(frozen=True)
class RubricChange:
    """One recorded edit to a question's rubric that actually changed it."""

    question_id: str
    timestamp: str
    actor: str
    old_options: tuple[str, ...]
    new_options: tuple[str, ...]

    @property
    def added(self) -> tuple[str, ...]:
        return tuple(label for label in self.new_options if label not in self.old_options)

    @property
    def removed(self) -> tuple[str, ...]:
        return tuple(label for label in self.old_options if label not in self.new_options)

    def describe(self) -> str:
        """"added 'Partial - method', removed 'Incorrect'" — what moved."""
        parts: list[str] = []
        if self.added:
            parts.append("added " + ", ".join(repr(label) for label in self.added))
        if self.removed:
            parts.append("removed " + ", ".join(repr(label) for label in self.removed))
        if not parts:
            # Same labels, different points or ordering: the labels are all the
            # journal records, so say that rather than claiming nothing moved.
            return "rubric options changed"
        return " and ".join(parts)


@dataclass(frozen=True)
class StaleGrade:
    """One student's grade that predates the rubric it was awarded under."""

    student_key: str
    student_id: str
    student_name: str
    question_id: str
    graded_at: str
    graded_by: str
    change: RubricChange

    @property
    def label(self) -> str:
        return self.student_name or self.student_id or self.student_key


def _options(payload: dict) -> tuple[str, ...]:
    raw = payload.get("options")
    if isinstance(raw, (list, tuple)):
        return tuple(str(label) for label in raw)
    return ()


def find_rubric_changes(entries: Sequence[GradeJournalEntry]) -> dict[str, RubricChange]:
    """The last real rubric edit per question, keyed by question id.

    The *last* one, because a question edited three times has one current
    rubric and three "before"s: flagging against the earliest would report
    students who were regraded under edit two as stale when they are only stale
    with respect to edit three, which is what the single latest comparison
    already says.
    """
    changes: dict[str, RubricChange] = {}
    for entry in entries:
        if entry.kind != "rubric" or not entry.question_id:
            continue
        old_options = _options(entry.old)
        new_options = _options(entry.new)
        if old_options == new_options:
            continue  # Rule 2: a save that changed nothing is not a change.
        change = RubricChange(
            question_id=entry.question_id,
            timestamp=entry.timestamp,
            actor=entry.actor,
            old_options=old_options,
            new_options=new_options,
        )
        existing = changes.get(entry.question_id)
        if existing is None or change.timestamp >= existing.timestamp:
            changes[entry.question_id] = change
    return changes


def detect_rubric_drift(entries: Sequence[GradeJournalEntry]) -> list[StaleGrade]:
    """Every grade recorded before the rubric it was awarded under last changed.

    Pure: it reads the journal and nothing else. Ordering is total and stable
    (question, then student) so two runs produce the same list.

    ISO-8601 UTC timestamps compare correctly as strings — the journal writes
    them with ``datetime.isoformat()`` in UTC — so no parsing is needed and a
    malformed timestamp degrades to an ordinary string comparison rather than
    raising and taking the whole audit with it.
    """
    changes = find_rubric_changes(entries)
    if not changes:
        return []

    # Each student's *latest* grade per question (rule 3).
    latest_grade: dict[tuple[str, str], GradeJournalEntry] = {}
    for entry in entries:
        if entry.kind != "grade" or not entry.question_id:
            continue
        if entry.question_id not in changes:
            continue  # No rubric edit here, so nothing to be stale against.
        key = student_key(entry.student_id, entry.student_name)
        if not key:
            continue  # Unattributable: naming nobody helps nobody.
        previous = latest_grade.get((key, entry.question_id))
        if previous is None or entry.timestamp >= previous.timestamp:
            latest_grade[(key, entry.question_id)] = entry

    stale: list[StaleGrade] = []
    for (key, question_id), entry in latest_grade.items():
        change = changes[question_id]
        if entry.timestamp >= change.timestamp:
            continue  # Graded after the edit — measured by today's rubric.
        stale.append(
            StaleGrade(
                student_key=key,
                student_id=entry.student_id,
                student_name=entry.student_name,
                question_id=question_id,
                graded_at=entry.timestamp,
                graded_by=entry.actor,
                change=change,
            )
        )

    stale.sort(key=lambda item: (item.question_id, item.label.lower(), item.student_key))
    return stale

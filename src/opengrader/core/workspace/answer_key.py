"""Replace a graded assignment's answer key, carrying its grades along.

A workspace keeps each question — prompt, points, rubric — beside every
answer to it, so a grade can be checked and scored without parsing the answer
key again. That makes changing the key more than storing new text: every
stored answer has to take the new question, and a grade that names a rubric
item has to follow that item through a rename and be re-scored if its points
moved.

`update_assignment` (the Answer key page's Save) refuses once submissions
exist, because it accepts any edit at all. This module accepts only an edit
that keeps the same questions, which is what a rubric edit is, and so can be
allowed while grading is under way. It is used by a folder session, where
the answer key is a file that either the browser (`folder_session.edit_rubric`)
or the terminal app may have changed.

Points are never stored independently of the rubric: a grade made by
choosing rubric items is re-scored through `grade_points`, the same function
a save uses.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from opengrader.core.grading.edits import GradeEdit, grade_points
from opengrader.core.grading.questions import flatten_question_contexts
from opengrader.core.outline import question_structure
from opengrader.core.qa_file import QADocument, parse_qa_markdown
from opengrader.core.workspace.intake import context_revision, workspace_question
from opengrader.core.workspace.models import Answer, Question
from opengrader.core.workspace.store import (
    Workspace,
    WorkspaceConflict,
    WorkspaceNotFound,
    queue_feedback,
    record_event,
)

__all__ = [
    "AnswerKeyUpdate",
    "RubricItemInUse",
    "Relabel",
    "check_answer_key",
    "replace_answer_key",
]

# (version, question id) → {old rubric label: new rubric label}. The version
# is None for an answer key that has no versions.
Relabel = Mapping[tuple[str | None, str], Mapping[str, str]]


class RubricItemInUse(ValueError):
    """The new key cannot hold a grade that is already saved.

    Not a `WorkspaceConflict`: nothing changed underneath the editor, so
    reopening it would not help. The edit itself has to be different.
    """


@dataclass(frozen=True)
class AnswerKeyUpdate:
    """What replacing the key changed.

    `regraded` lists the (submission, question) cells whose labels or points
    changed; a folder session writes exactly those back into their files.
    """

    refreshed: int = 0
    regraded: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class _Change:
    submission_id: int
    question_id: str
    before: Answer
    after: Answer

    @property
    def regraded(self) -> bool:
        return (
            self.before.rubric_labels != self.after.rubric_labels
            or self.before.points_awarded != self.after.points_awarded
        )


def _questions(document: QADocument) -> dict[tuple[str | None, str], Question]:
    by_version = document.version_questions or {None: document.questions}
    return {
        (version, context.question_id): workspace_question(context)
        for version, questions in by_version.items()
        for context in flatten_question_contexts(questions)
    }


def _changes(
    db: sqlite3.Connection,
    assignment_id: str,
    source: str,
    relabel: Relabel,
    *,
    strict: bool,
) -> list[_Change]:
    row = db.execute(
        "SELECT source FROM assignments WHERE id=?", (assignment_id,)
    ).fetchone()
    if row is None:
        raise WorkspaceNotFound("Assignment not found.")
    document = parse_qa_markdown(source)
    if question_structure(parse_qa_markdown(row[0])) != question_structure(document):
        raise WorkspaceConflict(
            "The answer key now has different questions. Questions cannot be "
            "added, removed, renumbered or reordered once submissions are "
            "being graded."
        )
    questions = _questions(document)
    changes: list[_Change] = []
    in_use: dict[str, int] = {}
    for stored in db.execute(
        """SELECT a.submission_id, a.question_id, a.value, s.version
           FROM answers a JOIN submissions s
             ON s.assignment_id = a.assignment_id AND s.id = a.submission_id
           WHERE a.assignment_id=? ORDER BY a.submission_id, a.rowid""",
        (assignment_id,),
    ):
        question = questions.get((stored["version"], stored["question_id"]))
        if question is None:
            continue
        before = Answer.model_validate_json(stored["value"])
        if before.question == question:
            continue
        renames = relabel.get((stored["version"], stored["question_id"]), {})
        labels = tuple(renames.get(label, label) for label in before.rubric_labels)
        offered = {option.label for option in question.rubric}
        missing = [label for label in labels if label not in offered]
        points = before.points_awarded
        if missing:
            # The grade selects an item the new rubric does not have. A
            # browser edit is refused for it below. A key changed in the
            # file is taken as it is: the terminal app has already rewritten
            # the grades in the submission files, and the sync that follows
            # brings those in.
            for label in missing:
                in_use[label] = in_use.get(label, 0) + 1
        elif before.score_mode == "rubric" and labels:
            try:
                points = grade_points(
                    question,
                    GradeEdit(
                        request_id=uuid4(),
                        expected_context_revision="",
                        expected_revision="",
                        rubric_labels=list(labels),
                        analysis=before.analysis,
                    ),
                )
            except ValueError as exc:
                if strict:
                    raise RubricItemInUse(
                        f"Submission {stored['submission_id']} has a grade on "
                        f"question {stored['question_id']} that the new rubric "
                        f"does not allow: {exc}"
                    ) from exc
        after = before.model_copy(
            update={
                "question": question,
                "context_revision": context_revision(question, before.response_text),
                # Bumped even when the grade is untouched, so a grader with
                # this answer open is shown the new rubric.
                "revision": str(int(before.revision) + 1),
                "rubric_labels": labels,
                "points_awarded": points,
            }
        )
        changes.append(
            _Change(stored["submission_id"], stored["question_id"], before, after)
        )
    if strict and in_use:
        label, count = max(in_use.items(), key=lambda item: item[1])
        raise RubricItemInUse(
            f"{count} saved grade{'' if count == 1 else 's'} "
            f"use{'s' if count == 1 else ''} the rubric item “{label}”, so it "
            "cannot be removed. Rename it instead, or change those grades first."
        )
    return changes


def check_answer_key(
    workspace: Workspace,
    assignment_id: str,
    source: str,
    *,
    relabel: Relabel | None = None,
) -> None:
    """Raise what `replace_answer_key(strict=True)` would, changing nothing.

    For a caller that has to do something irreversible first — write the
    answer-key file — and wants the refusal before it, not after.
    """
    with workspace.connect() as db:
        _changes(db, assignment_id, source, relabel or {}, strict=True)


def replace_answer_key(
    workspace: Workspace,
    assignment_id: str,
    source: str,
    *,
    relabel: Relabel | None = None,
    strict: bool = True,
    actor: str | None = None,
) -> AnswerKeyUpdate:
    """Store `source` as the answer key and bring every stored answer up to it.

    `relabel` says which rubric items were renamed, so grades follow them.
    With `strict` (an edit somebody is making now) a grade the new rubric
    cannot hold refuses the whole change; without it (a key that already
    changed on disk) such a grade is left as it is for the sync to settle.
    Everything commits together or not at all.
    """
    title = parse_qa_markdown(source).title or "Untitled assignment"
    with workspace.connect(write=True) as db:
        changes = _changes(db, assignment_id, source, relabel or {}, strict=strict)
        actor = actor or db.execute("SELECT actor FROM workspace").fetchone()[0]
        now = datetime.now(timezone.utc).isoformat()
        for change in changes:
            db.execute(
                "UPDATE answers SET value=? WHERE assignment_id=? AND submission_id=? AND question_id=?",
                (
                    change.after.model_dump_json(),
                    assignment_id,
                    change.submission_id,
                    change.question_id,
                ),
            )
            if not change.regraded:
                continue
            db.execute(
                "INSERT INTO history (assignment_id, submission_id, question_id, actor, timestamp, before, after) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    assignment_id,
                    change.submission_id,
                    change.question_id,
                    actor,
                    now,
                    change.before.model_dump_json(),
                    change.after.model_dump_json(),
                ),
            )
            record_event(
                db,
                assignment_id,
                kind="grade",
                submission_id=change.submission_id,
                question_id=change.question_id,
                revision=change.after.revision,
            )
            queue_feedback(db, assignment_id, change.submission_id)
        db.execute(
            "UPDATE assignments SET source=?, title=? WHERE id=?",
            (source, title, assignment_id),
        )
        record_event(db, assignment_id, kind="assignment")
    return AnswerKeyUpdate(
        refreshed=len(changes),
        regraded=tuple(
            (change.submission_id, change.question_id)
            for change in changes
            if change.regraded
        ),
    )

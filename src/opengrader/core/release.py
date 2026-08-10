"""Grade release: handing an assignment back, as a recorded event.

The pipeline has a pre-flight at the *input* end — `submissions/scan_check.py`
refuses to let a rotated stack become a cohort of zeros — and until now nothing
at all at the *output* end, which is the more irreversible of the two. Once
grades are handed back, a student has seen a number; changing it afterwards is a
conversation, not an edit. Yet the app could not answer either half of that:

* **Is this ready to hand back?** The signals existed but were scattered across
  four commands (`review-queue`, `claim status`, `regrade list`, `curve show`),
  so "have I actually finished?" was a question an instructor answered from
  memory.
* **What did the student actually see?** Nothing recorded it. After a regrade,
  a curve, or a merge, there was no way to say "Q3 went from 4 to 6 since you
  got this back" — the audit journal knows every change but not which of them
  happened on the student's side of the handover.

Two capabilities, deliberately in that order:

* **Readiness** (:func:`build_readiness_report`) — a pre-flight over the cohort
  that classifies what stands between it and being handed back. It *aggregates*
  the review queue rather than re-deriving it: `submissions/review.py` is the
  one definition of "this submission looks wrong", and a second copy of those
  checks is exactly how two parts of the app start disagreeing about whether an
  id is malformed. What is new here is the *verdict* — which anomalies make a
  release wrong (blocking) rather than merely worth a look.
* **Release** (:func:`build_release`) — a snapshot of every student's grade,
  per question, at the moment they were handed back, written to
  ``<submissions>/.opengrader/releases/<release-id>.json``. Per-question and not
  just the total, because the entire value on the far side is naming the
  question: a dispute about "my grade changed" is unanswerable from two totals.

Three decisions carry the feature:

* **A release records; it never locks.** Nothing here refuses a later grade
  change, and no other module consults a release before writing. That is the
  same stance `policy.py` and `claims.py` take — coordination for a team, not a
  permission system — and it matters more here than there: an assignment is
  released *because* regrades are expected, so a release that froze grades would
  break the one workflow it exists to serve. What it buys instead is
  :func:`compare_release`, which says what moved.
* **Students are matched by the journal's own key** (`history/restore.py`'s
  `student_key`), not by list position or filename. A release outlives
  re-extraction, an id assigned later, and a folder that has been merged; the
  key is what the audit trail already uses to mean "this person", so a release
  comparison and a restore agree about who is who.
* **Totals come from `export/gradebook.py`.** A release stores the number the
  student was handed, curve included, and it must be built by exactly the code
  that built their gradebook row — a second scoring path would eventually
  disagree, and it would surface as a grade dispute.

Everything down to :func:`compare_release` is pure, so the whole verdict layer
unit-tests with no folder; only the functions below the I/O divider touch a disk.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterable, Literal, Sequence

from opengrader.core.claims import WorkClaim
from opengrader.core.curve import Curve, apply_curve, describe_curve
from opengrader.core.export.gradebook import total_awarded, total_possible
from opengrader.core.history.journal import current_actor
from opengrader.core.history.restore import document_student_key
from opengrader.core.submissions.consistency import ConsistencyFlag, ConsistencyKind
from opengrader.core.submissions.review import ReviewFlag, ReviewReason
from opengrader.core.submissions.student_file import StudentSubmissionDocument

RELEASE_DIRNAME = ".opengrader"
RELEASE_SUBDIR = "releases"

RELEASE_FORMAT = 1

# How many students an issue names before it stops listing and starts counting.
# A readiness report is read to decide one thing; a wall of ids buries the
# decision, and the queue that lists them all is one keypress away.
MAX_ISSUE_EXAMPLES = 3

# Point differences below this are float noise, not a grade change. Matches the
# epsilon the scoring module uses to compare awarded points.
POINT_EPSILON = 1e-6


class ReleaseError(ValueError):
    """A release could not be read or written."""


# --------------------------------------------------------------------------- #
# Readiness — is this cohort ready to hand back?
# --------------------------------------------------------------------------- #

# Deliberately *not* the review queue's high/medium/low. Those rank how much a
# flag deserves a human's attention; these answer a different question — would
# releasing right now be **wrong** (blocking), worth knowing about (warning), or
# simply context the instructor should have (note). A malformed student id is
# only "medium" to a grader and is absolutely blocking to a release, and
# collapsing the two scales would lose exactly that distinction.
ReadinessSeverity = Literal["blocking", "warning", "note"]

_SEVERITY_RANK: dict[ReadinessSeverity, int] = {"blocking": 0, "warning": 1, "note": 2}


class ReadinessKind(str, Enum):
    """What stands between a cohort and being handed back."""

    # Blocking: releasing now would hand back something known to be wrong.
    UNGRADED_ANSWERS = "ungraded_answers"
    UNRESOLVED_IDENTITY = "unresolved_identity"

    # Warning: releasing is defensible, but a person should have decided so.
    UNSURE_IDENTITY = "unsure_identity"
    UNREADABLE_RESPONSES = "unreadable_responses"
    UNCONFIRMED_AI_GRADES = "unconfirmed_ai_grades"
    INCONSISTENT_GRADING = "inconsistent_grading"
    OPEN_REGRADE_REQUESTS = "open_regrade_requests"
    MISSING_SUBMISSIONS = "missing_submissions"
    STALE_RUBRIC_GRADES = "stale_rubric_grades"
    CHANGED_SINCE_RELEASE = "changed_since_release"

    # Note: context that changes how the numbers should be read.
    HELD_WORK_CLAIMS = "held_work_claims"
    CURVE_ACTIVE = "curve_active"


@dataclass(frozen=True)
class ReadinessIssue:
    """One thing standing between this cohort and a hand-back.

    ``count`` is the number of *things* (answers, students, disputes) — the unit
    is whatever the summary says it is. ``examples`` names a few student ids so
    the reader can start somewhere; it is ids and never names, so a readiness
    report is safe to show during anonymous grading.

    ``example_total`` is how many **students** those examples were drawn from,
    which is deliberately a separate number from ``count``: "22 answers" and
    "2 students" are both true of one issue, and computing the "and N more"
    tail from the answer count would tell the reader there are 20 more students
    when there are none.
    """

    kind: ReadinessKind
    severity: ReadinessSeverity
    summary: str
    detail: str = ""
    count: int = 0
    examples: tuple[str, ...] = ()
    example_total: int = 0

    @property
    def is_blocking(self) -> bool:
        return self.severity == "blocking"

    def describe(self) -> str:
        parts = [self.summary]
        if self.detail:
            parts.append(self.detail)
        if self.examples:
            shown = ", ".join(self.examples)
            remaining = self.example_total - len(self.examples)
            parts.append(
                f"e.g. {shown}" + (f" and {remaining} more" if remaining > 0 else "")
            )
        return " ".join(parts)


@dataclass(frozen=True)
class ReadinessReport:
    """Everything standing between this cohort and a hand-back, worst first."""

    student_count: int
    graded_answers: int
    total_answers: int
    issues: tuple[ReadinessIssue, ...] = ()

    @property
    def ready(self) -> bool:
        """True when nothing blocking remains.

        Warnings deliberately do not make a cohort un-ready: an instructor
        releasing an assignment with an open dispute or an active curve is doing
        something ordinary, and a gate that cried wolf on it would be turned off.
        """
        return not self.blocking

    @property
    def blocking(self) -> tuple[ReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.is_blocking)

    @property
    def warnings(self) -> tuple[ReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "warning")

    @property
    def notes(self) -> tuple[ReadinessIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "note")

    @property
    def graded_fraction(self) -> float | None:
        """Share of answers that carry a grade, or ``None`` when there are none.

        ``None`` rather than 1.0 for an empty cohort: "100% graded" would be a
        lie about a folder with nothing in it, and the caller needs to be able
        to say so.
        """
        if self.total_answers <= 0:
            return None
        return self.graded_answers / self.total_answers


def _is_graded(question) -> bool:
    """Mirror the review queue's definition so the two counts can't disagree."""
    return question.points_awarded is not None or bool(question.rubric_labels)


def _student_example(document: StudentSubmissionDocument) -> str:
    """An anonymity-safe label for a student in an issue's example list."""
    return document.student_id.strip() or document.source_pdf or "(unidentified)"


def _examples(values: Iterable[str], total: int) -> tuple[str, ...]:
    """The first few distinct example labels, capped (see MAX_ISSUE_EXAMPLES)."""
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= MAX_ISSUE_EXAMPLES:
            break
    # A single example that *is* the whole population reads as a truncation of
    # something longer, so drop it and let the count speak.
    if total <= 1:
        return ()
    return tuple(seen)


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    return singular if count == 1 else (plural or f"{singular}s")


def build_readiness_report(
    documents: Sequence[StudentSubmissionDocument],
    *,
    review_flags: Sequence[ReviewFlag] = (),
    consistency_flags: Sequence[ConsistencyFlag] = (),
    claims: Sequence[WorkClaim] = (),
    curve: Curve = Curve(),
    previous_release: "GradeRelease | None" = None,
) -> ReadinessReport:
    """Decide whether a cohort is ready to hand back, and what is in the way.

    Pure: everything it reasons about is passed in, which is what lets the CLI,
    the TUI and a test build the same verdict from the same inputs.

    ``review_flags`` should come from
    :func:`core.submissions.review.build_review_flags` — this function
    deliberately re-reads that one definition of an anomaly rather than
    re-implementing the checks, and only decides what each *means* for a
    release. Omit any argument to skip the corresponding check entirely, the
    same convention `build_review_flags` uses for its roster and regrades.
    """
    issues: list[ReadinessIssue] = []

    # ── Ungraded work: the primary measure, counted straight from the files. ──
    # Not taken from the review queue's UNGRADED flag, which is opt-in there and
    # per-question: releasing needs both the answer count (how much work is
    # left) and the student count (how many people would get a partial grade).
    ungraded_answers = 0
    total_answers = 0
    ungraded_students: list[str] = []
    for document in documents:
        missing = 0
        for question in document.questions:
            total_answers += 1
            if not _is_graded(question):
                missing += 1
        ungraded_answers += missing
        if missing:
            ungraded_students.append(_student_example(document))

    if ungraded_answers:
        student_count = len(ungraded_students)
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNGRADED_ANSWERS,
                severity="blocking",
                summary=(
                    f"{ungraded_answers} {_plural(ungraded_answers, 'answer')} "
                    f"across {student_count} "
                    f"{_plural(student_count, 'student')} not graded yet."
                ),
                detail="Releasing now hands those students an incomplete grade.",
                count=ungraded_answers,
                examples=_examples(ungraded_students, student_count),
                example_total=student_count,
            )
        )

    # ── Everything the review queue already found, read as release verdicts. ──
    by_reason: dict[ReviewReason, list[ReviewFlag]] = {}
    for flag in review_flags:
        by_reason.setdefault(flag.reason, []).append(flag)

    identity_flags = [
        *by_reason.get(ReviewReason.MISSING_STUDENT_ID, ()),
        *by_reason.get(ReviewReason.MALFORMED_STUDENT_ID, ()),
    ]
    if identity_flags:
        # Blocking rather than a warning: a grade with no trustworthy id cannot
        # be posted to an LMS, cannot be matched to the person in a return
        # bundle, and — the reason this outranks everything else — a *wrong* id
        # hands one student another student's grade.
        students = {flag.student_id or flag.source_pdf for flag in identity_flags}
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNRESOLVED_IDENTITY,
                severity="blocking",
                summary=(
                    f"{len(students)} {_plural(len(students), 'submission')} "
                    "with a missing or unreadable student id."
                ),
                detail="Resolve them in the review queue before releasing.",
                count=len(students),
                examples=_examples(
                    (flag.source_pdf or flag.student_id for flag in identity_flags),
                    len(students),
                ),
                example_total=len(students),
            )
        )

    unsure_identity = by_reason.get(ReviewReason.LOW_CONFIDENCE_IDENTITY, [])
    if unsure_identity:
        # A warning rather than a blocker, and the split is the point: a missing
        # or malformed id is *known* to be unusable, while this one is a
        # plausible-looking value that may still belong to someone else. That is
        # a judgement for the instructor, not a gate — but it is the last moment
        # anyone will look at it, since a returned paper is never re-identified.
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNSURE_IDENTITY,
                severity="warning",
                summary=(
                    f"{len(unsure_identity)} "
                    f"{_plural(len(unsure_identity), 'submission')} whose student "
                    "name or id was read with low confidence."
                ),
                detail="Confirm them against the scan; a wrong id returns the "
                "wrong grade to the wrong person.",
                count=len(unsure_identity),
                examples=_examples(
                    (flag.source_pdf or flag.student_id for flag in unsure_identity),
                    len(unsure_identity),
                ),
                example_total=len(unsure_identity),
            )
        )

    unreadable = by_reason.get(ReviewReason.UNREADABLE_RESPONSE, [])
    if unreadable:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNREADABLE_RESPONSES,
                severity="warning",
                summary=(
                    f"{len(unreadable)} {_plural(len(unreadable), 'answer')} "
                    "could not be read at all."
                ),
                detail="Each will score whatever was graded from a blank reading.",
                count=len(unreadable),
                examples=_examples(
                    (flag.student_id for flag in unreadable), len(unreadable)
                ),
                example_total=len({flag.student_id for flag in unreadable}),
            )
        )

    ai_flags = by_reason.get(ReviewReason.LOW_CONFIDENCE_AI_GRADE, [])
    if ai_flags:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNCONFIRMED_AI_GRADES,
                severity="warning",
                summary=(
                    f"{len(ai_flags)} {_plural(len(ai_flags), 'answer')} carry a "
                    "low-confidence AI grade no human has confirmed."
                ),
                count=len(ai_flags),
                examples=_examples(
                    (flag.student_id for flag in ai_flags), len(ai_flags)
                ),
                example_total=len({flag.student_id for flag in ai_flags}),
            )
        )

    stale_rubric = by_reason.get(ReviewReason.GRADED_BEFORE_RUBRIC_CHANGE, [])
    if stale_rubric:
        # A warning, never a blocker, and the reason is the same one that keeps
        # open regrades out of the blocking tier: an instructor who edited a
        # rubric and chose not to regrade everybody is doing something ordinary
        # and defensible. What they must not do is *not notice*. Releasing is
        # also the last honest moment to decide, since after hand-back the same
        # question arrives one dispute at a time.
        students = {flag.student_id or flag.source_pdf for flag in stale_rubric}
        questions = sorted({flag.question_id for flag in stale_rubric if flag.question_id})
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.STALE_RUBRIC_GRADES,
                severity="warning",
                summary=(
                    f"{len(stale_rubric)} {_plural(len(stale_rubric), 'grade')} "
                    f"across {len(students)} {_plural(len(students), 'student')} "
                    f"{'was' if len(stale_rubric) == 1 else 'were'} awarded "
                    "before the rubric was edited."
                ),
                detail=(
                    "Affected "
                    + (
                        f"question{'s' if len(questions) != 1 else ''} "
                        f"{', '.join(questions)}. "
                        if questions
                        else ""
                    )
                    + "They were measured against options that no longer exist; "
                    "regrade them or confirm the grades still stand."
                ),
                count=len(stale_rubric),
                examples=_examples(
                    (flag.student_id or flag.source_pdf for flag in stale_rubric),
                    len(students),
                ),
                example_total=len(students),
            )
        )

    missing_subs = by_reason.get(ReviewReason.MISSING_SUBMISSION, [])
    if missing_subs:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.MISSING_SUBMISSIONS,
                severity="warning",
                summary=(
                    f"No submission for {len(missing_subs)} roster "
                    f"{_plural(len(missing_subs), 'student')}."
                ),
                detail="They are absent from the release, not scored zero by it.",
                count=len(missing_subs),
                examples=_examples(
                    (flag.student_id for flag in missing_subs), len(missing_subs)
                ),
                example_total=len(missing_subs),
            )
        )

    regrades = by_reason.get(ReviewReason.OPEN_REGRADE_REQUEST, [])
    if regrades:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.OPEN_REGRADE_REQUESTS,
                severity="warning",
                summary=(
                    f"{len(regrades)} open regrade "
                    f"{_plural(len(regrades), 'request')} unresolved."
                ),
                count=len(regrades),
                examples=_examples(
                    (flag.student_id for flag in regrades), len(regrades)
                ),
                example_total=len({flag.student_id for flag in regrades}),
            )
        )

    # ── Fairness: identical answers marked differently is the one thing that
    # reliably comes back as a complaint, so it is worth surfacing here even
    # though it has its own audit. Only the points divergence — a rubric
    # divergence at equal points changes nobody's grade. ──
    divergent = [
        flag
        for flag in consistency_flags
        if flag.kind == ConsistencyKind.POINTS_DIVERGENCE
    ]
    if divergent:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.INCONSISTENT_GRADING,
                severity="warning",
                summary=(
                    f"{len(divergent)} answer {_plural(len(divergent), 'group')} "
                    "where the same answer got different points."
                ),
                detail="Press C for the consistency audit.",
                count=len(divergent),
            )
        )

    # ── Notes: not problems, but they change how the numbers read. ──
    held = [claim for claim in claims if claim.is_active]
    if held:
        actors = sorted({claim.actor for claim in held})
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.HELD_WORK_CLAIMS,
                severity="note",
                summary=(
                    f"{len(actors)} {_plural(len(actors), 'grader')} still hold "
                    f"work: {', '.join(actors)}."
                ),
                detail="They may not consider their slice finished.",
                count=len(held),
            )
        )

    if curve.is_active:
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.CURVE_ACTIVE,
                severity="note",
                summary=f"A curve is active ({describe_curve(curve)}).",
                detail="Released totals are the curved ones, with raw kept beside them.",
            )
        )

    if previous_release is not None:
        comparison = compare_release(previous_release, documents, curve=curve)
        if comparison.changes or comparison.totals:
            changed_students = len(comparison.totals) or len(
                {change.student_key for change in comparison.changes}
            )
            issues.append(
                ReadinessIssue(
                    kind=ReadinessKind.CHANGED_SINCE_RELEASE,
                    severity="warning",
                    summary=(
                        f"Grades have changed for {changed_students} "
                        f"{_plural(changed_students, 'student')} since release "
                        f"{previous_release.release_id} handed them out."
                    ),
                    detail="Releasing again supersedes it; those students should be told.",
                    count=changed_students,
                )
            )

    issues.sort(key=lambda issue: (_SEVERITY_RANK[issue.severity], issue.kind.value))
    graded_answers = total_answers - ungraded_answers
    return ReadinessReport(
        student_count=len(documents),
        graded_answers=graded_answers,
        total_answers=total_answers,
        issues=tuple(issues),
    )


# --------------------------------------------------------------------------- #
# The release record — what the student was actually handed
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReleasedQuestion:
    """One question's grade at release time."""

    question_id: str
    points: float | None = None
    possible: float | None = None


@dataclass(frozen=True)
class ReleasedGrade:
    """One student's whole grade at release time.

    ``total`` is the raw sum and ``curved_total`` is what the student was
    actually shown; both are stored because a curve can be changed or cleared
    afterwards, and without the raw number a later comparison could not tell a
    regrade apart from a re-curve.
    """

    student_key: str
    student_id: str
    student_name: str
    total: float
    curved_total: float
    possible: float
    questions: tuple[ReleasedQuestion, ...] = ()

    def question_map(self) -> dict[str, ReleasedQuestion]:
        return {question.question_id: question for question in self.questions}


@dataclass(frozen=True)
class GradeRelease:
    """Every student's grade at the moment the assignment was handed back."""

    release_id: str
    created_at: str
    created_by: str = ""
    label: str = ""
    curve: Curve = Curve()
    students: tuple[ReleasedGrade, ...] = ()

    def by_key(self) -> dict[str, ReleasedGrade]:
        return {student.student_key: student for student in self.students}

    @property
    def student_count(self) -> int:
        return len(self.students)


def new_release_id(*, now: datetime | None = None) -> str:
    """A date-prefixed release id (``rel-2026-08-05-1a2b3c``).

    Date first so a directory listing is in release order, and a random tail so
    two releases on one day never collide — the same shape as the packet ids in
    `handoff/packet.py`, for the same reason: an id a human has to type in a
    `diff --release` command should be short and unambiguous.
    """
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    return f"rel-{stamp}-{uuid.uuid4().hex[:6]}"


def build_release(
    documents: Sequence[StudentSubmissionDocument],
    *,
    curve: Curve = Curve(),
    label: str = "",
    actor: str = "",
    release_id: str = "",
    now: datetime | None = None,
) -> GradeRelease:
    """Snapshot every student's grade exactly as the gradebook computes it.

    Students with no usable key (no id *and* no name) are skipped rather than
    stored under an empty key: two of them would collide into one record and a
    later comparison would report a grade change that never happened. They are
    already blocking the release as an unresolved identity.
    """
    stamp = now or datetime.now(timezone.utc)
    grades: list[ReleasedGrade] = []
    for document in documents:
        key = document_student_key(document)
        if not key:
            continue
        raw = total_awarded(document)
        possible = total_possible(document)
        grades.append(
            ReleasedGrade(
                student_key=key,
                student_id=document.student_id.strip(),
                student_name=document.student_name.strip(),
                total=raw,
                curved_total=apply_curve(raw, possible, curve),
                possible=possible,
                questions=tuple(
                    ReleasedQuestion(
                        question_id=question.question_id,
                        points=question.points_awarded,
                        possible=question.possible_points,
                    )
                    for question in document.questions
                ),
            )
        )
    return GradeRelease(
        release_id=release_id or new_release_id(now=stamp),
        created_at=stamp.isoformat(),
        created_by=actor or current_actor(),
        label=label.strip(),
        curve=curve,
        students=tuple(sorted(grades, key=lambda grade: grade.student_key)),
    )


# --------------------------------------------------------------------------- #
# Comparison — what moved since the student saw it
# --------------------------------------------------------------------------- #

ChangeKind = Literal[
    "raised",         # graded before and after, worth more now
    "lowered",        # graded before and after, worth less now
    "newly_graded",   # ungraded at release, graded since
    "ungraded",       # graded at release, ungraded since (a restore, usually)
    "added_question", # not in the release at all (a re-extraction changed the set)
    "removed_question",
]


@dataclass(frozen=True)
class GradeChange:
    """One question whose grade differs from what the release handed out."""

    student_key: str
    student_id: str
    student_name: str
    question_id: str
    kind: ChangeKind
    before: float | None = None
    after: float | None = None

    @property
    def delta(self) -> float | None:
        if self.before is None or self.after is None:
            return None
        return self.after - self.before


@dataclass(frozen=True)
class StudentTotalChange:
    """One student whose overall total is no longer what they were shown."""

    student_key: str
    student_id: str
    student_name: str
    before: float
    after: float
    possible: float

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass(frozen=True)
class ReleaseComparison:
    """What has moved since a release, per question and per student."""

    release_id: str
    released_at: str
    changes: tuple[GradeChange, ...] = ()
    totals: tuple[StudentTotalChange, ...] = ()
    # Students in the release with no submission today, and vice versa. Reported
    # rather than treated as changes: a vanished submission is a folder problem,
    # not a grade going down.
    missing_students: tuple[str, ...] = ()
    new_students: tuple[str, ...] = ()
    curve_changed: bool = False

    @property
    def unchanged(self) -> bool:
        return not (
            self.changes
            or self.totals
            or self.missing_students
            or self.new_students
        )


def _differs(before: float | None, after: float | None) -> bool:
    if before is None or after is None:
        return before is not after
    return abs(after - before) > POINT_EPSILON


def compare_release(
    release: GradeRelease,
    documents: Sequence[StudentSubmissionDocument],
    *,
    curve: Curve | None = None,
) -> ReleaseComparison:
    """Diff today's grades against what a release handed out.

    ``curve`` is the curve in effect *now*; omit it to reuse the release's own,
    which answers the narrower question "did any grade change?" while passing
    today's answers the one students actually experience: "is my total still
    what you told me?" A total can move because a grade moved or because the
    curve did, and keeping both numbers in the release is what lets this tell
    them apart.

    Pure — it takes parsed documents, so the CLI, the TUI and a test all compare
    identically.
    """
    current_curve = curve if curve is not None else release.curve
    released = release.by_key()
    seen: set[str] = set()

    changes: list[GradeChange] = []
    totals: list[StudentTotalChange] = []
    new_students: list[str] = []

    for document in documents:
        key = document_student_key(document)
        if not key:
            continue
        seen.add(key)
        before_student = released.get(key)
        if before_student is None:
            new_students.append(document.student_id.strip() or key)
            continue

        before_questions = before_student.question_map()
        current_ids = {question.question_id for question in document.questions}

        for question in document.questions:
            previous = before_questions.get(question.question_id)
            after = question.points_awarded
            if previous is None:
                # A question the release never carried. Only interesting when it
                # now has a grade — an ungraded new question changes nothing
                # about what the student was told.
                if after is not None:
                    changes.append(
                        GradeChange(
                            student_key=key,
                            student_id=before_student.student_id,
                            student_name=before_student.student_name,
                            question_id=question.question_id,
                            kind="added_question",
                            after=after,
                        )
                    )
                continue
            before = previous.points
            if not _differs(before, after):
                continue
            if before is None:
                kind: ChangeKind = "newly_graded"
            elif after is None:
                kind = "ungraded"
            elif after > before:
                kind = "raised"
            else:
                kind = "lowered"
            changes.append(
                GradeChange(
                    student_key=key,
                    student_id=before_student.student_id,
                    student_name=before_student.student_name,
                    question_id=question.question_id,
                    kind=kind,
                    before=before,
                    after=after,
                )
            )

        for question_id, previous in before_questions.items():
            if question_id in current_ids:
                continue
            changes.append(
                GradeChange(
                    student_key=key,
                    student_id=before_student.student_id,
                    student_name=before_student.student_name,
                    question_id=question_id,
                    kind="removed_question",
                    before=previous.points,
                )
            )

        raw = total_awarded(document)
        possible = total_possible(document)
        after_total = apply_curve(raw, possible, current_curve)
        if _differs(before_student.curved_total, after_total):
            totals.append(
                StudentTotalChange(
                    student_key=key,
                    student_id=before_student.student_id,
                    student_name=before_student.student_name,
                    before=before_student.curved_total,
                    after=after_total,
                    possible=possible,
                )
            )

    missing = tuple(
        sorted(
            released[key].student_id or key
            for key in released
            if key not in seen
        )
    )

    changes.sort(
        key=lambda change: (change.student_key, change.question_id, change.kind)
    )
    totals.sort(key=lambda change: change.student_key)
    return ReleaseComparison(
        release_id=release.release_id,
        released_at=release.created_at,
        changes=tuple(changes),
        totals=tuple(totals),
        missing_students=missing,
        new_students=tuple(sorted(new_students)),
        curve_changed=current_curve != release.curve,
    )


# --------------------------------------------------------------------------- #
# Serialization
# --------------------------------------------------------------------------- #


def render_release(release: GradeRelease) -> str:
    """Serialize a release to JSON text."""
    payload = {
        "release_format": RELEASE_FORMAT,
        "release_id": release.release_id,
        "created_at": release.created_at,
        "created_by": release.created_by,
        "label": release.label,
        "curve": release.curve.to_dict(),
        "students": [
            {
                "student_key": student.student_key,
                "student_id": student.student_id,
                "student_name": student.student_name,
                "total": student.total,
                "curved_total": student.curved_total,
                "possible": student.possible,
                "questions": [
                    {
                        "question_id": question.question_id,
                        "points": question.points,
                        "possible": question.possible,
                    }
                    for question in student.questions
                ],
            }
            for student in release.students
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_release(text: str) -> GradeRelease:
    """Read a release, raising :class:`ReleaseError` on anything unusable.

    Loud rather than lenient, for the same reason `parse_snapshot` is: a
    half-read release would silently compare today's grades against a subset of
    the cohort and report the rest as unchanged, which is the one answer it must
    never give.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"release is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseError("release must be a JSON object")
    version = data.get("release_format")
    if isinstance(version, int) and version > RELEASE_FORMAT:
        raise ReleaseError(
            f"release was written by a newer Opengrader (format {version}, "
            f"this version reads {RELEASE_FORMAT})"
        )

    raw_curve = data.get("curve")
    # `Curve.from_dict` already coerces an unknown method back to "none", so a
    # release written by a future version that grew a new curve method reads as
    # "no curve" rather than raising — the grades in it are still exact.
    curve = Curve.from_dict(raw_curve) if isinstance(raw_curve, dict) else Curve()

    raw_students = data.get("students")
    if not isinstance(raw_students, list):
        raise ReleaseError("release has no student list")

    students: list[ReleasedGrade] = []
    for entry in raw_students:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("student_key", "")).strip()
        if not key:
            continue
        raw_questions = entry.get("questions")
        questions: list[ReleasedQuestion] = []
        if isinstance(raw_questions, list):
            for raw_question in raw_questions:
                if not isinstance(raw_question, dict):
                    continue
                question_id = str(raw_question.get("question_id", "")).strip()
                if not question_id:
                    continue
                questions.append(
                    ReleasedQuestion(
                        question_id=question_id,
                        points=_optional_float(raw_question.get("points")),
                        possible=_optional_float(raw_question.get("possible")),
                    )
                )
        students.append(
            ReleasedGrade(
                student_key=key,
                student_id=str(entry.get("student_id", "")),
                student_name=str(entry.get("student_name", "")),
                total=_optional_float(entry.get("total")) or 0.0,
                curved_total=_optional_float(entry.get("curved_total")) or 0.0,
                possible=_optional_float(entry.get("possible")) or 0.0,
                questions=tuple(questions),
            )
        )

    return GradeRelease(
        release_id=str(data.get("release_id", "")),
        created_at=str(data.get("created_at", "")),
        created_by=str(data.get("created_by", "")),
        label=str(data.get("label", "")),
        curve=curve,
        students=tuple(students),
    )


# --------------------------------------------------------------------------- #
# Everything below touches the filesystem. Keep new verdict logic above this
# line so it stays testable without a folder to build.
# --------------------------------------------------------------------------- #


def resolve_releases_dir(submissions_path: str | Path) -> Path:
    """The ``.opengrader/releases/`` directory for a submissions folder.

    One file per release rather than one append-only log, matching
    `handoff/<packet-id>.json`: a release record is the whole cohort, so
    appending them would make reading the latest cost every earlier one, and a
    partially-written line would damage more than itself.
    """
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / RELEASE_DIRNAME / RELEASE_SUBDIR


def write_release(submissions_path: str | Path, release: GradeRelease) -> Path:
    """Persist a release into the folder it describes.

    Refuses to overwrite: a release id names a specific hand-back, and replacing
    one would rewrite what a student was told they got.
    """
    directory = resolve_releases_dir(submissions_path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{release.release_id}.json"
    if target.exists():
        raise ReleaseError(
            f"{target} already exists — a release is never rewritten, since it "
            "records what a student was handed"
        )
    target.write_text(render_release(release), encoding="utf-8")
    return target


def read_release(submissions_path: str | Path, release_id: str) -> GradeRelease | None:
    """Load one release by id, or ``None`` when there is no such release."""
    path = resolve_releases_dir(submissions_path) / f"{release_id.strip()}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_release(text)


def read_releases(submissions_path: str | Path) -> list[GradeRelease]:
    """Every release for a folder, oldest first.

    A file that will not parse is skipped rather than fatal: one damaged record
    must not make the rest of the history unreadable, and the missing id is
    visible in the listing by its absence.
    """
    directory = resolve_releases_dir(submissions_path)
    releases: list[GradeRelease] = []
    try:
        paths = sorted(directory.glob("*.json"))
    except OSError:
        return []
    for path in paths:
        try:
            releases.append(parse_release(path.read_text(encoding="utf-8")))
        except (OSError, ReleaseError):
            continue
    releases.sort(key=lambda release: (release.created_at, release.release_id))
    return releases


def latest_release(submissions_path: str | Path) -> GradeRelease | None:
    """The most recent release, or ``None`` when nothing has been released."""
    releases = read_releases(submissions_path)
    return releases[-1] if releases else None


@dataclass(frozen=True)
class ReleaseContext:
    """Everything a folder can tell us about its readiness, loaded once.

    The CLIs and the TUI all need the same six things (documents, curve, review
    flags, consistency flags, claims, the previous release) and assembling them
    is half the work; doing it in one place is what keeps `grade-check` and the
    TUI's release modal from ever disagreeing about whether a cohort is ready.
    """

    submissions_path: Path
    documents: tuple[StudentSubmissionDocument, ...]
    curve: Curve
    readiness: ReadinessReport
    previous_release: "GradeRelease | None" = None
    unreadable_files: tuple[str, ...] = ()


def load_release_context(
    submissions_path: str | Path,
    *,
    roster_path: str | Path | None = None,
) -> ReleaseContext:
    """Load a folder and build its readiness verdict.

    Every read is best-effort in the way its own module already defines: a
    missing curve is the identity curve, an absent claims log is no claims, an
    unparseable submission is *named* and skipped rather than aborting the
    check. That last one matters — a folder with one broken file is exactly when
    someone runs this, and refusing to answer would be the least useful reply.
    """
    from opengrader.core.analytics.rubric_drift import detect_rubric_drift
    from opengrader.core.claims import read_claims
    from opengrader.core.curve import read_curve
    from opengrader.core.history import read_journal, resolve_history_dir
    from opengrader.core.export.gradebook import discover_submission_paths
    from opengrader.core.roster import load_roster_students
    from opengrader.core.submissions.consistency import build_consistency_flags
    from opengrader.core.submissions.regrade import read_regrade_requests
    from opengrader.core.submissions.review import build_review_flags
    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    base = Path(submissions_path)
    documents: list[StudentSubmissionDocument] = []
    unreadable: list[str] = []
    for path in discover_submission_paths(base):
        try:
            documents.append(load_student_submission_markdown(path))
        except (OSError, ValueError):
            unreadable.append(path.name)

    roster = None
    if roster_path is not None:
        try:
            roster = load_roster_students(Path(roster_path))
        except (OSError, ValueError):
            roster = None

    curve = read_curve(base)
    previous = latest_release(base)
    review_flags = build_review_flags(
        documents,
        roster=roster,
        regrade_requests=read_regrade_requests(base),
        # The journal is the only record of when a rubric last moved, so the
        # readiness verdict has to read it here — but the *judgement* still
        # comes from the review flags below, so `grade-check` and `review-queue`
        # can never disagree about which grades are stale.
        stale_grades=detect_rubric_drift(read_journal(resolve_history_dir(base))),
    )
    readiness = build_readiness_report(
        documents,
        review_flags=review_flags,
        consistency_flags=build_consistency_flags(documents),
        claims=read_claims(base),
        curve=curve,
        previous_release=previous,
    )
    return ReleaseContext(
        submissions_path=base,
        documents=tuple(documents),
        curve=curve,
        readiness=readiness,
        previous_release=previous,
        unreadable_files=tuple(unreadable),
    )

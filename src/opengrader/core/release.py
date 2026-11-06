from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Iterable, Literal, Sequence

if TYPE_CHECKING:
    from opengrader.core.roster import RosterStudent
    from opengrader.core.workspace.store import Workspace

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

MAX_ISSUE_EXAMPLES = 3

POINT_EPSILON = 1e-6


class ReleaseError(ValueError):
    pass


ReadinessSeverity = Literal["blocking", "warning", "note"]

_SEVERITY_RANK: dict[ReadinessSeverity, int] = {"blocking": 0, "warning": 1, "note": 2}


class ReadinessKind(str, Enum):
    UNGRADED_ANSWERS = "ungraded_answers"
    UNRESOLVED_IDENTITY = "unresolved_identity"

    UNSURE_IDENTITY = "unsure_identity"
    UNREADABLE_RESPONSES = "unreadable_responses"
    UNCONFIRMED_AI_GRADES = "unconfirmed_ai_grades"
    INCONSISTENT_GRADING = "inconsistent_grading"
    OPEN_REGRADE_REQUESTS = "open_regrade_requests"
    MISSING_SUBMISSIONS = "missing_submissions"
    STALE_RUBRIC_GRADES = "stale_rubric_grades"
    CHANGED_SINCE_RELEASE = "changed_since_release"

    HELD_WORK_CLAIMS = "held_work_claims"
    CURVE_ACTIVE = "curve_active"


@dataclass(frozen=True)
class ReadinessIssue:
    """count uses the summary's unit; example_total always counts students."""

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
    student_count: int
    graded_answers: int
    total_answers: int
    issues: tuple[ReadinessIssue, ...] = ()

    @property
    def ready(self) -> bool:
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
        if self.total_answers <= 0:
            return None
        return self.graded_answers / self.total_answers


def _is_graded(question) -> bool:
    return question.points_awarded is not None or bool(question.rubric_labels)


def _student_example(document: StudentSubmissionDocument) -> str:
    return document.student_id.strip() or document.source_pdf or "(unidentified)"


def _examples(values: Iterable[str], total: int) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value and value not in seen:
            seen.append(value)
        if len(seen) >= MAX_ISSUE_EXAMPLES:
            break
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
    issues: list[ReadinessIssue] = []

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

    by_reason: dict[ReviewReason, list[ReviewFlag]] = {}
    for flag in review_flags:
        by_reason.setdefault(flag.reason, []).append(flag)

    identity_flags = [
        *by_reason.get(ReviewReason.MISSING_STUDENT_ID, ()),
        *by_reason.get(ReviewReason.MALFORMED_STUDENT_ID, ()),
        *by_reason.get(ReviewReason.NOT_ON_ROSTER, ()),
    ]
    if identity_flags:
        students = {flag.student_id or flag.source_pdf for flag in identity_flags}
        issues.append(
            ReadinessIssue(
                kind=ReadinessKind.UNRESOLVED_IDENTITY,
                severity="blocking",
                summary=(
                    f"{len(students)} {_plural(len(students), 'submission')} "
                    "with a missing or unreadable student id, or one not on the roster."
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


@dataclass(frozen=True)
class ReleasedQuestion:
    question_id: str
    points: float | None = None
    possible: float | None = None


@dataclass(frozen=True)
class ReleasedGrade:
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


ChangeKind = Literal[
    "raised",
    "lowered",
    "newly_graded",
    "ungraded",
    "added_question",
    "removed_question",
]


@dataclass(frozen=True)
class GradeChange:
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
    release_id: str
    released_at: str
    changes: tuple[GradeChange, ...] = ()
    totals: tuple[StudentTotalChange, ...] = ()
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


def render_release(release: GradeRelease) -> str:
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


def resolve_releases_dir(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / RELEASE_DIRNAME / RELEASE_SUBDIR


def write_release(submissions_path: str | Path, release: GradeRelease) -> Path:
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
    path = resolve_releases_dir(submissions_path) / f"{release_id.strip()}.json"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    return parse_release(text)


def read_releases(submissions_path: str | Path) -> list[GradeRelease]:
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
    releases = read_releases(submissions_path)
    return releases[-1] if releases else None


@dataclass(frozen=True)
class ReleaseContext:
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
    from opengrader.core.export.gradebook import discover_submission_paths
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

    return _release_context_from_documents(
        base, documents, roster_path=roster_path, unreadable=tuple(unreadable)
    )


def load_workspace_release_context(
    workspace: "Workspace", assignment_id: str, *, roster_path: str | Path | None = None
) -> ReleaseContext:
    from opengrader.core.workspace.context import (
        inputs_for_assignment,
        resolve_path,
        submissions_path,
    )

    saved = inputs_for_assignment(workspace, assignment_id)
    base = submissions_path(workspace, assignment_id)
    if saved is not None:
        folder = resolve_path(workspace.root, saved.folder)
        if roster_path is None and saved.roster is not None:
            roster_path = resolve_path(folder, saved.roster)
    documents = [
        workspace.document(assignment_id, item.id)
        for item in workspace.submissions(assignment_id)
    ]
    roster = None
    if roster_path is None:
        # No roster file: the workspace's own (the browser's People page).
        from opengrader.core.roster import RosterStudent
        from opengrader.core.workspace import roster as workspace_roster

        students = workspace_roster.students(workspace)
        roster = [RosterStudent(student.sid, student.name) for student in students] or None
    return _release_context_from_documents(
        base, documents, roster_path=roster_path, unreadable=(), roster=roster
    )


def _release_context_from_documents(
    base: Path,
    documents: Sequence[StudentSubmissionDocument],
    *,
    roster_path: str | Path | None,
    unreadable: tuple[str, ...],
    roster: "list[RosterStudent] | None" = None,
) -> ReleaseContext:
    """`roster_path` is the roster to check against; without one (and no
    `roster` given) it is the class folder's (`roster_check.find_class_roster`),
    so a paper not matched to it is held back as an unresolved identity."""
    from opengrader.core.analytics.rubric_drift import detect_rubric_drift
    from opengrader.core.claims import read_claims
    from opengrader.core.curve import read_curve
    from opengrader.core.history import read_journal, resolve_history_dir
    from opengrader.core.roster import load_roster_students
    from opengrader.core.submissions.consistency import build_consistency_flags
    from opengrader.core.submissions.regrade import read_regrade_requests
    from opengrader.core.submissions.review import build_review_flags

    if roster_path is not None:
        try:
            roster = load_roster_students(Path(roster_path))
        except (OSError, ValueError):
            roster = None
    elif roster is None:
        from opengrader.core.submissions.roster_check import (
            find_class_roster,
            load_class_roster,
        )

        found = find_class_roster(base)
        roster = load_class_roster(found[1]) if found is not None else None

    curve = read_curve(base)
    previous = latest_release(base)
    review_flags = build_review_flags(
        documents,
        roster=roster,
        regrade_requests=read_regrade_requests(base),
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
        unreadable_files=unreadable,
    )

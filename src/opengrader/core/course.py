"""Course-level gradebook: a semester's assignments rolled into one grade.

One folder per assignment is the right unit for *grading* — it keeps each
exam's scans, submissions, journal and curve together — and the wrong unit for a
*semester*. An instructor at the end of a term has eight of those folders and
one question the app could not answer: what is each student's course grade?

This module is the thin layer that answers it, over a ``course.toml`` the
instructor writes once:

    name = "CS 146 — Spring 2026"

    [[assignment]]
    name = "Quiz 1"
    path = "quizzes/quiz-1/student-submissions"
    weight = 10
    solutions = "quizzes/quiz-1/solutions.md"   # optional; unlocks outcomes

    [[assignment]]
    name = "Midterm"
    path = "exams/midterm/student-submissions"
    weight = 30

    [grades]
    A = 90
    B = 80
    C = 70
    D = 60

Design rules, each of them a deliberate constraint:

* **Nothing new is written inside an assignment folder.** The course file lives
  *above* them and only ever reads. An assignment folder stays exactly what it
  was — portable, gradeable on its own, and unaware a course exists. That is
  what keeps a mid-semester re-grade from needing a course-level migration.
* **Per-assignment scores come from `export/gradebook.py`**, the same code the
  CSV and the return bundle use. A student's course grade is therefore built
  from the identical numbers on the gradebook they were handed, curve included;
  a second scoring path would eventually disagree with the first, and the
  disagreement would surface as a grade dispute.
* **Weights are relative, not required to sum to 100.** Instructors write
  ``10, 10, 30, 50`` and also ``1, 1, 3, 5``; normalizing means neither is
  wrong, and a missing assignment simply doesn't dilute the rest.
* **Missing work is *missing*, not zero — until the instructor says otherwise.**
  A student with no submission for an assignment is reported as *not counted*
  by default, because mid-semester that overwhelmingly means "not graded yet"
  rather than "scored nothing", and silently averaging in zeros produces an
  at-risk list full of false alarms. ``missing = "zero"`` per assignment (or
  course-wide) switches it, which is what an instructor does at final-grade
  time.
* **Read-only.** Nothing here changes a grade. It reads assignment folders and
  reports.

``tomllib`` is in the standard library from Python 3.11, so this adds no
dependency.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from opengrader.core.analytics.item_analysis import (
    DEFAULT_MASTERY_THRESHOLD,
    outcome_sort_key,
)
from opengrader.core.curve import apply_curve, read_curve
from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.qa_file import (
    load_qa_markdown,
    outcomes_by_question,
    possible_points_by_question,
)
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    load_student_submission_markdown,
)

COURSE_FILENAME = "course.toml"

# The conventional US letter scale, used when the course file names none. Kept
# as a plain ordered list of (letter, minimum percent) so a course can replace
# it wholesale without this module knowing anything about grading philosophy.
DEFAULT_GRADE_SCALE: tuple[tuple[str, float], ...] = (
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
    ("F", 0.0),
)

# A student at or below this course percentage is surfaced as at-risk. 70 is the
# usual "C" line — the point at which most programmes expect an intervention
# rather than a note.
DEFAULT_AT_RISK_PERCENT = 70.0

# How an assignment a student has no graded work for is treated.
#   "skip" — leave it out of both the earned and the possible totals, so the
#            course percentage describes what *has* been graded.
#   "zero" — count its full weight as earned nothing.
MissingPolicy = str  # Literal["skip", "zero"] without importing typing at runtime
_MISSING_POLICIES = ("skip", "zero")


class CourseConfigError(ValueError):
    """Raised when a ``course.toml`` cannot be understood.

    Always names the offending key and the file, because this is a
    hand-written file and a silent default here would misreport grades.
    """


@dataclass(frozen=True)
class CourseAssignment:
    """One assignment in the course, as declared in ``course.toml``."""

    name: str
    path: Path
    weight: float
    solutions: Path | None = None
    missing: MissingPolicy = "skip"


@dataclass(frozen=True)
class CourseConfig:
    """A parsed ``course.toml``."""

    name: str
    assignments: tuple[CourseAssignment, ...]
    grade_scale: tuple[tuple[str, float], ...] = DEFAULT_GRADE_SCALE
    at_risk_percent: float = DEFAULT_AT_RISK_PERCENT
    mastery_threshold: float = DEFAULT_MASTERY_THRESHOLD
    root: Path = field(default_factory=Path)

    @property
    def total_weight(self) -> float:
        return sum(a.weight for a in self.assignments)


def _as_float(value: object, *, key: str, source: Path) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise CourseConfigError(f"{source}: {key} must be a number, got {value!r}") from exc


def load_course_config(path: str | Path) -> CourseConfig:
    """Read and validate a ``course.toml`` (or the directory holding one).

    Every relative path inside the file resolves against the **file's own
    directory**, so a course folder can be moved or shared without editing it.
    """
    path = Path(path).expanduser()
    if path.is_dir():
        path = path / COURSE_FILENAME
    if not path.is_file():
        raise CourseConfigError(f"course file not found: {path}")

    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise CourseConfigError(f"{path}: not valid TOML — {exc}") from exc

    root = path.parent
    raw_assignments = data.get("assignment") or []
    if not isinstance(raw_assignments, list) or not raw_assignments:
        raise CourseConfigError(
            f"{path}: needs at least one [[assignment]] table "
            "(name, path, weight)."
        )

    course_missing = str(data.get("missing", "skip")).strip().lower()
    if course_missing not in _MISSING_POLICIES:
        raise CourseConfigError(
            f"{path}: missing must be one of {_MISSING_POLICIES}, got {course_missing!r}"
        )

    assignments: list[CourseAssignment] = []
    seen_names: set[str] = set()
    for index, entry in enumerate(raw_assignments, start=1):
        if not isinstance(entry, dict):
            raise CourseConfigError(f"{path}: [[assignment]] #{index} is not a table")
        name = str(entry.get("name") or "").strip()
        raw_path = str(entry.get("path") or "").strip()
        if not name or not raw_path:
            raise CourseConfigError(
                f"{path}: [[assignment]] #{index} needs both a name and a path"
            )
        if name in seen_names:
            # Names key the per-assignment columns, so a duplicate would make
            # one assignment silently overwrite another in every output.
            raise CourseConfigError(f"{path}: duplicate assignment name {name!r}")
        seen_names.add(name)

        weight = _as_float(entry.get("weight", 1), key=f"{name}.weight", source=path)
        if weight < 0:
            raise CourseConfigError(f"{path}: {name}.weight must not be negative")

        missing = str(entry.get("missing", course_missing)).strip().lower()
        if missing not in _MISSING_POLICIES:
            raise CourseConfigError(
                f"{path}: {name}.missing must be one of {_MISSING_POLICIES}, "
                f"got {missing!r}"
            )

        solutions = entry.get("solutions")
        assignments.append(
            CourseAssignment(
                name=name,
                path=(root / raw_path).resolve(),
                weight=weight,
                solutions=(root / str(solutions)).resolve() if solutions else None,
                missing=missing,
            )
        )

    if all(a.weight <= 0 for a in assignments):
        raise CourseConfigError(f"{path}: every assignment weight is zero")

    grades = data.get("grades")
    scale = DEFAULT_GRADE_SCALE
    if isinstance(grades, dict) and grades:
        # Highest cut first, so `letter_for` can take the first match.
        scale = tuple(
            sorted(
                (
                    (str(letter), _as_float(cut, key=f"grades.{letter}", source=path))
                    for letter, cut in grades.items()
                ),
                key=lambda item: -item[1],
            )
        )

    return CourseConfig(
        name=str(data.get("name") or root.name),
        assignments=tuple(assignments),
        grade_scale=scale,
        at_risk_percent=_as_float(
            data.get("at_risk_percent", DEFAULT_AT_RISK_PERCENT),
            key="at_risk_percent",
            source=path,
        ),
        mastery_threshold=_as_float(
            data.get("mastery_threshold", DEFAULT_MASTERY_THRESHOLD),
            key="mastery_threshold",
            source=path,
        ),
        root=root,
    )


def letter_for(percent: float | None, scale: tuple[tuple[str, float], ...]) -> str:
    """The letter grade for a course percentage, or ``"-"`` when unknown.

    Falls to the lowest letter rather than raising when a percentage sits under
    every cut, so a scale that omits an explicit F still produces a grade.
    """
    if percent is None:
        return "-"
    for letter, minimum in scale:
        if percent >= minimum:
            return letter
    return scale[-1][0] if scale else "-"


# ── reading one assignment ───────────────────────────────────────────────────


@dataclass(frozen=True)
class AssignmentScore:
    """One student's result on one assignment."""

    assignment: str
    earned: float
    possible: float
    graded_questions: int

    @property
    def percent(self) -> float | None:
        if self.possible <= 0:
            return None
        return self.earned / self.possible * 100.0


@dataclass(frozen=True)
class LoadedAssignment:
    """One assignment's submissions, scored and keyed by student."""

    assignment: CourseAssignment
    scores: dict[str, AssignmentScore]
    names: dict[str, str]
    outcomes_by_question: dict[str, tuple[str, ...]]
    possible_by_question: dict[str, float]
    documents: dict[str, StudentSubmissionDocument]
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def student_key(document: StudentSubmissionDocument) -> str:
    """How a student is matched *across* assignments.

    The student id, because it is the only field stable between two separately
    extracted folders — names get OCR'd differently on different scans and a
    file path is per-assignment by construction. A submission with no id falls
    back to a normalized name so it still appears somewhere rather than being
    dropped; those are exactly the rows the needs-review queue already asks the
    instructor to fix, and the course report says so rather than guessing.
    """
    student_id = document.student_id.strip()
    if student_id:
        return student_id
    return f"name:{document.student_name.strip().casefold()}"


def load_assignment(assignment: CourseAssignment) -> LoadedAssignment:
    """Score one assignment folder, curve included.

    Failures are captured on the result rather than raised: one unreadable
    folder in a twelve-assignment course should downgrade that column, not
    destroy the whole course report a week before grades are due.
    """
    empty: dict = {}
    try:
        paths = discover_submission_paths(assignment.path)
    except (FileNotFoundError, NotADirectoryError, OSError) as exc:
        return LoadedAssignment(assignment, {}, {}, empty, empty, {}, error=str(exc))
    if not paths:
        return LoadedAssignment(
            assignment,
            {},
            {},
            empty,
            empty,
            {},
            error=f"no .submission.md files under {assignment.path}",
        )

    curve = read_curve(assignment.path)
    outcome_tags: dict[str, tuple[str, ...]] = {}
    question_points: dict[str, float] = {}
    if assignment.solutions is not None and assignment.solutions.is_file():
        qa_doc = load_qa_markdown(assignment.solutions)
        outcome_tags = outcomes_by_question(qa_doc)
        question_points = possible_points_by_question(qa_doc)

    scores: dict[str, AssignmentScore] = {}
    names: dict[str, str] = {}
    documents: dict[str, StudentSubmissionDocument] = {}
    for path in paths:
        try:
            document = load_student_submission_markdown(path)
        except ValueError:
            # A malformed file is skipped rather than fatal, matching how every
            # other whole-folder reader in the app behaves.
            continue
        key = student_key(document)
        earned, possible, graded = _score_document(document, question_points)
        scores[key] = AssignmentScore(
            assignment=assignment.name,
            earned=apply_curve(earned, possible, curve) if possible > 0 else earned,
            possible=possible,
            graded_questions=graded,
        )
        names[key] = document.student_name.strip()
        documents[key] = document

    return LoadedAssignment(
        assignment=assignment,
        scores=scores,
        names=names,
        outcomes_by_question=outcome_tags,
        possible_by_question=question_points,
        documents=documents,
    )


def _score_document(
    document: StudentSubmissionDocument,
    question_points: dict[str, float],
) -> tuple[float, float, int]:
    """(earned, possible, graded-question count) for one submission.

    Only *graded* questions contribute to `possible`, the same rule the
    gradebook CSV uses: an assignment half-marked must read as a percentage of
    what was marked, never as a student having lost the rest.
    """
    earned = 0.0
    possible = 0.0
    graded = 0
    for question in document.questions:
        if question.points_awarded is None:
            continue
        points = question.possible_points
        if points is None:
            points = question_points.get(question.question_id)
        if points is None:
            continue
        earned += question.points_awarded
        possible += float(points)
        graded += 1
    return earned, possible, graded


# ── the course roll-up ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class StudentCourseGrade:
    """One student's course result across every assignment."""

    student_key: str
    student_id: str
    student_name: str
    scores: dict[str, AssignmentScore]
    missing: tuple[str, ...]
    weighted_percent: float | None
    letter: str

    @property
    def is_complete(self) -> bool:
        return not self.missing

    def trend(self) -> tuple[tuple[str, float], ...]:
        """Per-assignment percentages in course order — the student's trajectory.

        Only assignments the student actually has a score for, so a gap reads as
        a gap rather than as a crash to zero.
        """
        return tuple(
            (name, score.percent)
            for name, score in self.scores.items()
            if score.percent is not None
        )

    def direction(self) -> float | None:
        """Change in percentage points from the first half to the second.

        A deliberately blunt measure — first-half mean vs second-half mean —
        because the alternative (a regression slope over 4 points) reports
        precision this data does not have. ``None`` until there are at least two
        graded assignments to compare.
        """
        points = [percent for _, percent in self.trend()]
        if len(points) < 2:
            return None
        middle = len(points) // 2
        first = points[:middle] or points[:1]
        second = points[middle:]
        return sum(second) / len(second) - sum(first) / len(first)


@dataclass(frozen=True)
class CourseOutcomeMastery:
    """One learning outcome, aggregated across every assignment that tags it."""

    outcome: str
    assignments: tuple[str, ...]
    possible_points: float
    students_measured: int
    students_mastered: int
    mean_mastery: float | None

    @property
    def mastery_rate(self) -> float | None:
        if self.students_measured == 0:
            return None
        return self.students_mastered / self.students_measured


@dataclass(frozen=True)
class CourseReport:
    """The whole semester: weighted grades, at-risk list, outcome mastery."""

    course: str
    assignments: tuple[CourseAssignment, ...]
    students: tuple[StudentCourseGrade, ...]
    outcomes: tuple[CourseOutcomeMastery, ...]
    problems: tuple[str, ...]
    at_risk_percent: float
    mastery_threshold: float
    grade_scale: tuple[tuple[str, float], ...]

    @property
    def at_risk(self) -> tuple[StudentCourseGrade, ...]:
        """Students at or below the at-risk line, lowest first.

        A student with no graded work at all is **not** here: there is no
        evidence they are struggling, and burying the real cases under an
        entire un-graded class is how an at-risk list gets ignored.
        """
        flagged = [
            student
            for student in self.students
            if student.weighted_percent is not None
            and student.weighted_percent <= self.at_risk_percent
        ]
        flagged.sort(key=lambda s: (s.weighted_percent or 0.0, s.student_id))
        return tuple(flagged)

    def grade_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for student in self.students:
            counts[student.letter] = counts.get(student.letter, 0) + 1
        return counts


def build_course_report(config: CourseConfig) -> CourseReport:
    """Roll a semester of assignment folders into one course report (reads only).

    Every assignment is loaded once and reused for the grade roll-up *and* the
    outcome roll-up, so the two can never describe different data.
    """
    loaded = [load_assignment(assignment) for assignment in config.assignments]
    problems = [
        f"{item.assignment.name}: {item.error}" for item in loaded if item.error
    ]

    keys: list[str] = []
    names: dict[str, str] = {}
    ids: dict[str, str] = {}
    for item in loaded:
        for key in item.scores:
            if key not in names:
                keys.append(key)
            # A later assignment's name wins only when the earlier one was
            # blank — anonymized folders legitimately carry no name.
            if item.names.get(key):
                names[key] = item.names[key]
            names.setdefault(key, "")
            ids.setdefault(key, item.documents[key].student_id.strip())

    students = [
        _grade_student(key, ids[key], names[key], loaded, config) for key in keys
    ]
    students.sort(key=lambda s: (s.student_name.casefold(), s.student_id))

    return CourseReport(
        course=config.name,
        assignments=config.assignments,
        students=tuple(students),
        outcomes=_course_outcomes(loaded, config),
        problems=tuple(problems),
        at_risk_percent=config.at_risk_percent,
        mastery_threshold=config.mastery_threshold,
        grade_scale=config.grade_scale,
    )


def _grade_student(
    key: str,
    student_id: str,
    student_name: str,
    loaded: list[LoadedAssignment],
    config: CourseConfig,
) -> StudentCourseGrade:
    """Weighted course percentage for one student.

    The weighted mean is over the assignments that *count* for this student:
    each contributes ``weight × percent``, normalized by the weight actually
    included. That normalization is what makes a mid-semester report meaningful
    — with three of eight assignments graded, a student sits at their average so
    far rather than at 37% of the course.
    """
    scores: dict[str, AssignmentScore] = {}
    missing: list[str] = []
    weighted_sum = 0.0
    weight_used = 0.0

    for item in loaded:
        assignment = item.assignment
        score = item.scores.get(key)
        percent = score.percent if score is not None else None
        if score is not None:
            scores[assignment.name] = score
        if percent is None:
            missing.append(assignment.name)
            if assignment.missing == "zero" and assignment.weight > 0:
                # Counted as earning nothing: the weight is included in the
                # denominator but contributes no numerator.
                weight_used += assignment.weight
            continue
        weighted_sum += assignment.weight * percent
        weight_used += assignment.weight

    percent = weighted_sum / weight_used if weight_used > 0 else None
    return StudentCourseGrade(
        student_key=key,
        student_id=student_id,
        student_name=student_name,
        scores=scores,
        missing=tuple(missing),
        weighted_percent=percent,
        letter=letter_for(percent, config.grade_scale),
    )


def _course_outcomes(
    loaded: list[LoadedAssignment], config: CourseConfig
) -> tuple[CourseOutcomeMastery, ...]:
    """Aggregate learning-outcome mastery across every assignment that tags one.

    **This is the accreditation artifact.** A single exam measures an outcome
    once; a programme review asks whether students met it over a course, which
    only exists at this level. Points are pooled across assignments rather than
    averaging each assignment's rate, so a 40-point final carrying CLO2 counts
    for more than a 4-point quiz question carrying it — the instructor's own
    weighting, already expressed in the points.

    Deliberately independent of assignment *weights*: those govern the grade an
    outcome roll-up is not. Mixing them would make "did they learn it" depend on
    how much the exam was worth.
    """
    earned: dict[tuple[str, str], float] = {}
    possible: dict[tuple[str, str], float] = {}
    outcome_points: dict[str, float] = {}
    outcome_assignments: dict[str, list[str]] = {}

    for item in loaded:
        if not item.outcomes_by_question:
            continue
        counted_questions: set[str] = set()
        for key, document in item.documents.items():
            for question in document.questions:
                tags = item.outcomes_by_question.get(question.question_id, ())
                if not tags or question.points_awarded is None:
                    continue
                points = question.possible_points
                if points is None:
                    points = item.possible_by_question.get(question.question_id)
                if points is None:
                    continue
                for outcome in tags:
                    earned[(key, outcome)] = (
                        earned.get((key, outcome), 0.0) + question.points_awarded
                    )
                    possible[(key, outcome)] = possible.get((key, outcome), 0.0) + float(
                        points
                    )
                    names = outcome_assignments.setdefault(outcome, [])
                    if item.assignment.name not in names:
                        names.append(item.assignment.name)
                    # The outcome's point pool is per question, not per
                    # response, or it would be multiplied by the class size.
                    marker = f"{item.assignment.name}:{question.question_id}:{outcome}"
                    if marker not in counted_questions:
                        counted_questions.add(marker)
                        outcome_points[outcome] = outcome_points.get(
                            outcome, 0.0
                        ) + float(points)

    results: list[CourseOutcomeMastery] = []
    for outcome in sorted(outcome_assignments, key=outcome_sort_key):
        masteries = [
            earned[(key, name)] / possible[(key, name)]
            for (key, name) in possible
            if name == outcome and possible[(key, name)] > 0
        ]
        mastered = sum(1 for m in masteries if m >= config.mastery_threshold)
        results.append(
            CourseOutcomeMastery(
                outcome=outcome,
                assignments=tuple(outcome_assignments[outcome]),
                possible_points=outcome_points.get(outcome, 0.0),
                students_measured=len(masteries),
                students_mastered=mastered,
                mean_mastery=sum(masteries) / len(masteries) if masteries else None,
            )
        )
    return tuple(results)


# ── weighting, explained ─────────────────────────────────────────────────────


def weight_shares(weights: list[float]) -> list[float]:
    """Turn relative weights into the share of the final grade each one is.

    This is the whole meaning of ``weight`` in one function, and the reason the
    course builder can show a live "→ 25%" beside every row. Weights are
    *relative*: what an assignment is worth is its weight over the total, so
    ``1, 1, 2`` and ``25, 25, 50`` and ``10, 10, 20`` are all the same course.

    Showing the share rather than explaining the ratio is deliberate — an
    instructor who types ``10, 20, 30, 40`` and sees exactly 10/20/30/40% has
    learned the rule without reading it, and one who types ``1, 1, 2`` and sees
    25/25/50% has learned it too.

    All-zero (or empty) weights have no meaningful share, so every entry is
    ``0.0`` rather than a division by zero.
    """
    total = sum(w for w in weights if w > 0)
    if total <= 0:
        return [0.0 for _ in weights]
    return [(w / total if w > 0 else 0.0) for w in weights]


# ── writing a course file ────────────────────────────────────────────────────

# Assignment folders are usually called one of these, in which case the *parent*
# directory is the assignment's real name ("midterm/student-submissions" is the
# midterm, not a thing called "student submissions").
_GENERIC_SUBMISSION_DIR_NAMES = {
    "student-submissions",
    "student_submissions",
    "submissions",
    "subs",
    "out",
}

# Filenames an answer key is likely to have, most specific first. Guessing is
# safe here because the builder only *prefills* the field — a wrong guess is
# corrected by the instructor before saving, and a miss just leaves it blank.
_LIKELY_SOLUTION_NAMES = (
    "solutions.md",
    "solution.md",
    "answer-key.md",
    "answer_key.md",
    "qa.md",
)

# How deep to look for assignment folders under a course root. Three levels
# covers `course/exams/midterm/student-submissions` without turning a scan of a
# home directory into a filesystem crawl.
_DISCOVERY_DEPTH = 3


def _escape_toml(value: str) -> str:
    """Escape a string for a TOML basic (double-quoted) string.

    Hand-written because Python has no stdlib TOML *writer* and the schema here
    is tiny and fully ours. Backslash first, or it would double-escape the
    escapes it just added — the classic ordering bug in this two-line function.
    """
    return value.replace("\\", "\\\\").replace('"', '\\"')


def humanize_assignment_name(path: Path) -> str:
    """Guess a readable assignment name from a submissions folder path.

    ``exams/midterm-2/student-submissions`` → ``Midterm 2``. A guess the
    instructor can overwrite, so it optimizes for "usually right and always
    editable" rather than correctness.
    """
    directory = path if path.name else path.parent
    name = directory.name
    if name.lower() in _GENERIC_SUBMISSION_DIR_NAMES and directory.parent.name:
        name = directory.parent.name
    cleaned = name.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return "Assignment"
    # Title-case only the words that are entirely lowercase, so "CS146" and
    # "HW2b" keep the casing the instructor chose.
    return " ".join(
        word.capitalize() if word.islower() else word for word in cleaned.split()
    )


def find_solutions_near(submissions: Path) -> Path | None:
    """Look for an answer key beside (or just above) a submissions folder."""
    for directory in (submissions, submissions.parent):
        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue
        for candidate in _LIKELY_SOLUTION_NAMES:
            path = directory / candidate
            if path.is_file():
                return path
        # A lone markdown file next to the submissions is almost certainly it —
        # but student submissions are markdown too, and a folder holding exactly
        # one of them would otherwise be "found" as its own answer key.
        try:
            markdown = [
                p
                for p in sorted(directory.glob("*.md"))
                if p.is_file() and not p.name.endswith(".submission.md")
            ]
        except OSError:
            markdown = []
        if len(markdown) == 1:
            return markdown[0]
    return None


@dataclass(frozen=True)
class DiscoveredAssignment:
    """A submissions folder found under a course root, with its guessed name."""

    name: str
    path: Path
    solutions: Path | None


def discover_course_assignments(root: str | Path) -> list[DiscoveredAssignment]:
    """Find every assignment folder under ``root``, for prefilling a course file.

    Saves the instructor from typing a path per assignment: point this at the
    semester's folder and the builder drafts a row for each. Ordered by path so
    two scans agree, and bounded by :data:`_DISCOVERY_DEPTH` so pointing it
    somewhere broad is slow at worst, never unbounded.

    A folder is an assignment when submissions are directly in it or one level
    down — the same rule every other tool discovers by. Nested matches are
    skipped: with `midterm/student-submissions` found, `midterm` itself would
    otherwise be reported as a second, duplicate assignment.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        return []

    found: list[Path] = []
    queue: list[tuple[Path, int]] = [(root, 0)]
    while queue:
        directory, depth = queue.pop(0)
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            continue
        if any(directory.glob("*.submission.md")):
            found.append(directory)
            continue  # its children are per-student folders, not assignments
        if depth < _DISCOVERY_DEPTH:
            queue.extend((child, depth + 1) for child in children)

    results: list[DiscoveredAssignment] = []
    for path in sorted(found):
        results.append(
            DiscoveredAssignment(
                name=humanize_assignment_name(path),
                path=path,
                solutions=find_solutions_near(path),
            )
        )
    return results


def render_course_toml(
    *,
    name: str,
    assignments: list[CourseAssignment],
    relative_to: Path | None = None,
    at_risk_percent: float | None = None,
) -> str:
    """Render a ``course.toml``. The one definition of the file we write.

    Paths are written **relative to the course file** when ``relative_to`` is
    given and they sit underneath it — that is what makes the whole course
    folder movable and shareable, which `load_course_config` already assumes.
    An assignment stored elsewhere on disk keeps its absolute path rather than a
    fragile `../../..` chain.

    The comments in the output are part of the artifact, not decoration: this
    file is hand-edited afterwards, and the next person to open it needs to know
    what ``weight`` means without finding the docs.
    """
    lines = [
        "# Opengrader course gradebook.",
        "#   uv run course-grades --course course.toml",
        "#",
        "# weight = how much an assignment counts toward the final grade.",
        "# Weights are relative, so 1/1/2 and 25/25/50 describe the same course.",
        "# An assignment a student has no graded work for is left out of their",
        '# average; add  missing = "zero"  to count it against them instead.',
        "",
        f'name = "{_escape_toml(name.strip() or "Course")}"',
    ]
    if at_risk_percent is not None:
        lines.append(f"at_risk_percent = {at_risk_percent:g}")

    shares = weight_shares([a.weight for a in assignments])
    for assignment, share in zip(assignments, shares):
        lines.extend(["", "[[assignment]]"])
        lines.append(f'name = "{_escape_toml(assignment.name)}"')
        lines.append(
            f'path = "{_escape_toml(_relative_path(assignment.path, relative_to))}"'
        )
        # The share is a comment, not a key: it is derived from the weights and
        # would go stale the moment one is edited by hand.
        lines.append(f"weight = {assignment.weight:g}  # {share:.0%} of the grade")
        if assignment.solutions is not None:
            lines.append(
                "solutions = "
                f'"{_escape_toml(_relative_path(assignment.solutions, relative_to))}"'
            )
        if assignment.missing != "skip":
            lines.append(f'missing = "{_escape_toml(assignment.missing)}"')
    return "\n".join(lines) + "\n"


def _relative_path(path: Path, relative_to: Path | None) -> str:
    if relative_to is None:
        return str(path)
    try:
        return str(path.resolve().relative_to(relative_to.resolve()))
    except (ValueError, OSError):
        # Outside the course folder: an absolute path is honest and stable,
        # where a computed `../../` chain would break as soon as it moved.
        return str(path)


def write_course_toml(
    output_path: str | Path,
    *,
    name: str,
    assignments: list[CourseAssignment],
    at_risk_percent: float | None = None,
) -> Path:
    """Write a course file, with its assignment paths relative to itself."""
    output_path = Path(output_path).expanduser()
    if output_path.is_dir():
        output_path = output_path / COURSE_FILENAME
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        render_course_toml(
            name=name,
            assignments=assignments,
            relative_to=output_path.parent,
            at_risk_percent=at_risk_percent,
        ),
        encoding="utf-8",
    )
    return output_path

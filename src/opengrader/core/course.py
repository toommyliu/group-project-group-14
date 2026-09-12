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

DEFAULT_GRADE_SCALE: tuple[tuple[str, float], ...] = (
    ("A", 90.0),
    ("B", 80.0),
    ("C", 70.0),
    ("D", 60.0),
    ("F", 0.0),
)

DEFAULT_AT_RISK_PERCENT = 70.0

MissingPolicy = str
_MISSING_POLICIES = ("skip", "zero")


class CourseConfigError(ValueError):
    pass


@dataclass(frozen=True)
class CourseAssignment:
    name: str
    path: Path
    weight: float
    solutions: Path | None = None
    missing: MissingPolicy = "skip"


@dataclass(frozen=True)
class CourseConfig:
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
    if percent is None:
        return "-"
    for letter, minimum in scale:
        if percent >= minimum:
            return letter
    return scale[-1][0] if scale else "-"


@dataclass(frozen=True)
class AssignmentScore:
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
    student_id = document.student_id.strip()
    if student_id:
        return student_id
    return f"name:{document.student_name.strip().casefold()}"


def load_assignment(assignment: CourseAssignment) -> LoadedAssignment:
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


@dataclass(frozen=True)
class StudentCourseGrade:
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
        return tuple(
            (name, score.percent)
            for name, score in self.scores.items()
            if score.percent is not None
        )

    def direction(self) -> float | None:
        points = [percent for _, percent in self.trend()]
        if len(points) < 2:
            return None
        middle = len(points) // 2
        first = points[:middle] or points[:1]
        second = points[middle:]
        return sum(second) / len(second) - sum(first) / len(first)


@dataclass(frozen=True)
class CourseOutcomeMastery:
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


def weight_shares(weights: list[float]) -> list[float]:
    total = sum(w for w in weights if w > 0)
    if total <= 0:
        return [0.0 for _ in weights]
    return [(w / total if w > 0 else 0.0) for w in weights]


_GENERIC_SUBMISSION_DIR_NAMES = {
    "student-submissions",
    "student_submissions",
    "submissions",
    "subs",
    "out",
}

LIKELY_SOLUTION_NAMES = (
    "solutions.md",
    "solution.md",
    "answer-key.md",
    "answer_key.md",
    "qa.md",
)

_DISCOVERY_DEPTH = 3


def _escape_toml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def humanize_assignment_name(path: Path) -> str:
    directory = path if path.name else path.parent
    name = directory.name
    if name.lower() in _GENERIC_SUBMISSION_DIR_NAMES and directory.parent.name:
        name = directory.parent.name
    cleaned = name.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return "Assignment"
    return " ".join(
        word.capitalize() if word.islower() else word for word in cleaned.split()
    )


def find_solutions_near(submissions: Path) -> Path | None:
    for directory in (submissions, submissions.parent):
        try:
            if not directory.is_dir():
                continue
        except OSError:
            continue
        for candidate in LIKELY_SOLUTION_NAMES:
            path = directory / candidate
            if path.is_file():
                return path
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
    name: str
    path: Path
    solutions: Path | None


def discover_course_assignments(root: str | Path) -> list[DiscoveredAssignment]:
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
            continue
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
        return str(path)


def write_course_toml(
    output_path: str | Path,
    *,
    name: str,
    assignments: list[CourseAssignment],
    at_risk_percent: float | None = None,
) -> Path:
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

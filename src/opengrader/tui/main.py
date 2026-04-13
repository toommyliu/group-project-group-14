import os
import sys
from pathlib import Path

from opengrader.core import get_database_path
from opengrader.core.roster import read_roster
from opengrader.core.courses import StudentInput, build_sqlite_course_services
from opengrader.tui.tui import TuiApp

# https://github.com/Textualize/textual/blob/f044be2a115a1f3655b8de9f734d2ba57ab2207e/src/textual/features.py#L10
_TEXTUAL_ENV_VAR = "TEXTUAL"
_DEVTOOLS_FEATURE = "devtools"
_SKIP_BOOTSTRAP_ENV_VAR = "OPENGRADER_SKIP_BOOTSTRAP"
_ROSTER_PATH = Path(__file__).resolve().parents[3] / "data" / "roster.csv"


def _enable_textual_feature(feature: str) -> None:
    features = {
        value.strip().lower()
        for value in os.environ.get(_TEXTUAL_ENV_VAR, "").split(",")
        if value.strip()
    }
    features.add(feature)
    os.environ[_TEXTUAL_ENV_VAR] = ",".join(sorted(features))


def _split_roster_name(name: str) -> tuple[str, str]:
    cleaned = name.strip()
    if not cleaned:
        return ("Unknown", "Student")
    if "," in cleaned:
        last_name, first_name = (part.strip() for part in cleaned.split(",", 1))
        if first_name and last_name:
            return (first_name, last_name)

    parts = [part for part in cleaned.split() if part]
    if len(parts) == 1:
        return (parts[0], "Student")
    return (" ".join(parts[:-1]), parts[-1])


def _load_bootstrap_students(roster_path: Path = _ROSTER_PATH) -> list[StudentInput]:
    try:
        rows = read_roster(str(roster_path))
    except OSError:
        rows = []

    students: list[StudentInput] = []
    seen_sids: set[str] = set()
    for index, row in enumerate(rows):
        line = index + 2  # account for header row
        sid = (row.get("sid") or "").strip()
        if not sid:
            print(
                f"roster: line {line}: skipping row with missing sid", file=sys.stderr
            )
            continue
        if sid in seen_sids:
            print(
                f"roster: line {line}: skipping duplicate sid {sid!r}", file=sys.stderr
            )
            continue

        seen_sids.add(sid)
        first_name, last_name = _split_roster_name(row.get("name", ""))
        students.append(
            StudentInput(
                sid=sid,
                first_name=first_name,
                last_name=last_name,
                email=(row.get("email") or "").strip() or None,
            )
        )

    return students


def _bootstrap_default_workspace(db_path: str | Path) -> None:
    if os.environ.get(_SKIP_BOOTSTRAP_ENV_VAR, "").strip().lower() in {
        "1",
        "true",
        "yes",
    }:
        return

    services = build_sqlite_course_services(db_path)
    if services.courses.list_courses():
        return

    course = services.courses.create_course(
        code="CMPE-195",
        name="Senior Design Project",
        term="Spring 2026",
        section="01",
    )

    for title, assignment_type, max_points in [
        ("HW 1", "assignment", 100),
        ("Midterm", "exam", 100),
    ]:
        services.assignments.create_assignment(
            course_id=course.id,
            assignment_type=assignment_type,
            title=title,
            max_points=max_points,
        )

    students = _load_bootstrap_students()
    if students:
        services.enrollments.enroll_students(course.id, students)


def run_app() -> None:
    db_path = get_database_path()
    _bootstrap_default_workspace(db_path)
    TuiApp(db_path=db_path).run()


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()

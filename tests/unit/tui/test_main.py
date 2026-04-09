from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from opengrader.tui.main import (
    _bootstrap_default_workspace,
    _load_bootstrap_students,
    _split_roster_name,
    run_app,
)


class _FakeCoursesService:
    def __init__(self, existing_courses: list[object] | None = None) -> None:
        self._courses = list(existing_courses or [])
        self.created: list[dict[str, object]] = []

    def list_courses(self) -> list[object]:
        return list(self._courses)

    def create_course(self, code: str, name: str, term: str, section: str):
        course = SimpleNamespace(
            id=7,
            code=code,
            name=name,
            term=term,
            section=section,
        )
        self._courses.append(course)
        self.created.append(
            {
                "code": code,
                "name": name,
                "term": term,
                "section": section,
            }
        )
        return course


class _FakeAssignmentsService:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create_assignment(
        self,
        *,
        course_id: int,
        assignment_type: str,
        title: str,
        max_points: int,
    ) -> None:
        self.created.append(
            {
                "course_id": course_id,
                "assignment_type": assignment_type,
                "title": title,
                "max_points": max_points,
            }
        )


class _FakeEnrollmentsService:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def enroll_students(self, course_id: int, students: list[object]) -> None:
        self.calls.append({"course_id": course_id, "students": students})


def test_bootstrap_default_workspace_seeds_when_database_is_empty() -> None:
    services = SimpleNamespace(
        courses=_FakeCoursesService(),
        assignments=_FakeAssignmentsService(),
        enrollments=_FakeEnrollmentsService(),
    )

    with patch(
        "opengrader.tui.main.build_sqlite_course_services",
        return_value=services,
    ):
        _bootstrap_default_workspace(Path("demo.db"))

    assert len(services.courses.created) == 1
    assert [item["title"] for item in services.assignments.created] == [
        "HW 1",
        "Midterm",
    ]
    assert len(services.enrollments.calls) == 1
    students = services.enrollments.calls[0]["students"]
    assert len(students) == 4
    assert students[0].sid == "12345"
    assert students[0].first_name == "Example"
    assert students[0].last_name == "Student"


def test_bootstrap_default_workspace_skips_when_courses_exist() -> None:
    services = SimpleNamespace(
        courses=_FakeCoursesService(existing_courses=[object()]),
        assignments=_FakeAssignmentsService(),
        enrollments=_FakeEnrollmentsService(),
    )

    with patch(
        "opengrader.tui.main.build_sqlite_course_services",
        return_value=services,
    ):
        _bootstrap_default_workspace(Path("demo.db"))

    assert services.courses.created == []
    assert services.assignments.created == []
    assert services.enrollments.calls == []


def test_run_app_bootstraps_database_before_launch() -> None:
    with patch("opengrader.tui.main._bootstrap_default_workspace") as bootstrap_mock:
        with patch("opengrader.tui.main.TuiApp.run", autospec=True) as run_mock:
            run_app()

    bootstrap_mock.assert_called_once()
    assert run_mock.call_count == 1


def test_split_roster_name_supports_comma_format() -> None:
    assert _split_roster_name("Lovelace, Ada") == ("Ada", "Lovelace")


def test_load_bootstrap_students_skips_rows_with_missing_sid(tmp_path, capsys) -> None:
    roster_path = tmp_path / "roster.csv"
    roster_path.write_text(
        "name,email,section,course,sid,grade\n"
        "No SID Student,nosid@example.com,1,CMPE195,,A\n"
        "Valid Student,valid@example.com,1,CMPE195,100000001,A\n",
        encoding="utf-8",
    )

    students = _load_bootstrap_students(roster_path)

    assert len(students) == 1
    assert students[0].sid == "100000001"
    assert "missing sid" in capsys.readouterr().err


def test_load_bootstrap_students_skips_duplicate_sids(tmp_path, capsys) -> None:
    roster_path = tmp_path / "roster.csv"
    roster_path.write_text(
        "name,email,section,course,sid,grade\n"
        "Alice,alice@example.com,1,CMPE195,100000001,A\n"
        "Alice Duplicate,alice2@example.com,1,CMPE195,100000001,A\n"
        "Bob,bob@example.com,1,CMPE195,100000002,B\n",
        encoding="utf-8",
    )

    students = _load_bootstrap_students(roster_path)

    assert len(students) == 2
    assert [s.sid for s in students] == ["100000001", "100000002"]
    assert "duplicate sid" in capsys.readouterr().err


def test_load_bootstrap_students_reads_repo_roster_format(tmp_path) -> None:
    roster_path = tmp_path / "roster.csv"
    roster_path.write_text(
        "name,email,section,course,sid,grade\n"
        "\"Lovelace, Ada\",ada@example.com,1,CMPE195,100000001,A\n"
        "Grace Hopper,grace@example.com,1,CMPE195,100000002,A\n",
        encoding="utf-8",
    )

    students = _load_bootstrap_students(roster_path)

    assert [student.sid for student in students] == ["100000001", "100000002"]
    assert students[0].first_name == "Ada"
    assert students[0].last_name == "Lovelace"
    assert students[0].email == "ada@example.com"
    assert students[1].first_name == "Grace"
    assert students[1].last_name == "Hopper"
    assert students[1].email == "grace@example.com"

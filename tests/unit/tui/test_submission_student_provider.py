from opengrader.tui.data.sqlite_provider import SqliteProvider
from opengrader.tui.data.stub_data_provider import StubDataProvider

from .fakes import DEFAULT_IMAGE_PATH


def test_stub_provider_lists_students_from_submission_markdown() -> None:
    provider = StubDataProvider(DEFAULT_IMAGE_PATH)

    students = provider.list_students(course_id=1)

    assert [student.full_name for student in students] == ["Tommy", "Andy"]


def test_sqlite_provider_lists_students_from_submission_markdown() -> None:
    provider = SqliteProvider(DEFAULT_IMAGE_PATH, db_path="demo.db")

    students = provider.list_students(course_id=1)

    assert [student.full_name for student in students] == ["Tommy", "Andy"]

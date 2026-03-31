from __future__ import annotations

from pathlib import Path

from local_grader.core.courses.contracts.transaction import CoursesTransaction
from local_grader.core.courses.infrastructure.sqlite.connection import (
    create_sqlite_connection,
)
from local_grader.core.courses.infrastructure.sqlite.stores import (
    SqliteAssessmentStore,
    SqliteCourseStore,
    SqliteEnrollmentStore,
    SqliteGradeStore,
    SqliteStudentStore,
)


class SqliteCoursesTransaction(CoursesTransaction):
    def __init__(self, db_path: str | Path, immediate: bool = False) -> None:
        self._db_path = Path(db_path)
        self._immediate = immediate
        self._conn = None
        self.courses = None
        self.students = None
        self.enrollments = None
        self.assessments = None
        self.grades = None

    def __enter__(self) -> "SqliteCoursesTransaction":
        self._conn = create_sqlite_connection(self._db_path)
        self._conn.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")
        self.courses = SqliteCourseStore(self._conn)
        self.students = SqliteStudentStore(self._conn)
        self.enrollments = SqliteEnrollmentStore(self._conn)
        self.assessments = SqliteAssessmentStore(self._conn)
        self.grades = SqliteGradeStore(self._conn)
        return self

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        self._conn.rollback()

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._conn is None:
            return
        try:
            if exc_type is not None:
                self.rollback()
        finally:
            self._conn.close()

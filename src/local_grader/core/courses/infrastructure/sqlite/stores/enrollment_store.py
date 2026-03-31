from __future__ import annotations

import sqlite3

from local_grader.core.courses.domain.entities import Enrollment, Student

from ._time import parse_datetime


def _row_to_student(row: sqlite3.Row) -> Student:
    return Student(
        id=row["id"],
        sid=row["sid"],
        name=row["name"],
        email=row["email"],
        created_at=parse_datetime(row["created_at"]),
    )


def _row_to_enrollment(row: sqlite3.Row) -> Enrollment:
    return Enrollment(
        course_id=row["course_id"],
        student_id=row["student_id"],
        created_at=parse_datetime(row["created_at"]),
    )


class SqliteEnrollmentStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def enroll_students(
        self, course_id: int, student_ids: list[int]
    ) -> list[Enrollment]:
        if not student_ids:
            return []

        for student_id in student_ids:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO enrollments(course_id, student_id)
                VALUES (?, ?)
                """,
                (course_id, student_id),
            )

        placeholders = ",".join("?" for _ in student_ids)
        rows = self._conn.execute(
            f"""
            SELECT course_id, student_id, created_at
            FROM enrollments
            WHERE course_id = ?
              AND student_id IN ({placeholders})
            ORDER BY student_id
            """,
            (course_id, *student_ids),
        ).fetchall()
        return [_row_to_enrollment(row) for row in rows]

    def is_student_enrolled(self, course_id: int, student_id: int) -> bool:
        row = self._conn.execute(
            """
            SELECT 1
            FROM enrollments
            WHERE course_id = ? AND student_id = ?
            """,
            (course_id, student_id),
        ).fetchone()
        return row is not None

    def list_students_for_course(self, course_id: int) -> list[Student]:
        rows = self._conn.execute(
            """
            SELECT s.id, s.sid, s.name, s.email, s.created_at
            FROM students s
            INNER JOIN enrollments e ON e.student_id = s.id
            WHERE e.course_id = ?
            ORDER BY s.name ASC, s.sid ASC
            """,
            (course_id,),
        ).fetchall()
        return [_row_to_student(row) for row in rows]

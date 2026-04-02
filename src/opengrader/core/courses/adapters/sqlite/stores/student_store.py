from __future__ import annotations

import sqlite3

from opengrader.core.courses.domain.entities import Student, StudentInput

from ._time import parse_datetime


def _row_to_student(row: sqlite3.Row) -> Student:
    return Student(
        id=row["id"],
        sid=row["sid"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        middle_name=row["middle_name"],
        email=row["email"],
        created_at=parse_datetime(row["created_at"]),
    )


class SqliteStudentStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def upsert_students(self, students: list[StudentInput]) -> list[Student]:
        if not students:
            return []

        for student in students:
            self._conn.execute(
                """
                INSERT INTO students(sid, first_name, last_name, middle_name, email)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(sid) DO UPDATE SET
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    middle_name=excluded.middle_name,
                    email=excluded.email
                """,
                (
                    student.sid,
                    student.first_name,
                    student.last_name,
                    student.middle_name,
                    student.email,
                ),
            )

        sid_values = [s.sid for s in students]
        placeholders = ",".join("?" for _ in sid_values)
        rows = self._conn.execute(
            f"""
            SELECT id, sid, first_name, last_name, middle_name, email, created_at
            FROM students
            WHERE sid IN ({placeholders})
            ORDER BY id
            """,
            sid_values,
        ).fetchall()

        by_sid = {row["sid"]: _row_to_student(row) for row in rows}
        return [by_sid[s.sid] for s in students if s.sid in by_sid]

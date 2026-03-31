from __future__ import annotations

import sqlite3

from local_grader.core.courses.domain.entities import Student, StudentInput

from ._time import parse_datetime


def _row_to_student(row: sqlite3.Row) -> Student:
    return Student(
        id=row["id"],
        sid=row["sid"],
        name=row["name"],
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
                INSERT INTO students(sid, name, email)
                VALUES (?, ?, ?)
                ON CONFLICT(sid) DO UPDATE SET
                    name=excluded.name,
                    email=excluded.email
                """,
                (student.sid, student.name, student.email),
            )

        sid_values = [s.sid for s in students]
        placeholders = ",".join("?" for _ in sid_values)
        rows = self._conn.execute(
            f"""
            SELECT id, sid, name, email, created_at
            FROM students
            WHERE sid IN ({placeholders})
            ORDER BY id
            """,
            sid_values,
        ).fetchall()

        by_sid = {row["sid"]: _row_to_student(row) for row in rows}
        return [by_sid[s.sid] for s in students if s.sid in by_sid]

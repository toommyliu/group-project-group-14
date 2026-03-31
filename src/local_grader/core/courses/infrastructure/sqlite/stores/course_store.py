from __future__ import annotations

import sqlite3

from local_grader.core.courses.domain.entities import Course

from ._time import parse_datetime


def _row_to_course(row: sqlite3.Row) -> Course:
    return Course(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        term=row["term"],
        section=row["section"],
        created_at=parse_datetime(row["created_at"]),
    )


class SqliteCourseStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(self, code: str, name: str, term: str, section: str) -> Course:
        cursor = self._conn.execute(
            """
            INSERT INTO courses(code, name, term, section)
            VALUES (?, ?, ?, ?)
            """,
            (code, name, term, section),
        )
        course_id = int(cursor.lastrowid)
        row = self._conn.execute(
            "SELECT id, code, name, term, section, created_at FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone()
        return _row_to_course(row)

    def get_by_id(self, course_id: int) -> Course | None:
        row = self._conn.execute(
            "SELECT id, code, name, term, section, created_at FROM courses WHERE id = ?",
            (course_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_course(row)

    def get_by_key(self, code: str, term: str, section: str) -> Course | None:
        row = self._conn.execute(
            """
            SELECT id, code, name, term, section, created_at
            FROM courses
            WHERE code = ? AND term = ? AND section = ?
            """,
            (code, term, section),
        ).fetchone()
        if row is None:
            return None
        return _row_to_course(row)

    def list_all(self) -> list[Course]:
        rows = self._conn.execute(
            """
            SELECT id, code, name, term, section, created_at
            FROM courses
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
        return [_row_to_course(row) for row in rows]

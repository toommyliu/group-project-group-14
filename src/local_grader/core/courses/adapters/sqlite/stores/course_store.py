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
        self._conn.execute(
            """
            INSERT INTO course_catalogs(code, name)
            VALUES (?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name
            """,
            (code, name),
        )

        cursor = self._conn.execute(
            """
            INSERT INTO courses(catalog_code, term, section)
            VALUES (?, ?, ?)
            """,
            (code, term, section),
        )
        course_id = int(cursor.lastrowid)
        row = self._conn.execute(
            """
            SELECT c.id, cc.code, cc.name, c.term, c.section, c.created_at
            FROM courses c
            INNER JOIN course_catalogs cc ON cc.code = c.catalog_code
            WHERE c.id = ?
            """,
            (course_id,),
        ).fetchone()
        return _row_to_course(row)

    def get_by_id(self, course_id: int) -> Course | None:
        row = self._conn.execute(
            """
            SELECT c.id, cc.code, cc.name, c.term, c.section, c.created_at
            FROM courses c
            INNER JOIN course_catalogs cc ON cc.code = c.catalog_code
            WHERE c.id = ?
            """,
            (course_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_course(row)

    def get_by_key(self, code: str, term: str, section: str) -> Course | None:
        row = self._conn.execute(
            """
            SELECT c.id, cc.code, cc.name, c.term, c.section, c.created_at
            FROM courses c
            INNER JOIN course_catalogs cc ON cc.code = c.catalog_code
            WHERE cc.code = ? AND c.term = ? AND c.section = ?
            """,
            (code, term, section),
        ).fetchone()
        if row is None:
            return None
        return _row_to_course(row)

    def list_all(self) -> list[Course]:
        rows = self._conn.execute(
            """
            SELECT c.id, cc.code, cc.name, c.term, c.section, c.created_at
            FROM courses c
            INNER JOIN course_catalogs cc ON cc.code = c.catalog_code
            ORDER BY c.created_at DESC, c.id DESC
            """
        ).fetchall()
        return [_row_to_course(row) for row in rows]

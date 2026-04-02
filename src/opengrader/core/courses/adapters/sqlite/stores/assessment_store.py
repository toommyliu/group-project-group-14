from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from opengrader.core.courses.domain.entities import Assessment, AssessmentType

from ._time import parse_datetime


def _row_to_assessment(row: sqlite3.Row) -> Assessment:
    return Assessment(
        id=row["id"],
        course_id=row["course_id"],
        type=row["type"],
        title=row["title"],
        max_points=Decimal(row["max_points"]),
        due_at=parse_datetime(row["due_at"]) if row["due_at"] else None,
        created_at=parse_datetime(row["created_at"]),
    )


class SqliteAssessmentStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        course_id: int,
        assessment_type: AssessmentType,
        title: str,
        max_points: Decimal,
        due_at: datetime | None,
    ) -> Assessment:
        cursor = self._conn.execute(
            """
            INSERT INTO assessments(course_id, type_code, title, max_points, due_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                course_id,
                assessment_type,
                title,
                str(max_points),
                due_at.isoformat() if due_at else None,
            ),
        )
        assessment_id = int(cursor.lastrowid)
        row = self._conn.execute(
            """
            SELECT id, course_id, type_code AS type, title, max_points, due_at, created_at
            FROM assessments
            WHERE id = ?
            """,
            (assessment_id,),
        ).fetchone()
        return _row_to_assessment(row)

    def get_by_id(self, assessment_id: int) -> Assessment | None:
        row = self._conn.execute(
            """
            SELECT id, course_id, type_code AS type, title, max_points, due_at, created_at
            FROM assessments
            WHERE id = ?
            """,
            (assessment_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_assessment(row)

    def list_for_course(self, course_id: int) -> list[Assessment]:
        rows = self._conn.execute(
            """
            SELECT id, course_id, type_code AS type, title, max_points, due_at, created_at
            FROM assessments
            WHERE course_id = ?
            ORDER BY CASE WHEN due_at IS NULL THEN 1 ELSE 0 END, due_at ASC, created_at ASC, id ASC
            """,
            (course_id,),
        ).fetchall()
        return [_row_to_assessment(row) for row in rows]

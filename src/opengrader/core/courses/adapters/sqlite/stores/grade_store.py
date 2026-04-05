from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from opengrader.core.courses.domain.entities import Grade
from opengrader.core.courses.domain.errors import GradeConflictError

from ._time import parse_datetime


def _row_to_grade(row: sqlite3.Row) -> Grade:
    return Grade(
        assignment_id=row["assignment_id"],
        student_id=row["student_id"],
        points_awarded=Decimal(row["points_awarded"]),
        updated_at=parse_datetime(row["updated_at"]),
        version=row["version"],
    )


class SqliteGradeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, assignment_id: int, student_id: int) -> Grade | None:
        row = self._conn.execute(
            """
            SELECT assignment_id, student_id, points_awarded,
                   updated_at, version
            FROM grades
            WHERE assignment_id = ? AND student_id = ?
            """,
            (assignment_id, student_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_grade(row)

    def upsert(
        self,
        assignment_id: int,
        student_id: int,
        points_awarded: Decimal,
        updated_at: datetime,
        expected_version: int | None,
    ) -> Grade:
        existing = self.get(assignment_id=assignment_id, student_id=student_id)
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO grades(
                    assignment_id, student_id, points_awarded,
                    updated_at, version
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (
                    assignment_id,
                    student_id,
                    str(points_awarded),
                    updated_at.isoformat(),
                ),
            )
            created = self.get(assignment_id=assignment_id, student_id=student_id)
            if created is None:
                raise RuntimeError("grade insert succeeded but row was not found")
            return created

        if expected_version is not None and expected_version != existing.version:
            raise GradeConflictError(
                "grade update conflict: expected_version does not match current version"
            )

        target_version = existing.version + 1
        cursor = self._conn.execute(
            """
            UPDATE grades
            SET points_awarded = ?,
                updated_at = ?,
                version = ?
            WHERE assignment_id = ? AND student_id = ? AND version = ?
            """,
            (
                str(points_awarded),
                updated_at.isoformat(),
                target_version,
                assignment_id,
                student_id,
                existing.version,
            ),
        )
        if cursor.rowcount == 0:
            raise GradeConflictError("grade update conflict: row changed during update")

        updated = self.get(assignment_id=assignment_id, student_id=student_id)
        if updated is None:
            raise RuntimeError("grade update succeeded but row was not found")
        return updated

    def list_for_course(self, course_id: int) -> list[Grade]:
        rows = self._conn.execute(
            """
            SELECT g.assignment_id, g.student_id, g.points_awarded,
                   g.updated_at, g.version
            FROM grades g
            INNER JOIN assignments a ON a.id = g.assignment_id
            WHERE a.course_id = ?
            ORDER BY g.student_id ASC, g.assignment_id ASC
            """,
            (course_id,),
        ).fetchall()
        return [_row_to_grade(row) for row in rows]

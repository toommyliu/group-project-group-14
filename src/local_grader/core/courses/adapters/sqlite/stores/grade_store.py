from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from local_grader.core.courses.domain.entities import Grade
from local_grader.core.courses.domain.errors import GradeConflictError

from ._time import parse_datetime


def _row_to_grade(row: sqlite3.Row) -> Grade:
    return Grade(
        assessment_id=row["assessment_id"],
        student_id=row["student_id"],
        points_awarded=Decimal(row["points_awarded"]),
        updated_at=parse_datetime(row["updated_at"]),
        version=row["version"],
    )


class SqliteGradeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def get(self, assessment_id: int, student_id: int) -> Grade | None:
        row = self._conn.execute(
            """
            SELECT assessment_id, student_id, points_awarded,
                   updated_at, version
            FROM grades
            WHERE assessment_id = ? AND student_id = ?
            """,
            (assessment_id, student_id),
        ).fetchone()
        if row is None:
            return None
        return _row_to_grade(row)

    def upsert(
        self,
        assessment_id: int,
        student_id: int,
        points_awarded: Decimal,
        updated_at: datetime,
        expected_version: int | None,
    ) -> Grade:
        existing = self.get(assessment_id=assessment_id, student_id=student_id)
        if existing is None:
            self._conn.execute(
                """
                INSERT INTO grades(
                    assessment_id, student_id, points_awarded,
                    updated_at, version
                ) VALUES (?, ?, ?, ?, 1)
                """,
                (
                    assessment_id,
                    student_id,
                    str(points_awarded),
                    updated_at.isoformat(),
                ),
            )
            created = self.get(assessment_id=assessment_id, student_id=student_id)
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
            WHERE assessment_id = ? AND student_id = ? AND version = ?
            """,
            (
                str(points_awarded),
                updated_at.isoformat(),
                target_version,
                assessment_id,
                student_id,
                existing.version,
            ),
        )
        if cursor.rowcount == 0:
            raise GradeConflictError("grade update conflict: row changed during update")

        updated = self.get(assessment_id=assessment_id, student_id=student_id)
        if updated is None:
            raise RuntimeError("grade update succeeded but row was not found")
        return updated

    def list_for_course(self, course_id: int) -> list[Grade]:
        rows = self._conn.execute(
            """
            SELECT g.assessment_id, g.student_id, g.points_awarded,
                   g.updated_at, g.version
            FROM grades g
            INNER JOIN assessments a ON a.id = g.assessment_id
            WHERE a.course_id = ?
            ORDER BY g.student_id ASC, g.assessment_id ASC
            """,
            (course_id,),
        ).fetchall()
        return [_row_to_grade(row) for row in rows]

from __future__ import annotations

import sqlite3

from opengrader.core.courses.domain.entities import AssignmentTemplate

from ._time import parse_datetime


def _row_to_assignment_template(row: sqlite3.Row) -> AssignmentTemplate:
    return AssignmentTemplate(
        id=row["id"],
        course_id=row["course_id"],
        name=row["name"],
        template_pdf_relpath=row["template_pdf_relpath"],
        spec_json_relpath=row["spec_json_relpath"],
        template_sha256=row["template_sha256"],
        spec_sha256=row["spec_sha256"],
        created_at=parse_datetime(row["created_at"]),
    )


class SqliteAssignmentTemplateStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def create(
        self,
        course_id: int,
        name: str,
        template_pdf_relpath: str,
        spec_json_relpath: str,
        template_sha256: str,
        spec_sha256: str,
    ) -> AssignmentTemplate:
        cursor = self._conn.execute(
            """
            INSERT INTO assignment_templates(
                course_id,
                name,
                template_pdf_relpath,
                spec_json_relpath,
                template_sha256,
                spec_sha256
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                course_id,
                name,
                template_pdf_relpath,
                spec_json_relpath,
                template_sha256,
                spec_sha256,
            ),
        )
        template_id = int(cursor.lastrowid)
        row = self._conn.execute(
            """
            SELECT id, course_id, name, template_pdf_relpath, spec_json_relpath,
                   template_sha256, spec_sha256, created_at
            FROM assignment_templates
            WHERE id = ?
            """,
            (template_id,),
        ).fetchone()
        return _row_to_assignment_template(row)

    def list_for_course(self, course_id: int) -> list[AssignmentTemplate]:
        rows = self._conn.execute(
            """
            SELECT id, course_id, name, template_pdf_relpath, spec_json_relpath,
                   template_sha256, spec_sha256, created_at
            FROM assignment_templates
            WHERE course_id = ?
            ORDER BY created_at DESC, id DESC
            """,
            (course_id,),
        ).fetchall()
        return [_row_to_assignment_template(row) for row in rows]

    def get_by_id(self, template_id: int) -> AssignmentTemplate | None:
        row = self._conn.execute(
            """
            SELECT id, course_id, name, template_pdf_relpath, spec_json_relpath,
                   template_sha256, spec_sha256, created_at
            FROM assignment_templates
            WHERE id = ?
            """,
            (template_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_assignment_template(row)

    def update_paths(
        self,
        *,
        template_id: int,
        template_pdf_relpath: str,
        spec_json_relpath: str,
    ) -> None:
        self._conn.execute(
            """
            UPDATE assignment_templates
            SET template_pdf_relpath = ?,
                spec_json_relpath = ?
            WHERE id = ?
            """,
            (template_pdf_relpath, spec_json_relpath, template_id),
        )

    def set_assignment_template(
        self,
        assignment_id: int,
        template_id: int,
    ) -> None:
        self._conn.execute(
            """
            UPDATE assignment_template_links
            SET is_current = 0
            WHERE assignment_id = ?
              AND is_current = 1
            """,
            (assignment_id,),
        )
        self._conn.execute(
            """
            INSERT INTO assignment_template_links(assignment_id, template_id, is_current)
            VALUES (?, ?, 1)
            """,
            (assignment_id, template_id),
        )

    def get_current_for_assignment(
        self,
        assignment_id: int,
    ) -> AssignmentTemplate | None:
        row = self._conn.execute(
            """
            SELECT t.id, t.course_id, t.name, t.template_pdf_relpath, t.spec_json_relpath,
                   t.template_sha256, t.spec_sha256, t.created_at
            FROM assignment_template_links l
            INNER JOIN assignment_templates t ON t.id = l.template_id
            WHERE l.assignment_id = ?
              AND l.is_current = 1
            ORDER BY l.id DESC
            LIMIT 1
            """,
            (assignment_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_assignment_template(row)

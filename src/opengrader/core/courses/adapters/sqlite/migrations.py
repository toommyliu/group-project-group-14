from __future__ import annotations

import sqlite3
from pathlib import Path

from opengrader.core.courses.domain.entities import ALLOWED_ASSIGNMENT_TYPES

from .connection import create_sqlite_connection

SCHEMA_VERSION = 2


def _drop_course_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS grades;
        DROP TABLE IF EXISTS assignments;
        DROP TABLE IF EXISTS assignment_types;
        DROP TABLE IF EXISTS enrollments;
        DROP TABLE IF EXISTS students;
        DROP TABLE IF EXISTS courses;
        DROP TABLE IF EXISTS course_catalogs;
        """
    )


def _apply_v2_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS course_catalogs (
            code TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            catalog_code TEXT NOT NULL,
            term TEXT NOT NULL,
            section TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(catalog_code, term, section),
            FOREIGN KEY(catalog_code) REFERENCES course_catalogs(code)
                ON DELETE RESTRICT ON UPDATE CASCADE
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sid TEXT NOT NULL UNIQUE,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            middle_name TEXT,
            email TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS enrollments (
            course_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (course_id, student_id),
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS assignment_types (
            code TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS assignments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            type_code TEXT NOT NULL,
            title TEXT NOT NULL,
            max_points TEXT NOT NULL,
            due_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
            FOREIGN KEY(type_code) REFERENCES assignment_types(code)
                ON DELETE RESTRICT ON UPDATE CASCADE
        );

        CREATE TABLE IF NOT EXISTS grades (
            assignment_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            points_awarded TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (assignment_id, student_id),
            FOREIGN KEY(assignment_id) REFERENCES assignments(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_courses_catalog_code
            ON courses(catalog_code);
        CREATE INDEX IF NOT EXISTS idx_assignments_course_id
            ON assignments(course_id);
        CREATE INDEX IF NOT EXISTS idx_assignments_type_code
            ON assignments(type_code);
        CREATE INDEX IF NOT EXISTS idx_enrollments_student_id
            ON enrollments(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_student_id
            ON grades(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_assignment_id
            ON grades(assignment_id);
        """
    )

    # pre-populate assignment_types with allowed types
    conn.executemany(
        "INSERT OR IGNORE INTO assignment_types(code) VALUES (?)",
        [(assignment_type,) for assignment_type in ALLOWED_ASSIGNMENT_TYPES],
    )


def initialize_sqlite_database(db_path: str | Path) -> None:
    conn = create_sqlite_connection(db_path)
    try:
        conn.execute("BEGIN")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        current_version = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
        ).fetchone()[0]

        if current_version < SCHEMA_VERSION:
            _drop_course_tables(conn)
            _apply_v2_schema(conn)
            conn.execute("DELETE FROM schema_migrations")
            conn.execute(
                "INSERT INTO schema_migrations(version) VALUES (?)",
                (SCHEMA_VERSION,),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

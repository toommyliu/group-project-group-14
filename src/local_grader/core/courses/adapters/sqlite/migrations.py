from __future__ import annotations

import sqlite3
from pathlib import Path

from local_grader.core.courses.domain.entities import ALLOWED_ASSESSMENT_TYPES

from .connection import create_sqlite_connection

SCHEMA_VERSION = 1


def _drop_course_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS grades;
        DROP TABLE IF EXISTS assessments;
        DROP TABLE IF EXISTS assessment_types;
        DROP TABLE IF EXISTS enrollments;
        DROP TABLE IF EXISTS students;
        DROP TABLE IF EXISTS courses;
        DROP TABLE IF EXISTS course_catalogs;
        """
    )


def _apply_v1_schema(conn: sqlite3.Connection) -> None:
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

        CREATE TABLE IF NOT EXISTS assessment_types (
            code TEXT PRIMARY KEY
        );

        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            type_code TEXT NOT NULL,
            title TEXT NOT NULL,
            max_points TEXT NOT NULL,
            due_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE,
            FOREIGN KEY(type_code) REFERENCES assessment_types(code)
                ON DELETE RESTRICT ON UPDATE CASCADE
        );

        CREATE TABLE IF NOT EXISTS grades (
            assessment_id INTEGER NOT NULL,
            student_id INTEGER NOT NULL,
            points_awarded TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (assessment_id, student_id),
            FOREIGN KEY(assessment_id) REFERENCES assessments(id) ON DELETE CASCADE,
            FOREIGN KEY(student_id) REFERENCES students(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_courses_catalog_code
            ON courses(catalog_code);
        CREATE INDEX IF NOT EXISTS idx_assessments_course_id
            ON assessments(course_id);
        CREATE INDEX IF NOT EXISTS idx_assessments_type_code
            ON assessments(type_code);
        CREATE INDEX IF NOT EXISTS idx_enrollments_student_id
            ON enrollments(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_student_id
            ON grades(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_assessment_id
            ON grades(assessment_id);
        """
    )

    # pre-populate assessment_types with allowed types
    conn.executemany(
        "INSERT OR IGNORE INTO assessment_types(code) VALUES (?)",
        [(assessment_type,) for assessment_type in ALLOWED_ASSESSMENT_TYPES],
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
            _apply_v1_schema(conn)
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

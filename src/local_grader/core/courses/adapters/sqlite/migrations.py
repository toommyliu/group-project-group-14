from __future__ import annotations

import sqlite3
from pathlib import Path

from .connection import create_sqlite_connection

SCHEMA_VERSION = 1

# TODO: maybe ORM?


def _apply_v1_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS courses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT NOT NULL,
            name TEXT NOT NULL,
            term TEXT NOT NULL,
            section TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(code, term, section)
        );

        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sid TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
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

        CREATE TABLE IF NOT EXISTS assessments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            course_id INTEGER NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('assignment', 'quiz', 'exam', 'other')),
            title TEXT NOT NULL,
            max_points TEXT NOT NULL,
            due_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
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

        CREATE INDEX IF NOT EXISTS idx_assessments_course_id
            ON assessments(course_id);
        CREATE INDEX IF NOT EXISTS idx_enrollments_student_id
            ON enrollments(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_student_id
            ON grades(student_id);
        CREATE INDEX IF NOT EXISTS idx_grades_assessment_id
            ON grades(assessment_id);
        """
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

        if current_version < 1:
            _apply_v1_schema(conn)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (1,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

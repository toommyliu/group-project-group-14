from __future__ import annotations

import argparse
import json
import random
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from opengrader.core import get_database_path
from opengrader.core.courses import StudentInput, build_sqlite_course_services

API_URL = "https://randomuser.me/api/"
DEFAULT_SEED_COURSES = [
    ("CMPE-195", "Senior Design", "Spring 2026", "01"),
    ("MATH-101", "Calculus I", "Spring 2026", "02"),
    ("ENGR-10", "Engineering Fundamentals", "Spring 2026", "03"),
]
DEFAULT_ASSIGNMENTS = [
    ("assignment", "HW 1"),
    ("assignment", "HW 2"),
    ("quiz", "Quiz 1"),
    ("exam", "Midterm"),
    ("assignment", "Project 1"),
]


def prepare_database_path(db_path: Path, clean: bool) -> Path:
    if clean and db_path.exists():
        print(f"Removing existing database at {db_path}")
        db_path.unlink()
    return db_path


def _build_student_from_randomuser_record(record: dict[str, Any]) -> StudentInput:
    login = record.get("login", {})
    sid = login.get("uuid")
    if not sid:
        sid = record.get("email") or f"generated-{uuid.uuid4().hex}"

    name_info = record.get("name", {})
    first_name = name_info.get("first", "").strip()
    last_name = name_info.get("last", "").strip()
    middle_name = name_info.get("middle")
    email = record.get("email")

    if not first_name or not last_name:
        raise ValueError("randomuser record is missing required name fields")

    return StudentInput(
        sid=str(sid),
        first_name=first_name,
        middle_name=middle_name if middle_name and middle_name.strip() else None,
        last_name=last_name,
        email=email,
    )


def fetch_students_from_api(count: int = 100) -> list[StudentInput]:
    request_url = f"{API_URL}?results={count}"
    try:
        with urllib.request.urlopen(request_url, timeout=20) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise RuntimeError("unable to fetch student data from randomuser API") from exc

    data = json.loads(raw)
    results = data.get("results")
    if not isinstance(results, list):
        raise RuntimeError("unexpected randomuser API response")

    students: list[StudentInput] = []
    for record in results[:count]:
        students.append(_build_student_from_randomuser_record(record))

    if len(students) < count:
        raise RuntimeError("randomuser API returned too few students")

    return students


def load_student_inputs(count: int = 100) -> list[StudentInput]:
    try:
        return fetch_students_from_api(count)
    except Exception:
        print(
            "Warning: failed to fetch student names from the randomuser API; using placeholder names.",
            file=sys.stderr,
        )
        students = []


def _create_seed_courses(services) -> list:
    courses = []
    for code, name, term, section in DEFAULT_SEED_COURSES:
        course = services.courses.create_course(
            code=code,
            name=name,
            term=term,
            section=section,
        )
        courses.append(course)
    return courses


def _create_seed_assignments(services, courses: list) -> int:
    total = 0
    for course in courses:
        assignments = DEFAULT_ASSIGNMENTS.copy()
        random.shuffle(assignments)
        for assignment_type, title in assignments[:4]:
            services.assignments.create_assignment(
                course_id=course.id,
                assignment_type=assignment_type,
                title=title,
                max_points=100,
            )
            total += 1
    return total


def seed_database(db_path: Path, student_count: int = 100) -> tuple[int, int, int]:
    services = build_sqlite_course_services(db_path)
    courses = _create_seed_courses(services)
    assignment_count = _create_seed_assignments(services, courses)
    students = load_student_inputs(student_count)

    for course in courses:
        services.enrollments.enroll_students(course.id, students)

    return len(courses), assignment_count, len(students)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed an OpenGrader SQLite course database with placeholder content."
    )
    parser.add_argument(
        "--clean",
        action="store_true",
        help="Delete the existing DB file before seeding.",
    )
    parser.add_argument(
        "--student-count",
        type=int,
        default=100,
        help="Number of student records to seed.",
    )
    return parser.parse_args()


def run_cli() -> int:
    args = parse_args()
    db_path = Path(get_database_path()).expanduser().resolve()
    prepare_database_path(db_path, clean=args.clean)

    print(f"Seeding database at {db_path}")
    course_count, assignment_count, student_count = seed_database(
        db_path, student_count=args.student_count
    )

    print(f"Created {course_count} courses.")
    print(f"Created {assignment_count} assignments.")
    print(f"Added {student_count} students and enrolled them in each course.")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli())

from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from datetime import datetime
from pathlib import Path
from typing import Optional

from opengrader.core import get_database_path
from opengrader.core.courses import (
    AssessmentNotFoundError,
    CourseAlreadyExistsError,
    GradeConflictError,
    InvalidPointsError,
    StudentInput,
    StudentNotEnrolledError,
    build_sqlite_course_services,
)


def setup_args() -> Namespace:
    parser = ArgumentParser(
        description="Interactive CLI for course grading core services.",
        epilog=(
            "examples:\n"
            " uv run course-demo\n"
            " uv run course-demo --db /tmp/mycourses.db\n"
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(get_database_path()),
        help="Path to SQLite DB file (default: ~/.opengrader/data/courses.db).",
    )
    parser.add_argument(
        "--no-delete",
        action="store_true",
        help="Do not delete the DB file after exiting the CLI.",
    )
    return parser.parse_args()


def safe_input(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def select_course_id(services) -> Optional[int]:
    courses = services.courses.list_courses()
    if not courses:
        print("No courses available.")
        return None
    print("Courses:")
    for c in courses:
        print(f"  {c.id}: {c.code} {c.name} [{c.term} {c.section}]")
    csv = safe_input("Enter course ID: ")
    if not csv:
        return None
    try:
        return int(csv)
    except ValueError:
        print("Invalid course id")
        return None


def select_assessment_id(services, course_id: int) -> Optional[int]:
    assessments = services.assessments.list_assessments(course_id)
    if not assessments:
        print("No assessments found for this course.")
        return None
    print("Assessments:")
    for a in assessments:
        due = a.due_at.isoformat() if a.due_at else "none"
        print(f"  {a.id}: {a.type} {a.title} (max {a.max_points}, due {due})")
    aid = safe_input("Enter assessment ID: ")
    if not aid:
        return None
    try:
        return int(aid)
    except ValueError:
        print("Invalid assessment id")
        return None


def select_student_id(services, course_id: int) -> Optional[int]:
    roster = services.enrollments.list_roster(course_id)
    if not roster:
        print("No students enrolled in this course.")
        return None
    print("Roster:")
    for s in roster:
        print(f"  {s.id}: {s.sid} {s.full_name} {s.email or ''}")
    sid = safe_input("Enter student id: ")
    if not sid:
        return None
    try:
        return int(sid)
    except ValueError:
        print("Invalid student id")
        return None


def parse_due_date(raw: str) -> Optional[datetime]:
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        print("Invalid datetime format. Use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS.")
        return None


def show_gradebook(services, course_id: int) -> None:
    gradebook = services.grading.get_course_gradebook(course_id)
    print("Gradebook:")
    print("student_sid | total_awarded | total_possible | percentage")
    for row in gradebook.rows:
        print(
            f"{row.student.sid} | {row.total_awarded} | {row.total_possible} | {row.percentage}"
        )


def run_cli() -> None:
    args = setup_args()
    db_path = Path(args.db).resolve()
    db_existed = db_path.exists()

    print(f"Using DB at: {db_path}")
    if db_existed:
        print(
            "Existing DB file detected; it will not be removed unless --no-delete is used."
        )

    services = build_sqlite_course_services(db_path)

    try:
        while True:
            print(f"DB: {db_path}")
            print("1) List courses")
            print("2) Create course")
            print("3) Enroll student(s)")
            print("4) List roster")
            print("5) Create assessment")
            print("6) List assessments")
            print("7) Assign grade")
            print("8) Show gradebook")
            print("9) Exit")
            choice = safe_input("Choose an option: ")
            if choice == "1":
                courses = services.courses.list_courses()
                if not courses:
                    print("No courses found.")
                for c in courses:
                    print(f"{c.id}: {c.code} {c.name} [{c.term} {c.section}]")
            elif choice == "2":
                code = safe_input("Course code: ")
                name = safe_input("Course name: ")
                term = safe_input("Term: ")
                section = safe_input("Section: ")
                try:
                    created = services.courses.create_course(
                        code=code, name=name, term=term, section=section
                    )
                    print(f"Created course id={created.id}.")
                except CourseAlreadyExistsError:
                    print("Course already exists.")
            elif choice == "3":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                students = []
                while True:
                    sid = safe_input("Student SID (blank to finish): ")
                    if not sid:
                        break
                    first_name = safe_input("Student first name: ")
                    middle_name = safe_input("Student middle name (optional): ")
                    last_name = safe_input("Student last name: ")
                    student_email = safe_input("Student email (optional): ")
                    students.append(
                        StudentInput(
                            sid=sid,
                            first_name=first_name,
                            middle_name=middle_name or None,
                            last_name=last_name,
                            email=student_email or None,
                        )
                    )
                if students:
                    enrolled = services.enrollments.enroll_students(course_id, students)
                    print(f"Enrolled {len(enrolled)} student(s).")
                else:
                    print("No students provided.")
            elif choice == "4":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                roster = services.enrollments.list_roster(course_id)
                for s in roster:
                    print(f"{s.id}: {s.sid} {s.full_name} {s.email or ''}")
            elif choice == "5":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                assessment_type = safe_input("Assessment type: ")
                title = safe_input("Title: ")
                max_points_raw = safe_input("Max points: ")
                due_raw = safe_input("Due date (ISO, blank for none): ")
                try:
                    max_points = float(max_points_raw)
                except ValueError:
                    print("Invalid max points")
                    continue
                due_at = parse_due_date(due_raw)
                services.assessments.create_assessment(
                    course_id=course_id,
                    assessment_type=assessment_type,
                    title=title,
                    max_points=max_points,
                    due_at=due_at,
                )
                print("Assessment created.")
            elif choice == "6":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                assessments = services.assessments.list_assessments(course_id)
                for a in assessments:
                    due = a.due_at.isoformat() if a.due_at else "<unset>"
                    print(f"{a.id}: {a.type} {a.title} max {a.max_points} due {due}")
            elif choice == "7":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                assessment_id = select_assessment_id(services, course_id)
                if not assessment_id:
                    continue
                student_id = select_student_id(services, course_id)
                if not student_id:
                    continue
                points_raw = safe_input("Points awarded: ")
                try:
                    points = float(points_raw)
                except ValueError:
                    print("Invalid points")
                    continue
                version_raw = safe_input("Expected version (optional): ")
                expected_version = int(version_raw) if version_raw.isdigit() else None
                try:
                    assigned = services.grading.assign_grade(
                        assessment_id=assessment_id,
                        student_id=student_id,
                        points_awarded=points,
                        expected_version=expected_version,
                    )
                    print(f"Grade assigned. version={assigned.version}")
                except StudentNotEnrolledError:
                    print("Student not enrolled")
                except AssessmentNotFoundError:
                    print("Assessment not found")
                except InvalidPointsError:
                    print("Invalid points")
                except GradeConflictError:
                    print("Grade conflict (optimistic lock failure)")
            elif choice == "8":
                course_id = select_course_id(services)
                if not course_id:
                    continue
                show_gradebook(services, course_id)
            elif choice == "9":
                print("Exiting.")
                break
            else:
                print("Unknown choice")
    except KeyboardInterrupt:
        print("\nInterrupted, exiting.")


if __name__ == "__main__":
    run_cli()

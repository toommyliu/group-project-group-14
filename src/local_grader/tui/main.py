from local_grader.core.courses.bootstrap import build_sqlite_course_services
from local_grader.tui.tui import TuiApp


def run_app() -> None:
    course_services = build_sqlite_course_services("local_grader.db")
    TuiApp(course_services=course_services).run()


if __name__ == "__main__":
    run_app()

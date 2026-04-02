from local_grader.tui.tui import TuiApp


def run_app() -> None:
    TuiApp(db_path="courses.db").run()


if __name__ == "__main__":
    run_app()

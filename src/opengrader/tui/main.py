from opengrader.core import get_database_path
from opengrader.tui.tui import TuiApp


def run_app() -> None:
    TuiApp(db_path=get_database_path()).run()


if __name__ == "__main__":
    run_app()

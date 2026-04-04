import os

from opengrader.core import get_database_path
from opengrader.tui.tui import TuiApp

# https://github.com/Textualize/textual/blob/f044be2a115a1f3655b8de9f734d2ba57ab2207e/src/textual/features.py#L10
_TEXTUAL_ENV_VAR = "TEXTUAL"
_DEVTOOLS_FEATURE = "devtools"


def _enable_textual_feature(feature: str) -> None:
    features = {
        value.strip().lower()
        for value in os.environ.get(_TEXTUAL_ENV_VAR, "").split(",")
        if value.strip()
    }
    features.add(feature)
    os.environ[_TEXTUAL_ENV_VAR] = ",".join(sorted(features))


def run_app() -> None:
    TuiApp(db_path=get_database_path()).run()


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()

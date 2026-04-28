from argparse import ArgumentParser
import os

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


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(description="Launch the Opengrader TUI.")
    parser.add_argument("--qa-file", help="Path to a QA markdown file.")
    parser.add_argument(
        "--submissions",
        help="Path to a .submission.md file or directory containing submission files.",
    )
    return parser


def run_app(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if bool(args.qa_file) != bool(args.submissions):
        parser.error("--qa-file and --submissions must be provided together")

    try:
        if args.qa_file:
            TuiApp(
                qa_file_path=args.qa_file,
                submissions_path=args.submissions,
            ).run()
            return

        TuiApp().run()
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")


def run_app_dev() -> None:
    _enable_textual_feature(_DEVTOOLS_FEATURE)
    run_app()


if __name__ == "__main__":
    run_app()

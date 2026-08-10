from argparse import ArgumentParser
import os

from opengrader.tui.terminal_graphics import (
    ENV_OVERRIDE,
    PROTOCOL_CHOICES,
    GraphicsOverrideError,
    configure_graphics,
    graphics_report,
)
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
    parser.add_argument(
        "--graphics",
        choices=PROTOCOL_CHOICES,
        default="auto",
        help=(
            "Terminal image protocol for answer crops. 'auto' detects it; "
            "force one when detection guesses wrong (also settable via "
            f"{ENV_OVERRIDE})."
        ),
    )
    parser.add_argument(
        "--check-graphics",
        action="store_true",
        help=(
            "Print which image protocol this terminal will use (and why), "
            "then exit. Paste this into a bug report."
        ),
    )
    return parser


def run_app(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    # Settle the graphics protocol before the app starts: probing the terminal
    # is impossible once Textual owns stdin, and every image widget snapshots
    # the answer when it is built.
    try:
        capability = configure_graphics(args.graphics)
    except GraphicsOverrideError as exc:
        parser.error(str(exc))
        return

    if args.check_graphics:
        parser.exit(0, graphics_report(capability) + "\n")
        return

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

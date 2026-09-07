"""Check that this machine has everything Opengrader needs, and say how to fix it.

The first command to run on a new machine, and the first thing to ask for when
something misbehaves: one report covering the Python install, the packages, the
Tesseract binary, the Ollama server and its models, the optional integrations,
and whether this terminal can draw answer crops — each finding naming the exact
command that fixes it.

Strictly read-only apart from ``--smoke``, which writes a template PDF into a
temporary directory and deletes it again.

Engine: ``core/environment.py``.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter

from opengrader.core import ai_config
from opengrader.core.environment import (
    EnvironmentCheck,
    EnvironmentReport,
    build_environment_report,
)

EPILOG = """
examples:
  uv run doctor                      # check everything
  uv run doctor --smoke              # also build a template PDF end to end
  uv run doctor --json               # machine-readable, for a bug report
  uv run doctor --fail-on-problem    # exit 2 if a core capability is broken (CI)
  uv run doctor --ollama-model llama3.1:8b
"""

# Glyph per status, so the eye can find the failures without reading the words.
_GLYPHS: dict[str, str] = {
    "ready": "OK  ",
    "degraded": "WARN",
    "unknown": "?   ",
    "broken": "FAIL",
}

# The one-line verdict, keyed by the report's worst status.
_VERDICTS: dict[str, str] = {
    "ready": "Everything Opengrader needs is installed and working.",
    "degraded": "Opengrader will work. Some optional features are unavailable.",
    "unknown": "Opengrader will probably work, but some checks could not run.",
    "broken": "Opengrader cannot run correctly until the FAIL items are fixed.",
}


def _print_check(check: EnvironmentCheck) -> None:
    print(f"  [{_GLYPHS[check.status]}] {check.name}: {check.detail}")
    # Impact and fix are indented under the finding they belong to, and only
    # printed when there is something to say — a passing check with three blank
    # lines under it is how a report becomes unreadable.
    if check.impact:
        print(f"           {check.impact}")
    if check.fix:
        print(f"           Fix: {check.fix}")


def _print_human(report: EnvironmentReport) -> None:
    print("Opengrader environment check")
    print()
    for check in report.checks:
        _print_check(check)

    print()
    print(_VERDICTS[report.status])

    # Repeat what needs attention as a to-do list. The findings are already
    # above, but they are scattered among the passing ones, and the reader's
    # actual question is "what do I have to do now?".
    attention = report.attention
    if attention:
        print()
        print("To do, most important first:")
        for index, check in enumerate(attention, start=1):
            action = check.fix or "see above"
            print(f"  {index}. {check.name}: {action}")


def _report_to_json(report: EnvironmentReport) -> dict:
    return {
        "status": report.status,
        "checks": [
            {
                "name": check.name,
                "status": check.status,
                "detail": check.detail,
                "impact": check.impact,
                "fix": check.fix,
            }
            for check in report.checks
        ],
    }


def build_parser() -> ArgumentParser:
    # Resolved once, here, so `doctor` checks the same host/model every other
    # tool would actually use — whatever is saved in AI Setup — rather than a
    # hardcoded literal that could silently disagree with the real settings.
    ai = ai_config.resolve()
    parser = ArgumentParser(
        # Deliberately no `prog=`: argparse then names whatever was
        # actually invoked — the standalone `doctor` script, or
        # `opengrader doctor` when the front-door command dispatched
        # here (cli/main.py patches sys.argv[0] for exactly this). A
        # hardcoded name would tell a user to fix a command they never ran.
        description="Check that this machine has everything Opengrader needs.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--ollama-host",
        default=ai.host,
        help="Ollama server to check (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--ollama-model",
        default=ai.vision_model,
        help="Vision model the extractor would use (default: %(default)s, from AI Setup).",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="Text model `automated-grading --llm` would use, if different.",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Also run an end-to-end check: answer key -> template PDF.",
    )
    parser.add_argument(
        "--no-graphics",
        action="store_true",
        help="Skip the terminal-graphics check (it inspects the terminal).",
    )
    parser.add_argument(
        "--time-ai",
        action="store_true",
        help="Time one small real request to the configured handwriting model and report it against your timeout. Note: slow on a first call because the model's weights are loaded then.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    parser.add_argument(
        "--fail-on-problem",
        action="store_true",
        help="Exit 2 when a core capability is broken (for scripts and CI).",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    ai = ai_config.resolve()

    report = build_environment_report(
        vision_model=args.ollama_model,
        text_model=args.llm_model,
        ollama_host=args.ollama_host,
        check_graphics=not args.no_graphics,
        smoke_test=args.smoke,
        ai=ai,
        time_ai=args.time_ai,
    )

    if args.json:
        print(json.dumps(_report_to_json(report), indent=2))
    else:
        _print_human(report)

    # A missing *optional* integration must not fail a scripted run: exit 2 is
    # reserved for the findings that genuinely stop the pipeline, matching
    # `scan-check --fail-on-high` and `grade-check --fail-on-blocking`.
    if args.fail_on_problem and report.broken:
        return 2
    return 0


def main() -> int:
    """Console-script entry point.

    ``run_cli`` returns an exit code, and a ``console_scripts`` wrapper does
    ``sys.exit(main())`` — so returning the code from here is what makes a clean
    run exit 0 instead of printing a repr. (The same latent defect was fixed in
    `llm-eval` and `grade-check`.)
    """
    return run_cli()


if __name__ == "__main__":  # pragma: no cover - manual invocation
    sys.exit(main())

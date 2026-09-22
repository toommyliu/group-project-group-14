"""Check local tools and model connections needed for grading."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter

from opengrader.core import app_config
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

_GLYPHS: dict[str, str] = {
    "ready": "OK  ",
    "degraded": "WARN",
    "unknown": "?   ",
    "broken": "FAIL",
}

_VERDICTS: dict[str, str] = {
    "ready": "Everything Opengrader needs is installed and working.",
    "degraded": "Opengrader will work. Some optional features are unavailable.",
    "unknown": "Opengrader will probably work, but some checks could not run.",
    "broken": "Opengrader cannot run correctly until the FAIL items are fixed.",
}


def _print_check(check: EnvironmentCheck) -> None:
    print(f"  [{_GLYPHS[check.status]}] {check.name}: {check.detail}")
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
    ai = app_config.resolve()
    parser = ArgumentParser(
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
    ai = app_config.resolve()

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

    if args.fail_on_problem and report.broken:
        return 2
    return 0


def main() -> int:
    return run_cli()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

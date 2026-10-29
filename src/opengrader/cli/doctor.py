"""Check local tools and model connections needed for grading."""

from __future__ import annotations

import json
import sys

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, resolved_ai
from opengrader.core import app_config
from opengrader.core.environment import (
    EnvironmentCheck,
    EnvironmentReport,
    build_environment_report,
)

EPILOG = """\
\b
Examples:
  opengrader doctor                      # check everything
  opengrader doctor --smoke              # also build a template PDF end to end
  opengrader doctor --json               # machine-readable, for a bug report
  opengrader doctor --fail-on-problem    # exit 2 if a core capability is broken (CI)
  opengrader doctor --ollama-model llama3.1:8b
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


def _print_human(report: EnvironmentReport, *, ai: "app_config.ResolvedAI | None" = None) -> None:
    print("Opengrader environment check")
    print()
    for check in report.checks:
        _print_check(check)

    print()
    print(_VERDICTS[report.status])

    # One sentence, one source (consent.egress_statement) — it already
    # says when names and IDs are included, so nothing else composes its
    # own wording here (see AIEgressBanner, the TUI's version of this line).
    if ai is not None and ai.kind == "cloud":
        print()
        print(f"Data flow: {app_config.egress_statement(ai)}")

    attention = report.attention
    if attention:
        print()
        print("To do, most important first:")
        for index, check in enumerate(attention, start=1):
            action = check.fix or "see above"
            print(f"  {index}. {check.name}: {action}")


def _report_to_json(report: EnvironmentReport, *, ai: "app_config.ResolvedAI | None" = None) -> dict:
    result = {
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
    
    # Same sentence as the human output.
    if ai is not None and ai.kind == "cloud":
        result["data_flow"] = app_config.egress_statement(ai)

    return result


@click.command("doctor", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--ollama-host",
    default=ai_default("host"),
    show_default="from AI Setup",
    help="Ollama server to check.",
)
@click.option(
    "--ollama-model",
    default=ai_default("vision_model"),
    show_default="from AI Setup",
    help="Vision model the extractor would use.",
)
@click.option(
    "--llm-model",
    help="Text model `opengrader automated-grading --llm` would use, if different.",
)
@click.option(
    "--smoke",
    is_flag=True,
    help="Also run an end-to-end check: answer key -> template PDF.",
)
@click.option(
    "--no-graphics",
    is_flag=True,
    help="Skip the terminal-graphics check (it inspects the terminal).",
)
@click.option(
    "--time-ai",
    is_flag=True,
    help="Time one small real request to the configured handwriting model and report it against your timeout. Note: slow on a first call because the model's weights are loaded then.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON instead of text.")
@click.option(
    "--fail-on-problem",
    is_flag=True,
    help="Exit 2 when a core capability is broken (for scripts and CI).",
)
def command(
    *,
    ollama_host: str,
    ollama_model: str,
    llm_model: str | None,
    smoke: bool,
    no_graphics: bool,
    time_ai: bool,
    as_json: bool,
    fail_on_problem: bool,
) -> None:
    """Check that this machine has everything Opengrader needs."""
    ai = resolved_ai()

    report = build_environment_report(
        vision_model=ollama_model,
        text_model=llm_model,
        ollama_host=ollama_host,
        check_graphics=not no_graphics,
        smoke_test=smoke,
        ai=ai,
        time_ai=time_ai,
    )

    if as_json:
        print(json.dumps(_report_to_json(report, ai=ai), indent=2))
    else:
        _print_human(report, ai=ai)

    if fail_on_problem and report.broken:
        sys.exit(2)


if __name__ == "__main__":  # pragma: no cover
    command()

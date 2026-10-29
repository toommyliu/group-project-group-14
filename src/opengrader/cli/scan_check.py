"""Check scan quality, orientation, and page order before extraction."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.submissions.scan_check import (
    DEFAULT_DPI_FLOOR,
    ScanIssue,
    ScanReport,
    analyze_scan_pdf,
    apply_rotation_fixes,
    default_upright_path,
    discover_scan_pdfs,
    page_order_fix_is_safe,
)

EPILOG = """\
\b
Examples:
  opengrader scan-check --input scans/all-submissions.pdf
  opengrader scan-check --input scans/all-submissions.pdf --pages-per-submission 6   # also checks page order
  opengrader scan-check --input scans/all-submissions.pdf --fix            # writes all-submissions-upright.pdf
  opengrader scan-check --input split/ --blank-template templates/blank.pdf
  opengrader scan-check --input split/ --fix --output split-upright/
  opengrader scan-check --input scans/all.pdf --json --fail-on-high        # for scripting/CI

\b
Note:
  Out-of-order pages are reported here, never repaired here — page numbers
  restart at 1 for every student, so they can only be put back in order after
  the bundle is split. Fix them with:
    opengrader submission-splitter --input scans/all.pdf --output split/ \\
        --pages-per-submission 6 --fix-order
"""

_SEVERITY_GLYPH = {"high": "●", "medium": "◐", "low": "○"}
_SEVERITY_LABEL = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}


def _template_page_count(template_path: Path) -> int:
    from pypdf import PdfReader

    return len(PdfReader(str(template_path)).pages)


def _location(issue: ScanIssue) -> str:
    return f"page {issue.page}" if issue.page is not None else "document"


def _print_report(report: ScanReport, *, show_source: bool) -> None:
    if show_source:
        print(f"\n{report.source}")

    header = f"  {report.page_count} page(s) checked"
    if not report.issues:
        print(f"{header} — no problems found. ✓")
    else:
        counts = report.counts_by_severity()
        print(
            f"{header} — {counts['high']} high, {counts['medium']} medium, "
            f"{counts['low']} low."
        )

    if not report.orientation_available:
        print(f"  ! {report.orientation_note}")
    elif report.rotation_plan.unverified_pages:
        pages = ", ".join(str(page) for page in report.rotation_plan.unverified_pages)
        print(
            f"  ~ orientation unverified on page(s) {pages} "
            "(too little text to read); they were left as they are"
        )

    order = report.page_order_plan
    if not order.checked:
        if order.note:
            print(f"  ! {order.note}")
    elif order.unread_pages:
        pages = ", ".join(str(page) for page in order.unread_pages)
        print(
            f"  ~ no page number could be read on page(s) {pages}; their "
            "position was taken on trust"
        )

    current: str | None = None
    for issue in report.issues:
        if issue.severity != current:
            current = issue.severity
            glyph = _SEVERITY_GLYPH.get(issue.severity, "·")
            print(f"  {glyph} {_SEVERITY_LABEL.get(issue.severity, issue.severity)}")
        fix_hint = "  [--fix]" if issue.fixable else ""
        print(f"      {_location(issue):<12} {issue.detail}{fix_hint}")


def _report_to_dict(
    report: ScanReport, *, has_blank_template: bool = False
) -> dict[str, object]:
    order = report.page_order_plan
    return {
        "source": report.source,
        "page_count": report.page_count,
        "orientation_available": report.orientation_available,
        "orientation_note": report.orientation_note,
        "unverified_orientation_pages": list(
            report.rotation_plan.unverified_pages
        ),
        "rotations": {
            str(page): angle
            for page, angle in sorted(report.rotation_plan.angles.items())
        },
        "page_order": {
            "checked": order.checked,
            "note": order.note,
            "evidence": order.evidence,
            "submission_length": order.submission_length,
            "detected_page_numbers": list(order.detected),
            "unread_pages": list(order.unread_pages),
            "fix_is_safe": page_order_fix_is_safe(
                order, has_blank_template=has_blank_template
            ),
            "out_of_order": [
                {
                    "first_page": window.first_page,
                    "last_page": window.last_page,
                    "break_page": window.break_page,
                    "page_numbers": list(window.numbers),
                }
                for window in order.out_of_order_windows
            ],
        },
        "issues": [
            {
                "kind": issue.kind.value,
                "severity": issue.severity,
                "page": issue.page,
                "detail": issue.detail,
                "fixable": issue.fixable,
            }
            for issue in report.issues
        ],
    }


def _fix_destination(source: Path, output: str | None, *, many: bool) -> Path:
    if output is None:
        return default_upright_path(source)
    destination = Path(output)
    if many or destination.is_dir() or destination.suffix.lower() != ".pdf":
        return destination / source.name
    return destination


@click.command("scan-check", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    help="A scanned PDF, or a directory of PDFs (e.g. a split/ directory).",
)
@click.option(
    "--pages-per-submission",
    type=int,
    help=(
        "Pages each student's submission should have. Checks that a bundle "
        "holds a whole number of submissions — a spare or missing sheet "
        "shifts every boundary after it."
    ),
)
@click.option(
    "--blank-template",
    metavar="PDF",
    help=(
        "Blank template PDF. Its page count is the length each already-split "
        "submission should be; use it when --input is a split directory."
    ),
)
@click.option(
    "--dpi-floor",
    type=float,
    default=DEFAULT_DPI_FLOOR,
    help=(
        f"Flag pages scanned below this effective DPI (default "
        f"{DEFAULT_DPI_FLOOR:.0f}). Handwriting OCR degrades badly beneath it."
    ),
)
@click.option(
    "--no-orientation",
    is_flag=True,
    help=(
        "Skip the rotation check (the slow part — one Tesseract OSD pass per "
        "page). The other checks still run."
    ),
)
@click.option(
    "--no-page-order",
    is_flag=True,
    help=(
        "Skip the page-order check. It reads each page's own page number "
        "(QR code, PDF text layer, or OCR of the footer) and reports any "
        "submission whose sheets do not run forwards. Needs to know how long "
        "a submission is, so on a single bundle it does nothing without "
        "--pages-per-submission or --blank-template."
    ),
)
@click.option(
    "--fix",
    is_flag=True,
    help=(
        "Write an upright copy with rotated pages turned. Never modifies the "
        "input; see --output for where the copy goes."
    ),
)
@click.option(
    "--output",
    help=(
        "Where --fix writes. A directory for several inputs (default: "
        "alongside each input as <name>-upright.pdf)."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the reports as JSON instead of a human-readable list.",
)
@click.option(
    "--fail-on-high",
    is_flag=True,
    help="Exit non-zero if any high-severity problem is found (for scripting/CI).",
)
def command(
    *,
    input_path: Path,
    pages_per_submission: int | None,
    blank_template: str | None,
    dpi_floor: float,
    no_orientation: bool,
    no_page_order: bool,
    fix: bool,
    output: str | None,
    as_json: bool,
    fail_on_high: bool,
) -> None:
    """Pre-flight a scan before grading: detect rotated pages, sheets that
    are out of order, blank sheets, low resolution, clipped margins and
    page-count mismatches, and optionally write an upright copy.
    """
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    if input_path.is_file() and input_path.suffix.lower() != ".pdf":
        print(f"Error: input must be a PDF: {input_path}", file=sys.stderr)
        sys.exit(1)

    paths = discover_scan_pdfs(input_path)
    if not paths:
        print(f"Error: no PDF files found at {input_path}", file=sys.stderr)
        sys.exit(1)

    expected_page_count: int | None = None
    if blank_template:
        template_path = Path(blank_template)
        if not template_path.exists():
            print(
                f"Error: blank template not found: {template_path}", file=sys.stderr
            )
            sys.exit(1)
        expected_page_count = _template_page_count(template_path)

    is_bundle = input_path.is_file()
    bundle_expectation = pages_per_submission if is_bundle else None
    per_file_expectation = expected_page_count
    if not is_bundle and pages_per_submission:
        per_file_expectation = pages_per_submission

    reports: list[ScanReport] = []
    fixed: list[tuple[Path, Path, int]] = []
    for path in paths:
        report = analyze_scan_pdf(
            path,
            pages_per_submission=bundle_expectation,
            expected_page_count=per_file_expectation,
            single_submission=not is_bundle,
            dpi_floor=dpi_floor,
            check_orientation=not no_orientation,
            check_page_order=not no_page_order,
        )
        reports.append(report)

        if fix and report.rotation_plan:
            destination = _fix_destination(path, output, many=len(paths) > 1)
            if destination.resolve() == path.resolve():
                print(
                    f"Error: --fix would overwrite the original scan {path}; "
                    "choose a different --output.",
                    file=sys.stderr,
                )
                sys.exit(1)
            turned = apply_rotation_fixes(path, destination, report.rotation_plan)
            fixed.append((path, destination, turned))

    if as_json:
        print(
            json.dumps(
                {
                    "reports": [
                        _report_to_dict(
                            report, has_blank_template=bool(blank_template)
                        )
                        for report in reports
                    ],
                    "fixed": [
                        {
                            "source": str(source),
                            "output": str(destination),
                            "pages_rotated": turned,
                        }
                        for source, destination, turned in fixed
                    ],
                },
                indent=2,
            )
        )
    else:
        for report in reports:
            _print_report(report, show_source=len(reports) > 1)
        if fix:
            print()
            if fixed:
                for _source, destination, turned in fixed:
                    print(f"Rotated {turned} page(s) → {destination}")
                print(
                    "Point the splitter at the upright copy — the original is "
                    "untouched."
                )
            else:
                print("Nothing to rotate; no copies written.")

    if fail_on_high and any(report.high_severity for report in reports):
        sys.exit(2)


if __name__ == "__main__":
    command()

"""Import a folder of phone photos as per-student PDFs for submission-extractor."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.submissions.photo_intake import (
    DEFAULT_OUTPUT_DPI,
    PAPER_SIZES_IN,
    ImageOutcome,
    PhotoIntakeError,
    PhotoIntakeReport,
    import_photos,
)

EPILOG = """\
\b
Examples:
  opengrader photo-import --input photos/ --output split/
  opengrader photo-import --input photos/ --output split/ --pages-per-photo-set 3
  opengrader photo-import --input photos/ --output split/ --paper a4
  opengrader photo-import --input photos/ --output split/ --no-perspective   # deskew only
  opengrader photo-import --input photos/ --output split/ --json             # for scripting

\b
Grouping (chosen automatically, in this order):
  1. one photo, --input pointing at a single file       -> one submission
  2. --pages-per-photo-set N                             -> N photos per student
  3. one subdirectory per student under --input          -> folder = submission
  4. filenames sharing a leading token (s1234-p1.jpg,
     s1234-p2.jpg, s5678-p1.jpg ...)                      -> token = submission
  5. none of the above                                    -> one photo = one submission

\b
Then hand off to submission-extractor:
  opengrader submission-extractor --input split/ --output out/student-submissions/
"""

_METHOD_LABEL = {
    "fiducials": "registration marks",
    "edges": "document edges",
    "deskew": "deskew only",
    "unreadable": "could not be read",
}


def _report_to_dict(report: PhotoIntakeReport) -> dict[str, object]:
    return {
        "grouping_mode": report.grouping_mode,
        "output_dir": str(report.output_dir),
        "dpi": report.dpi,
        "outcomes": [
            {"source": str(outcome.source), "method": outcome.method, "note": outcome.note}
            for outcome in report.outcomes
        ],
        "submissions": [
            {
                "token": submission.token,
                "output_pdf": str(submission.output_pdf),
                "pages": [str(page) for page in submission.pages],
            }
            for submission in report.submissions
        ],
    }


def _print_report(report: PhotoIntakeReport) -> None:
    outcomes_by_source: dict[Path, ImageOutcome] = {
        outcome.source: outcome for outcome in report.outcomes
    }

    print(f"Grouping: {report.grouping_mode.replace('_', ' ')}")
    if not report.submissions:
        print("No submissions were written.")
    for submission in report.submissions:
        label = submission.token or submission.output_pdf.stem
        method_counts: dict[str, int] = {}
        for page in submission.pages:
            method = outcomes_by_source[page].method
            method_counts[method] = method_counts.get(method, 0) + 1
        detail = ", ".join(
            f"{count} via {_METHOD_LABEL.get(method, method)}"
            for method, count in method_counts.items()
        )
        print(
            f"  {label}: {len(submission.pages)} page(s) — {detail} "
            f"-> {submission.output_pdf}"
        )

    unreadable = [o for o in report.outcomes if o.method == "unreadable"]
    if unreadable:
        print(f"\n{len(unreadable)} photo(s) could not be read and were skipped:")
        for outcome in unreadable:
            print(f"  {outcome.source.name}: {outcome.note}")

    print(f"\nWrote {len(report.submissions)} submission PDF(s) to {report.output_dir}")
    print(
        "Next: opengrader submission-extractor --input "
        f"{report.output_dir} --output {report.output_dir}-extracted/"
    )


@click.command("photo-import", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    help=(
        "A directory of photos (optionally one subdirectory per student), "
        "or a single photo file."
    ),
)
@click.option(
    "--output",
    required=True,
    help="Directory to write the per-student PDFs to. --input is never modified.",
)
@click.option(
    "--pages-per-photo-set",
    type=int,
    help=(
        "Fixed number of photos per student, taken in natural filename "
        "order. Mirrors opengrader submission-splitter --pages-per-submission."
    ),
)
@click.option(
    "--dpi",
    type=float,
    default=DEFAULT_OUTPUT_DPI,
    help=(
        f"Minimum DPI the embedded page must clear (default "
        f"{DEFAULT_OUTPUT_DPI:.0f} — the same floor scan-check enforces "
        "for handwriting OCR). A higher-resolution photo keeps its own "
        "resolution; only a low-resolution one is scaled up to meet this."
    ),
)
@click.option(
    "--paper",
    type=click.Choice(sorted(PAPER_SIZES_IN)),
    default="letter",
    help="Physical page size to rectify against (default: letter).",
)
@click.option(
    "--no-perspective",
    is_flag=True,
    help=(
        "Skip page-quad detection entirely and only deskew (small-angle "
        "rotation, no keystone correction). Use this when perspective "
        "correction is making a photo worse than leaving it flat."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the report as JSON instead of a human-readable summary.",
)
def command(
    *,
    input_path: Path,
    output: str,
    pages_per_photo_set: int | None,
    dpi: float,
    paper: str,
    no_perspective: bool,
    as_json: bool,
) -> None:
    """Import a folder of phone photos (JPEG/HEIC/PNG) into per-student PDFs:
    deskew or perspective-correct each page, group pages into submissions, and
    report the exact submission-extractor command to run next.
    """
    if not input_path.exists():
        print(f"Error: input path not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    try:
        report = import_photos(
            input_path,
            output,
            pages_per_photo_set=pages_per_photo_set,
            dpi=dpi,
            paper=paper,
            perspective=not no_perspective,
        )
    except PhotoIntakeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    if as_json:
        print(json.dumps(_report_to_dict(report), indent=2))
    else:
        _print_report(report)


if __name__ == "__main__":
    command()

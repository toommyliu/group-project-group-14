"""Create, validate, and preview answer-region maps for existing PDFs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.region_map import (
    PREVIEW_DPI,
    PROPOSAL_DPI,
    RegionMap,
    RegionMapError,
    RegionMapFinding,
    blank_region_map,
    default_region_map_path,
    grid_region_map,
    load_region_map,
    propose_region_map,
    region_map_errors,
    save_region_map,
    seed_region_map_from_qa,
    validate_region_map,
    verify_region_map,
    write_region_map_preview,
)

EPILOG = """\
\b
Examples:
  # 1. Draft the boxes automatically, then look at what it found.
  uv run region-map propose --input midterm.pdf --output midterm.regions.json
  uv run region-map preview --map midterm.regions.json --input midterm.pdf \\
      --output midterm-preview.pdf

\b
  # 2. Detection came back empty? Start from a grid or from nothing instead.
  uv run region-map propose --input midterm.pdf --grid 4 --output midterm.regions.json
  uv run region-map propose --input midterm.pdf --blank --output midterm.regions.json

\b
  # 3. Borrow points, ids and question types from the answer key.
  uv run region-map from-qa --map midterm.regions.json --qa-file qa.md

\b
  # 4. Check it, then grade with it.
  uv run region-map validate --map midterm.regions.json --input midterm.pdf
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
      --region-map midterm.regions.json

\b
Fix the boxes by hand:
  The TUI's region-map editor opens the assignment with its regions drawn on it and
  lets you nudge, resize, add and delete them with the keyboard (mouse drag too,
  where the terminal can draw images).

\b
Exit codes:
  0  fine   1  bad input / unusable file   2  the map has error-level findings
"""

_SEVERITY_GLYPH = {"error": "●", "warning": "◐", "info": "○"}
_SEVERITY_LABEL = {"error": "ERROR", "warning": "WARN", "info": "INFO"}


def _print_findings(findings: tuple[RegionMapFinding, ...]) -> None:
    if not findings:
        print("  No problems found. ✓")
        return
    for finding in findings:
        glyph = _SEVERITY_GLYPH.get(finding.severity, "·")
        label = _SEVERITY_LABEL.get(finding.severity, finding.severity.upper())
        where = f" [{finding.question_id}]" if finding.question_id else ""
        print(f"  {glyph} {label:<5}{where} {finding.message}")


def _print_summary(region_map: RegionMap) -> None:
    print(f"  Assignment: {region_map.source_name} ({region_map.source_pages} page(s))")
    if region_map.assignment_title:
        print(f"  Title:      {region_map.assignment_title}")
    print(f"  Questions:  {len(region_map.questions)}")

    by_page: dict[int, list[str]] = {}
    for question in region_map.questions:
        confidence = (
            "" if question.confidence is None else f" ({question.confidence:.0%})"
        )
        by_page.setdefault(question.page, []).append(
            f"{question.question_id}{confidence}"
        )
        for index, segment in enumerate(question.continuations, start=1):
            by_page.setdefault(segment.page, []).append(
                f"{question.question_id} cont.{index}"
            )

    for page in sorted(by_page):
        print(f"    page {page}: {', '.join(by_page[page])}")

    identity = []
    if region_map.name_field is not None:
        identity.append(f"name (page {region_map.name_field.page})")
    if region_map.student_id_field is not None:
        identity.append(f"student id (page {region_map.student_id_field.page})")
    print(f"  Identity:  {', '.join(identity) if identity else 'none mapped'}")


def _load(map_path: str) -> RegionMap:
    try:
        return load_region_map(map_path)
    except RegionMapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)


def _verify_or_exit(region_map: RegionMap, pdf_path: str) -> None:
    verification = verify_region_map(region_map, pdf_path)
    if not verification:
        print(f"error: {verification.message}", file=sys.stderr)
        sys.exit(1)


@click.group("region-map", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Draw the answer regions on an assignment Opengrader did not generate,
    once, and reuse them for every scan of it.
    """


@command.command(
    "propose",
    short_help="Detect the answer regions in a blank assignment and write a draft map.",
)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    metavar="PDF",
    help="The blank assignment PDF.",
)
@click.option(
    "--output",
    default="",
    metavar="JSON",
    help="Where to write the map (default: <assignment>.regions.json beside it).",
)
@click.option(
    "--grid",
    type=int,
    default=0,
    metavar="N",
    help=(
        "Skip detection: lay out N evenly spaced regions per page. The right "
        "move for an assignment with a regular answer-box layout, and for one the "
        "detector cannot read."
    ),
)
@click.option(
    "--columns",
    type=int,
    default=1,
    help="Columns in the --grid layout (default 1).",
)
@click.option(
    "--blank",
    is_flag=True,
    help="Skip detection entirely and write an empty map to draw into.",
)
@click.option(
    "--qa-file",
    default="",
    metavar="QA",
    help="Seed points/types/prompts from this answer key while proposing.",
)
@click.option(
    "--title",
    default="",
    help="Assignment title to record (default: the PDF's filename stem).",
)
@click.option(
    "--dpi",
    type=int,
    default=PROPOSAL_DPI,
    help=f"Detection render resolution (default {PROPOSAL_DPI}).",
)
@click.option(
    "--force",
    is_flag=True,
    help="Replace an existing map (this discards hand-corrected boxes).",
)
def propose_command(
    *,
    input_path: Path,
    output: str,
    grid: int,
    columns: int,
    blank: bool,
    qa_file: str,
    title: str,
    dpi: int,
    force: bool,
) -> None:
    """Render each page of the blank assignment, run Opengrader's layout
    detector over it, and write what it found as a region map. The result is
    a first draft to correct, not an answer to trust — check it with
    `region-map preview`.
    """
    output_path = Path(output) if output else default_region_map_path(input_path)

    if output_path.exists() and not force:
        print(
            f"error: {output_path} already exists. Pass --force to replace it, or "
            "edit the existing map instead — re-proposing throws away every box "
            "that was corrected by hand.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if blank:
            region_map = blank_region_map(input_path, assignment_title=title)
            reasons: tuple[str, ...] = ()
            print(f"Empty region map for {input_path.name}.")
        elif grid:
            region_map = grid_region_map(
                input_path,
                per_page=grid,
                columns=columns,
                assignment_title=title,
            )
            reasons = ()
            print(
                f"Laid out {grid} region(s) per page on every page of "
                f"{input_path.name}."
            )
        else:
            print(f"Detecting regions in {input_path.name} at {dpi} DPI...")
            proposal = propose_region_map(
                input_path, dpi=dpi, assignment_title=title
            )
            region_map = proposal.region_map
            reasons = proposal.review_reasons
    except RegionMapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if qa_file:
        from opengrader.core.qa_file import load_qa_markdown

        region_map = seed_region_map_from_qa(region_map, load_qa_markdown(qa_file))

    _print_summary(region_map)

    if reasons:
        print("\n  The detector was unsure about:")
        for reason in reasons:
            print(f"    - {reason}")
    if not region_map.questions and not blank:
        print(
            "\n  Nothing was detected. This assignment's layout is not one the\n"
            "  detector can read — that is an ordinary outcome, not a failure.\n"
            f"  Start from a grid instead:  region-map propose --input "
            f"{input_path.name} --grid 4"
        )

    save_region_map(region_map, output_path)
    print(f"\nWrote {output_path}")

    findings = validate_region_map(region_map)
    if findings:
        print("\nFindings:")
        _print_findings(findings)
    print(
        f"\nNext: region-map preview --map {output_path.name} "
        f"--input {input_path.name} --output preview.pdf"
    )


@command.command(
    "validate",
    short_help="Check a map for problems; exits 2 when any would break extraction.",
)
@click.option(
    "--map", "map_path", required=True, metavar="JSON", help="The region map."
)
@click.option(
    "--input",
    "input_path",
    default="",
    metavar="PDF",
    help="The blank assignment, to verify the map was drawn on this exact file.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit findings as JSON instead of a human-readable report.",
)
def validate_command(*, map_path: str, input_path: str, as_json: bool) -> None:
    """Report duplicate or missing question ids, gaps in the numbering, empty
    or off-page boxes, pages the assignment does not have, answer regions
    cropped into each other, and missing identity fields. Pass --input to also
    check the map really belongs to that assignment.
    """
    region_map = _load(map_path)
    page_count: int | None = None

    verification_error: str | None = None
    if input_path:
        verification = verify_region_map(region_map, input_path)
        if not verification:
            verification_error = verification.message
        else:
            page_count = region_map.source_pages

    findings = validate_region_map(region_map, page_count=page_count)

    if as_json:
        print(
            json.dumps(
                {
                    "map": map_path,
                    "verified_against": input_path or None,
                    "verification_error": verification_error,
                    "findings": [
                        {
                            "kind": finding.kind,
                            "severity": finding.severity,
                            "message": finding.message,
                            "question_id": finding.question_id,
                            "page": finding.page,
                        }
                        for finding in findings
                    ],
                },
                indent=2,
            )
        )
    else:
        print(f"\n{map_path}")
        _print_summary(region_map)
        if verification_error:
            print(f"\n  ● ERROR {verification_error}")
        print()
        _print_findings(findings)

    if verification_error or region_map_errors(findings):
        sys.exit(2)


@command.command(
    "preview",
    short_help="Render the assignment with every mapped region outlined and labelled.",
)
@click.option(
    "--map", "map_path", required=True, metavar="JSON", help="The region map."
)
@click.option(
    "--input",
    "input_path",
    required=True,
    metavar="PDF",
    help="The blank assignment PDF.",
)
@click.option(
    "--output",
    required=True,
    metavar="OUT",
    help=(
        "Output path. A .pdf writes one flickable document; any other "
        "extension writes <stem>-page-N.png per page."
    ),
)
@click.option(
    "--dpi",
    type=int,
    default=PREVIEW_DPI,
    help=f"Render resolution (default {PREVIEW_DPI}).",
)
def preview_command(*, map_path: str, input_path: str, output: str, dpi: int) -> None:
    """The cheap way to check a map: an outlined copy of the blank assignment,
    one page per page. Ten seconds here beats discovering a mis-placed box
    after extracting the whole class.
    """
    region_map = _load(map_path)
    _verify_or_exit(region_map, input_path)

    try:
        written = write_region_map_preview(input_path, region_map, output, dpi=dpi)
    except Exception as exc:  # noqa: BLE001
        print(f"error: could not write the preview: {exc}", file=sys.stderr)
        sys.exit(1)

    for path in written:
        print(f"Wrote {path}")
    print(
        "\nEvery mapped region is outlined and labelled. If a box does not sit "
        "over\nthe space a student writes in, fix it before extracting — a "
        "mis-placed box\nproduces a clean, confident crop of the wrong part of "
        "the page."
    )


@command.command(
    "from-qa",
    short_help="Fill in points, question types and prompts from an answer key.",
)
@click.option(
    "--map", "map_path", required=True, metavar="JSON", help="The region map."
)
@click.option(
    "--qa-file", required=True, metavar="QA", help="The answer key to read."
)
@click.option(
    "--output",
    default="",
    metavar="JSON",
    help="Where to write the seeded map (default: overwrite --map).",
)
def from_qa_command(*, map_path: str, qa_file: str, output: str) -> None:
    """Map geometry, not your rubric. Copies each question's points, type and
    prompt out of a QA file onto the matching mapped region — by question id,
    falling back to position.
    """
    from opengrader.core.qa_file import load_qa_markdown

    region_map = _load(map_path)
    try:
        document = load_qa_markdown(qa_file)
    except (OSError, ValueError) as exc:
        print(f"error: could not read {qa_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    seeded = seed_region_map_from_qa(region_map, document)
    output_path = Path(output) if output else Path(map_path)
    save_region_map(seeded, output_path)

    filled = sum(
        1
        for before, after in zip(region_map.questions, seeded.questions)
        if before.possible_points != after.possible_points
    )
    print(f"Seeded {filled} question(s) with points from {qa_file}")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    command()

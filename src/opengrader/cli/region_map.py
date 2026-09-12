"""Create, validate, and preview answer-region maps for existing PDFs."""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

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
    region_map_to_json,
    save_region_map,
    seed_region_map_from_qa,
    validate_region_map,
    verify_region_map,
    write_region_map_preview,
)

EPILOG = """
examples:
  # 1. Draft the boxes automatically, then look at what it found.
  uv run region-map propose --input midterm.pdf --output midterm.regions.json
  uv run region-map preview --map midterm.regions.json --input midterm.pdf \\
      --output midterm-preview.pdf

  # 2. Detection came back empty? Start from a grid or from nothing instead.
  uv run region-map propose --input midterm.pdf --grid 4 --output midterm.regions.json
  uv run region-map propose --input midterm.pdf --blank --output midterm.regions.json

  # 3. Borrow points, ids and question types from the answer key.
  uv run region-map from-qa --map midterm.regions.json --qa-file qa.md

  # 4. Check it, then grade with it.
  uv run region-map validate --map midterm.regions.json --input midterm.pdf
  uv run submission-extractor --input scans/ --output out/student-submissions/ \\
      --region-map midterm.regions.json

fix the boxes by hand:
  The TUI's region-map editor opens the assignment with its regions drawn on it and
  lets you nudge, resize, add and delete them with the keyboard (mouse drag too,
  where the terminal can draw images).

exit codes:
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


def _run_propose(args: Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output) if args.output else default_region_map_path(input_path)

    if output_path.exists() and not args.force:
        print(
            f"error: {output_path} already exists. Pass --force to replace it, or "
            "edit the existing map instead — re-proposing throws away every box "
            "that was corrected by hand.",
            file=sys.stderr,
        )
        sys.exit(1)

    try:
        if args.blank:
            region_map = blank_region_map(input_path, assignment_title=args.title)
            reasons: tuple[str, ...] = ()
            print(f"Empty region map for {input_path.name}.")
        elif args.grid:
            region_map = grid_region_map(
                input_path,
                per_page=args.grid,
                columns=args.columns,
                assignment_title=args.title,
            )
            reasons = ()
            print(
                f"Laid out {args.grid} region(s) per page on every page of "
                f"{input_path.name}."
            )
        else:
            print(f"Detecting regions in {input_path.name} at {args.dpi} DPI...")
            proposal = propose_region_map(
                input_path, dpi=args.dpi, assignment_title=args.title
            )
            region_map = proposal.region_map
            reasons = proposal.review_reasons
    except RegionMapError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.qa_file:
        from opengrader.core.qa_file import load_qa_markdown

        region_map = seed_region_map_from_qa(region_map, load_qa_markdown(args.qa_file))

    _print_summary(region_map)

    if reasons:
        print("\n  The detector was unsure about:")
        for reason in reasons:
            print(f"    - {reason}")
    if not region_map.questions and not args.blank:
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


def _run_validate(args: Namespace) -> None:
    region_map = _load(args.map)
    page_count: int | None = None

    verification_error: str | None = None
    if args.input:
        verification = verify_region_map(region_map, args.input)
        if not verification:
            verification_error = verification.message
        else:
            page_count = region_map.source_pages

    findings = validate_region_map(region_map, page_count=page_count)

    if args.as_json:
        print(
            json.dumps(
                {
                    "map": args.map,
                    "verified_against": args.input or None,
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
        print(f"\n{args.map}")
        _print_summary(region_map)
        if verification_error:
            print(f"\n  ● ERROR {verification_error}")
        print()
        _print_findings(findings)

    if verification_error or region_map_errors(findings):
        sys.exit(2)


def _run_preview(args: Namespace) -> None:
    region_map = _load(args.map)
    _verify_or_exit(region_map, args.input)

    try:
        written = write_region_map_preview(
            args.input, region_map, args.output, dpi=args.dpi
        )
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


def _run_from_qa(args: Namespace) -> None:
    from opengrader.core.qa_file import load_qa_markdown

    region_map = _load(args.map)
    try:
        document = load_qa_markdown(args.qa_file)
    except (OSError, ValueError) as exc:
        print(f"error: could not read {args.qa_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    seeded = seed_region_map_from_qa(region_map, document)
    output = Path(args.output) if args.output else Path(args.map)
    save_region_map(seeded, output)

    filled = sum(
        1
        for before, after in zip(region_map.questions, seeded.questions)
        if before.possible_points != after.possible_points
    )
    print(f"Seeded {filled} question(s) with points from {args.qa_file}")
    print(f"Wrote {output}")


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Draw the answer regions on an assignment Opengrader did not generate, "
            "once, and reuse them for every scan of it."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    propose = subparsers.add_parser(
        "propose",
        help="Detect the answer regions in a blank assignment and write a draft map.",
        description=(
            "Render each page of the blank assignment, run Opengrader's layout "
            "detector over it, and write what it found as a region map. The "
            "result is a first draft to correct, not an answer to trust — check "
            "it with `region-map preview`."
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    propose.add_argument(
        "--input", required=True, metavar="PDF", help="The blank assignment PDF."
    )
    propose.add_argument(
        "--output",
        default="",
        metavar="JSON",
        help="Where to write the map (default: <assignment>.regions.json beside it).",
    )
    propose.add_argument(
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
    propose.add_argument(
        "--columns",
        type=int,
        default=1,
        help="Columns in the --grid layout (default 1).",
    )
    propose.add_argument(
        "--blank",
        action="store_true",
        help="Skip detection entirely and write an empty map to draw into.",
    )
    propose.add_argument(
        "--qa-file",
        default="",
        metavar="QA",
        help="Seed points/types/prompts from this answer key while proposing.",
    )
    propose.add_argument(
        "--title",
        default="",
        help="Assignment title to record (default: the PDF's filename stem).",
    )
    propose.add_argument(
        "--dpi",
        type=int,
        default=PROPOSAL_DPI,
        help=f"Detection render resolution (default {PROPOSAL_DPI}).",
    )
    propose.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing map (this discards hand-corrected boxes).",
    )

    validate = subparsers.add_parser(
        "validate",
        help="Check a map for problems; exits 2 when any would break extraction.",
        description=(
            "Report duplicate or missing question ids, gaps in the numbering, "
            "empty or off-page boxes, pages the assignment does not have, answer "
            "regions cropped into each other, and missing identity fields. Pass "
            "--input to also check the map really belongs to that assignment."
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    validate.add_argument("--map", required=True, metavar="JSON", help="The region map.")
    validate.add_argument(
        "--input",
        default="",
        metavar="PDF",
        help="The blank assignment, to verify the map was drawn on this exact file.",
    )
    validate.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Emit findings as JSON instead of a human-readable report.",
    )

    preview = subparsers.add_parser(
        "preview",
        help="Render the assignment with every mapped region outlined and labelled.",
        description=(
            "The cheap way to check a map: an outlined copy of the blank assignment, "
            "one page per page. Ten seconds here beats discovering a mis-placed "
            "box after extracting the whole class."
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    preview.add_argument("--map", required=True, metavar="JSON", help="The region map.")
    preview.add_argument(
        "--input", required=True, metavar="PDF", help="The blank assignment PDF."
    )
    preview.add_argument(
        "--output",
        required=True,
        metavar="OUT",
        help=(
            "Output path. A .pdf writes one flickable document; any other "
            "extension writes <stem>-page-N.png per page."
        ),
    )
    preview.add_argument(
        "--dpi",
        type=int,
        default=PREVIEW_DPI,
        help=f"Render resolution (default {PREVIEW_DPI}).",
    )

    from_qa = subparsers.add_parser(
        "from-qa",
        help="Fill in points, question types and prompts from an answer key.",
        description=(
            "Map geometry, not your rubric. Copies each question's points, type "
            "and prompt out of a QA file onto the matching mapped region — by "
            "question id, falling back to position."
        ),
        formatter_class=RawDescriptionHelpFormatter,
    )
    from_qa.add_argument("--map", required=True, metavar="JSON", help="The region map.")
    from_qa.add_argument(
        "--qa-file", required=True, metavar="QA", help="The answer key to read."
    )
    from_qa.add_argument(
        "--output",
        default="",
        metavar="JSON",
        help="Where to write the seeded map (default: overwrite --map).",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    if args.command == "propose":
        _run_propose(args)
    elif args.command == "validate":
        _run_validate(args)
    elif args.command == "preview":
        _run_preview(args)
    else:
        _run_from_qa(args)


if __name__ == "__main__":
    run_cli()

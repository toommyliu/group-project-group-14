"""Package a graded class into a return bundle — one folder to hand back.

The last step of the offline workflow: assemble the class gradebook, each
student's annotated + summarized PDF, and a class analysis report into a single
professionally-named directory. Which documents go in is chosen with the content
flags (or ``--content``); with none given it builds everything.

    uv run return-bundle --submissions out/student-submissions/            # everything
    uv run return-bundle --submissions out/student-submissions/ --gradebook
    uv run return-bundle --submissions out/student-submissions/ --student-pdfs --analysis
    uv run return-bundle --submissions out/ --output ~/returns --name "quiz1-return"

Read-only w.r.t. grades. The actual assembly lives in
:mod:`opengrader.core.export.bundle`; this is the thin CLI wrapper.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.export.bundle import (
    BUNDLE_PRESETS,
    BundleContent,
    build_return_bundle,
)

EPILOG = """
examples:
  uv run return-bundle --submissions out/student-submissions/
  uv run return-bundle --submissions out/student-submissions/ --gradebook
  uv run return-bundle --submissions out/student-submissions/ --student-pdfs --analysis
  uv run return-bundle --submissions out/ --output ~/returns --name quiz1-return --all
  uv run return-bundle --submissions out/student-submissions/ --content gradebook-student

contents:
  gradebook          class gradebook CSV (one row per student)
  student-pdfs       one annotated + summarized PDF per student
  gradebook-student  gradebook CSV + student PDFs
  analysis           class analysis report PDF
  all                everything (default when no content flag is given)
"""


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="Package graded submissions into a return bundle directory.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--submissions",
        required=True,
        metavar="PATH",
        help="Path to a .submission.md file or directory of submission files.",
    )
    parser.add_argument(
        "--output",
        metavar="DIR",
        help="Directory to create the bundle in. Defaults to the submissions folder.",
    )
    parser.add_argument(
        "--name",
        metavar="NAME",
        help="Bundle folder name. Defaults to '<assignment>-return-<date>'.",
    )
    parser.add_argument(
        "--content",
        choices=sorted(BUNDLE_PRESETS),
        help="Named content preset (alternative to the individual flags below).",
    )
    parser.add_argument(
        "--gradebook",
        action="store_true",
        help="Include the class gradebook CSV.",
    )
    parser.add_argument(
        "--student-pdfs",
        action="store_true",
        help="Include one annotated + summarized PDF per student.",
    )
    parser.add_argument(
        "--analysis",
        action="store_true",
        help="Include the class analysis report PDF.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include everything (gradebook + student PDFs + analysis).",
    )
    parser.add_argument(
        "--solutions",
        default=None,
        help=(
            "QA answer key. Optional, and only used to add the learning-outcome "
            "mastery section to the analysis report — the outcome tags live in "
            "the answer key. Without it the bundle is unchanged."
        ),
    )
    parser.add_argument(
        "--anonymous",
        action="store_true",
        help="Withhold student names from filenames and summary pages.",
    )
    return parser


def _resolve_content(args) -> BundleContent:
    """Combine the content flags into a single selection.

    Individual flags union together; ``--all`` and ``--content PRESET`` are
    shortcuts. When nothing is specified we default to everything, since a bare
    ``return-bundle`` most usefully produces a complete hand-back package.
    """
    if args.content:
        preset = BUNDLE_PRESETS[args.content]
    else:
        preset = BundleContent()

    content = BundleContent(
        gradebook=args.gradebook or args.all or preset.gradebook,
        student_pdfs=args.student_pdfs or args.all or preset.student_pdfs,
        analysis=args.analysis or args.all or preset.analysis,
    )
    if content.is_empty:
        return BUNDLE_PRESETS["all"]
    return content


def run_cli(argv: list[str] | None = None) -> None:
    parser = setup_args()
    args = parser.parse_args(argv)

    submissions_path = Path(args.submissions)
    output_dir = Path(args.output) if args.output else submissions_path
    if output_dir.is_file():
        output_dir = output_dir.parent

    content = _resolve_content(args)

    qa_document = None
    if args.solutions:
        qa_path = Path(args.solutions)
        if not qa_path.exists():
            print(f"error: solutions file not found: {qa_path}", file=sys.stderr)
            sys.exit(1)
        qa_document = load_qa_markdown(qa_path)

    try:
        result = build_return_bundle(
            submissions_path,
            output_dir,
            content=content,
            name=args.name,
            anonymous=args.anonymous,
            progress=lambda message: print(message),
            qa_document=qa_document,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"\nBundle written to {result.bundle_dir}")
    if result.gradebook_path is not None:
        print(f"  gradebook: {result.gradebook_path}")
    if content.student_pdfs:
        print(f"  student PDFs: {result.student_count} written")
    if result.analysis_path is not None:
        print(f"  analysis: {result.analysis_path}")
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)

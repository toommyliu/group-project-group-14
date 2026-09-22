"""Package graded submissions for return."""

from __future__ import annotations

import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.qa_file import load_qa_markdown
from opengrader.core.export.bundle import (
    BUNDLE_PRESETS,
    BundleContent,
    BundleSubmission,
    build_return_bundle,
)
from opengrader.core.curve import read_curve
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.workspace.context import (
    current_assignment,
    inputs_for_assignment,
    resolve_path,
    submissions_path as workspace_submissions_path,
)
from opengrader.core.workspace.store import WorkspaceNotFound

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
        metavar="PATH",
        help="Read extraction files instead of the current workspace assignment.",
    )
    parser.add_argument("--workspace", type=Path, help="Workspace directory.")
    parser.add_argument("--assignment", help="Assignment ID within the workspace.")
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

    content = _resolve_content(args)
    try:
        qa_document = None
        if args.solutions:
            qa_path = Path(args.solutions)
            if not qa_path.is_file():
                raise FileNotFoundError(f"solutions file not found: {qa_path}")
            qa_document = load_qa_markdown(qa_path)

        submissions = None
        curve = None
        if args.submissions:
            submissions_path = Path(args.submissions)
            output_dir = Path(args.output) if args.output else submissions_path
        else:
            workspace, assignment_id = current_assignment(
                workspace_root=args.workspace, assignment_id=args.assignment
            )
            submissions = []
            for item in workspace.submissions(assignment_id):
                document = workspace.document(assignment_id, item.id)
                try:
                    source_pdf = workspace.asset(document.source_pdf)
                except WorkspaceNotFound:
                    source_pdf = None
                submissions.append(BundleSubmission(document, source_pdf))
            submissions_path = workspace_submissions_path(workspace, assignment_id)
            saved = inputs_for_assignment(workspace, assignment_id)
            default_output = (
                resolve_path(workspace.root, saved.folder)
                if saved is not None
                else workspace.root
            )
            output_dir = Path(args.output) if args.output else default_output
            curve = read_curve(submissions_path)
            if qa_document is None:
                qa_document = parse_qa_markdown(workspace.assignment_source(assignment_id))

        if output_dir.is_file():
            output_dir = output_dir.parent
        result = build_return_bundle(
            submissions_path,
            output_dir,
            content=content,
            name=args.name,
            anonymous=args.anonymous,
            progress=lambda message: print(message),
            qa_document=qa_document,
            curve=curve,
            submissions=submissions,
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

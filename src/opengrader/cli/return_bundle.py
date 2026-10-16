"""Package graded submissions for return."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
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

EPILOG = """\
\b
Examples:
  uv run return-bundle --submissions out/student-submissions/
  uv run return-bundle --submissions out/student-submissions/ --gradebook
  uv run return-bundle --submissions out/student-submissions/ --student-pdfs --analysis
  uv run return-bundle --submissions out/ --output ~/returns --name quiz1-return --all
  uv run return-bundle --submissions out/student-submissions/ --content gradebook-student
  uv run return-bundle --submissions out/student-submissions/ --by-section

\b
Contents:
  gradebook          class gradebook CSV (one row per student)
  student-pdfs       one annotated + summarized PDF per student
  gradebook-student  gradebook CSV + student PDFs
  analysis           class analysis report PDF
  all                everything (default when no content flag is given)

\b
By section (--by-section):
  One folder per class section ("Section 01/", and "No section/" for any
  submission without one), each holding the chosen content for that
  section's students. The class-wide gradebook (with a section column) and
  analysis stay at the top of the bundle.
"""


def _resolve_content(
    *,
    content_preset: str | None,
    gradebook: bool,
    student_pdfs: bool,
    analysis: bool,
    include_all: bool,
) -> BundleContent:
    if content_preset:
        preset = BUNDLE_PRESETS[content_preset]
    else:
        preset = BundleContent()

    content = BundleContent(
        gradebook=gradebook or include_all or preset.gradebook,
        student_pdfs=student_pdfs or include_all or preset.student_pdfs,
        analysis=analysis or include_all or preset.analysis,
    )
    if content.is_empty:
        return BUNDLE_PRESETS["all"]
    return content


@click.command("return-bundle", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--submissions",
    metavar="PATH",
    help="Read extraction files instead of the current workspace assignment.",
)
@click.option("--workspace", type=click.Path(path_type=Path), help="Workspace directory.")
@click.option("--assignment", help="Assignment ID within the workspace.")
@click.option(
    "--output",
    metavar="DIR",
    help="Directory to create the bundle in. Defaults to the submissions folder.",
)
@click.option(
    "--name",
    metavar="NAME",
    help="Bundle folder name. Defaults to '<assignment>-return-<date>'.",
)
@click.option(
    "--content",
    "content_preset",
    type=click.Choice(sorted(BUNDLE_PRESETS)),
    help="Named content preset (alternative to the individual flags below).",
)
@click.option(
    "--gradebook",
    is_flag=True,
    help="Include the class gradebook CSV.",
)
@click.option(
    "--student-pdfs",
    is_flag=True,
    help="Include one annotated + summarized PDF per student.",
)
@click.option(
    "--analysis",
    is_flag=True,
    help="Include the class analysis report PDF.",
)
@click.option(
    "--all",
    "include_all",
    is_flag=True,
    help="Include everything (gradebook + student PDFs + analysis).",
)
@click.option(
    "--solutions",
    help=(
        "QA answer key. Optional, and only used to add the learning-outcome "
        "mastery section to the analysis report — the outcome tags live in "
        "the answer key. Without it the bundle is unchanged."
    ),
)
@click.option(
    "--anonymous",
    is_flag=True,
    help="Withhold student names from filenames and summary pages.",
)
@click.option(
    "--by-section",
    is_flag=True,
    help="Also split the bundle into one folder per class section.",
)
def command(
    *,
    submissions: str | None,
    workspace: Path | None,
    assignment: str | None,
    output: str | None,
    name: str | None,
    content_preset: str | None,
    gradebook: bool,
    student_pdfs: bool,
    analysis: bool,
    include_all: bool,
    solutions: str | None,
    anonymous: bool,
    by_section: bool,
) -> None:
    """Package graded submissions into a return bundle directory."""
    content = _resolve_content(
        content_preset=content_preset,
        gradebook=gradebook,
        student_pdfs=student_pdfs,
        analysis=analysis,
        include_all=include_all,
    )
    try:
        qa_document = None
        if solutions:
            qa_path = Path(solutions)
            if not qa_path.is_file():
                raise FileNotFoundError(f"solutions file not found: {qa_path}")
            qa_document = load_qa_markdown(qa_path)

        workspace_submissions = None
        curve = None
        if submissions:
            submissions_path = Path(submissions)
            output_dir = Path(output) if output else submissions_path
        else:
            opened, assignment_id = current_assignment(
                workspace_root=workspace, assignment_id=assignment
            )
            workspace_submissions = []
            for item in opened.submissions(assignment_id):
                document = opened.document(assignment_id, item.id)
                try:
                    source_pdf = opened.asset(document.source_pdf)
                except WorkspaceNotFound:
                    source_pdf = None
                workspace_submissions.append(BundleSubmission(document, source_pdf))
            submissions_path = workspace_submissions_path(opened, assignment_id)
            saved = inputs_for_assignment(opened, assignment_id)
            default_output = (
                resolve_path(opened.root, saved.folder)
                if saved is not None
                else opened.root
            )
            output_dir = Path(output) if output else default_output
            curve = read_curve(submissions_path)
            if qa_document is None:
                qa_document = parse_qa_markdown(opened.assignment_source(assignment_id))

        if output_dir.is_file():
            output_dir = output_dir.parent
        result = build_return_bundle(
            submissions_path,
            output_dir,
            content=content,
            name=name,
            anonymous=anonymous,
            progress=lambda message: print(message),
            qa_document=qa_document,
            curve=curve,
            submissions=workspace_submissions,
            by_section=by_section,
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
    for folder in result.section_dirs:
        print(f"  section: {folder}")
    for warning in result.warnings:
        print(f"  warning: {warning}", file=sys.stderr)


if __name__ == "__main__":
    command()

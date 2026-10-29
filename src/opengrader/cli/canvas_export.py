"""Export grades and feedback to Canvas."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS
from opengrader.core.curve import apply_curve, read_curve
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
)


EPILOG = """\
\b
Examples:
  opengrader canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1" --dry-run
  opengrader canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1"
  opengrader canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1" --attach-pdf
  opengrader canvas-export --submissions quiz1/submissions/ --course "CS146-01" --assignment "Quiz 1" --section "Section 01"

\b
Sections:
  Students are matched by SIS ID, so one Canvas course that holds every
  section (cross-listed) needs no --section. When each section is its own
  Canvas course, post each section to its course with --section.

\b
Setup (one-time):
  pip install canvas-sak
  canvas-sak help-me-setup
"""


def _submission_paths(path: Path) -> list[Path]:
    # A pipeline's submissions/ holds one folder per student
    # (<stem>/<stem>.submission.md), so one level down is read as well.
    from opengrader.core.export.gradebook import discover_submission_paths

    return discover_submission_paths(path)


def _in_section(doc: StudentSubmissionDocument, section: str) -> bool:
    from opengrader.core.class_folder import section_key

    return section_key(doc.section or "") == section_key(section)


def _total_score(doc: StudentSubmissionDocument) -> float:
    return sum(q.points_awarded for q in doc.questions if q.points_awarded is not None)


def _possible_score(doc: StudentSubmissionDocument) -> float:
    return sum(q.possible_points for q in doc.questions if q.possible_points is not None)


def _ungraded_count(doc: StudentSubmissionDocument) -> int:
    return sum(1 for q in doc.questions if q.points_awarded is None)


def _format_question_line(q: StudentSubmissionQuestion) -> str:
    pts_label = f"{q.possible_points:.10g} pts" if q.possible_points is not None else "? pts"
    awarded = f"{q.points_awarded:.10g}" if q.points_awarded is not None else "?"
    possible = f"{q.possible_points:.10g}" if q.possible_points is not None else "?"
    rubric = " — " + " - ".join(q.rubric_labels) if q.rubric_labels else ""
    line = f"{q.question_id} ({pts_label}): {awarded}/{possible}{rubric}"
    if q.analysis and q.analysis.strip():
        line += f"\n  Analysis: {q.analysis.strip()}"
    return line


def _build_comment(doc: StudentSubmissionDocument) -> str:
    lines: list[str] = []
    for q in doc.questions:
        lines.append(_format_question_line(q))
    total = _total_score(doc)
    possible = _possible_score(doc)
    lines.append(f"\nTotal: {total:.10g}/{possible:.10g}")
    return "\n".join(lines)


def _annotated_pdf_path(submission_path: Path, doc: StudentSubmissionDocument) -> Path | None:
    source_stem = Path(doc.source_pdf).stem
    candidate = submission_path.parent / "annotated-pdfs" / f"{source_stem}.annotated.pdf"
    return candidate if candidate.exists() else None


def _call_canvas_sak(
    *,
    course: str,
    assignment: str,
    sisid: str,
    grade: float,
    message: str,
    attachment: Path | None,
    dry_run: bool,
) -> tuple[bool, str]:
    cmd = [
        "canvas-sak", "grade-submission",
        course, assignment,
        "--sisid", sisid,
        "--grade", str(grade),
        "--message", message,
        "--dryrun" if dry_run else "--no-dryrun",
    ]
    if attachment is not None:
        cmd += ["--attachment", str(attachment)]

    result = subprocess.run(cmd, capture_output=True, text=True)
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _check_canvas_sak_installed() -> bool:
    return shutil.which("canvas-sak") is not None


@click.command("canvas-export", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--submissions",
    metavar="PATH",
    help="Read extraction files instead of the current workspace assignment.",
)
@click.option("--workspace", type=click.Path(path_type=Path), help="Workspace directory.")
@click.option("--workspace-assignment", help="Assignment ID within the workspace.")
@click.option(
    "--course",
    required=True,
    help="Canvas course name (fuzzy matched by canvas_sak).",
)
@click.option(
    "--assignment",
    required=True,
    help="Canvas assignment name (fuzzy matched by canvas_sak).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Preview what would be posted without actually posting.",
)
@click.option(
    "--partial",
    is_flag=True,
    help="Export even if some questions are ungraded (treats missing points as 0).",
)
@click.option(
    "--attach-pdf",
    is_flag=True,
    help="Attach the annotated PDF to the Canvas comment if one exists.",
)
@click.option(
    "--section",
    metavar="NAME",
    help=(
        "Post only the students of this class section (as their submissions "
        "record it), e.g. to a Canvas course of that section alone."
    ),
)
def command(
    *,
    submissions: str | None,
    workspace: Path | None,
    workspace_assignment: str | None,
    course: str,
    assignment: str,
    dry_run: bool,
    partial: bool,
    attach_pdf: bool,
    section: str | None,
) -> None:
    """Export graded submission files to Canvas LMS via canvas_sak."""
    if not _check_canvas_sak_installed():
        print(
            "error: canvas-sak not found on PATH.\n"
            "Install it with:  pip install canvas-sak\n"
            "Then configure it: canvas-sak help-me-setup",
            file=sys.stderr,
        )
        sys.exit(1)

    if submissions:
        submissions_path = Path(submissions)
        try:
            paths = _submission_paths(submissions_path)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        records = [(path, load_student_submission_markdown(path)) for path in paths]
    else:
        from opengrader.core.workspace.context import (
            current_assignment,
            submissions_path as workspace_submissions_path,
        )

        if attach_pdf:
            raise click.UsageError(
                "--attach-pdf requires --submissions with annotated PDFs."
            )
        try:
            opened, assignment_id = current_assignment(
                workspace_root=workspace,
                assignment_id=workspace_assignment,
            )
            submissions_path = workspace_submissions_path(opened, assignment_id)
            records = [
                (
                    submissions_path / f"submission-{item.id}.submission.md",
                    opened.document(assignment_id, item.id),
                )
                for item in opened.submissions(assignment_id)
            ]
        except (OSError, ValueError) as exc:
            raise click.UsageError(str(exc)) from None

    if not records:
        print(f"error: no submissions found in {submissions_path}", file=sys.stderr)
        sys.exit(1)
    if section:
        records = [(path, doc) for path, doc in records if _in_section(doc, section)]
        if not records:
            print(
                f"error: no submission in {submissions_path} is in section {section!r}",
                file=sys.stderr,
            )
            sys.exit(1)

    curve = read_curve(submissions_path)
    mode = "[DRY RUN] " if dry_run else ""
    print(f"{mode}Exporting grades to Canvas: course={course!r} assignment={assignment!r}")
    if section:
        print(f"Section {section!r} only: {len(records)} submission(s).")
    if curve.is_active:
        print("A score curve is active — posting curved totals (raw shown alongside).")
    print()

    ready = skipped = failed = 0

    for path, doc in records:
        print(f"  {path.name}")

        ungraded = _ungraded_count(doc)
        if ungraded > 0 and not partial:
            print(f"    SKIP: {ungraded} ungraded question(s) — run with --partial to export anyway\n")
            skipped += 1
            continue

        if not doc.student_id.strip():
            print("    SKIP: student_id is empty in submission frontmatter\n")
            skipped += 1
            continue

        raw_total = _total_score(doc)
        possible = _possible_score(doc)
        total = apply_curve(raw_total, possible, curve)
        student_label = doc.student_name.strip() or f"(SID: {doc.student_id})"
        print(f"    Student: {student_label} (SID: {doc.student_id})")
        if curve.is_active:
            print(
                f"    Score: {total:.10g} / {possible:.10g}"
                f"  (raw {raw_total:.10g})"
            )
        else:
            print(f"    Score: {total:.10g} / {possible:.10g}")

        attachment: Path | None = None
        if attach_pdf:
            attachment = _annotated_pdf_path(path, doc)
            if attachment:
                print(f"    Annotated PDF: {attachment.relative_to(path.parent)} ✓")
            else:
                print("    Annotated PDF: not found, skipping attachment")

        comment = _build_comment(doc)

        ok, output = _call_canvas_sak(
            course=course,
            assignment=assignment,
            sisid=doc.student_id,
            grade=total,
            message=comment,
            attachment=attachment,
            dry_run=dry_run,
        )

        if ok:
            print(f"    {'[DRY RUN] Would post' if dry_run else 'Posted'}: {total:.10g} pts + comment")
            ready += 1
        else:
            print(f"    FAILED: {output}")
            failed += 1

        print()

    total_label = "ready" if dry_run else "posted"
    print(f"{ready} {total_label}, {skipped} skipped, {failed} failed.")
    if dry_run and ready > 0:
        print("Run without --dry-run to post.")


if __name__ == "__main__":
    command()

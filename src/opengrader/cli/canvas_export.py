"""Export grades and feedback to Canvas."""

from __future__ import annotations

import shutil
import subprocess
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.curve import Curve, apply_curve, read_curve
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
    load_student_submission_markdown,
)


EPILOG = """
examples:
  uv run canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1" --dry-run
  uv run canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1"
  uv run canvas-export --submissions samples/cs146-quiz1/student-submissions/ --course "CS146" --assignment "Quiz 1" --attach-pdf

setup (one-time):
  pip install canvas-sak
  canvas-sak help-me-setup
"""


def _submission_paths(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(path.glob("*.submission.md"))
    raise FileNotFoundError(f"submissions path not found: {path}")


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


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="Export graded submission files to Canvas LMS via canvas_sak.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--submissions",
        required=True,
        metavar="PATH",
        help="Path to a .submission.md file or directory containing submission files.",
    )
    parser.add_argument(
        "--course",
        required=True,
        help="Canvas course name (fuzzy matched by canvas_sak).",
    )
    parser.add_argument(
        "--assignment",
        required=True,
        help="Canvas assignment name (fuzzy matched by canvas_sak).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview what would be posted without actually posting.",
    )
    parser.add_argument(
        "--partial",
        action="store_true",
        default=False,
        help="Export even if some questions are ungraded (treats missing points as 0).",
    )
    parser.add_argument(
        "--attach-pdf",
        action="store_true",
        default=False,
        help="Attach the annotated PDF to the Canvas comment if one exists.",
    )
    return parser


def run_cli(argv: list[str] | None = None) -> None:
    parser = setup_args()
    args = parser.parse_args(argv)

    if not _check_canvas_sak_installed():
        print(
            "error: canvas-sak not found on PATH.\n"
            "Install it with:  pip install canvas-sak\n"
            "Then configure it: canvas-sak help-me-setup",
            file=sys.stderr,
        )
        sys.exit(1)

    submissions_path = Path(args.submissions)
    try:
        paths = _submission_paths(submissions_path)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    if not paths:
        print(f"error: no *.submission.md files found in {submissions_path}", file=sys.stderr)
        sys.exit(1)

    curve = read_curve(submissions_path)
    mode = "[DRY RUN] " if args.dry_run else ""
    print(f"{mode}Exporting grades to Canvas: course={args.course!r} assignment={args.assignment!r}")
    if curve.is_active:
        print("A score curve is active — posting curved totals (raw shown alongside).")
    print()

    ready = skipped = failed = 0

    for path in paths:
        print(f"  {path.name}")
        doc = load_student_submission_markdown(path)

        ungraded = _ungraded_count(doc)
        if ungraded > 0 and not args.partial:
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
        if args.attach_pdf:
            attachment = _annotated_pdf_path(path, doc)
            if attachment:
                print(f"    Annotated PDF: {attachment.relative_to(path.parent)} ✓")
            else:
                print("    Annotated PDF: not found, skipping attachment")

        comment = _build_comment(doc)

        ok, output = _call_canvas_sak(
            course=args.course,
            assignment=args.assignment,
            sisid=doc.student_id,
            grade=total,
            message=comment,
            attachment=attachment,
            dry_run=args.dry_run,
        )

        if ok:
            print(f"    {'[DRY RUN] Would post' if args.dry_run else 'Posted'}: {total:.10g} pts + comment")
            ready += 1
        else:
            print(f"    FAILED: {output}")
            failed += 1

        print()

    total_label = "ready" if args.dry_run else "posted"
    print(f"{ready} {total_label}, {skipped} skipped, {failed} failed.")
    if args.dry_run and ready > 0:
        print("Run without --dry-run to post.")

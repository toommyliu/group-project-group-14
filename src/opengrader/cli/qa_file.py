"""Validate QA files and generate printable assignment templates."""

from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.pdf.templates import (
    CMU_BOLD_FILE,
    CMU_REGULAR_FILE,
    generate_personalized_batch,
    generate_template_pdf,
)
from opengrader.core.qa_file import (
    load_qa_markdown,
    render_qa_markdown,
    qa_to_template_questions,
)
from opengrader.core.roster import load_roster_students


EPILOG = """
examples:
  uv run qa-file validate --qa-file samples/cmpe195a-quiz1/qa.md
  uv run qa-file validate --qa-file samples/cmpe195a-quiz2-multiple-choice/qa.md
  uv run qa-file generate-template --qa-file samples/cmpe195a-quiz1/qa.md --output-pdf out/template-single.pdf
  uv run qa-file generate-template --qa-file samples/cmpe195a-quiz2-multiple-choice/qa.md --output-pdf out/template-mc.pdf
  uv run qa-file generate-template --qa-file samples/cs146-quiz1/qa.md --output-pdf out/template-multi.pdf --all-versions
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description="Work with QA markdown files.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Validate QA markdown structure.",
    )
    validate_parser.add_argument(
        "--qa-file", required=True, help="Path to QA markdown file."
    )

    generate_parser = subparsers.add_parser(
        "generate-template", help="Generate template PDF from a QA markdown file."
    )
    generate_parser.add_argument(
        "--qa-file", required=True, help="Path to QA markdown file."
    )
    generate_parser.add_argument(
        "--output-pdf", required=True, help="Output path for generated template PDF."
    )
    generate_parser.add_argument(
        "--version",
        default=None,
        help="Template version ID (default: first QA version or 'A').",
    )
    generate_parser.add_argument(
        "--all-versions",
        action="store_true",
        default=False,
        help="Generate templates for all versions from the QA file.",
    )
    generate_parser.add_argument(
        "--title",
        default=None,
        help="Override title from QA file. Required if QA file has no title.",
    )
    generate_parser.add_argument(
        "--instructions",
        default=None,
        help="Override instructions from QA file.",
    )
    generate_parser.add_argument(
        "--continued-page-header",
        default=None,
        help="Override continued page header from QA file.",
    )
    generate_parser.add_argument(
        "--questions-start-page",
        type=int,
        choices=[1, 2],
        default=None,
        help="Start questions on page 1 or page 2 (default: QA file value or 1).",
    )
    generate_parser.add_argument(
        "--font-regular-file",
        default=str(CMU_REGULAR_FILE),
        help=f"Path to regular font file (default: {CMU_REGULAR_FILE}).",
    )
    generate_parser.add_argument(
        "--font-bold-file",
        default=str(CMU_BOLD_FILE),
        help=f"Path to bold font file (default: {CMU_BOLD_FILE}).",
    )
    generate_parser.add_argument(
        "--qr",
        action="store_true",
        default=False,
        help=(
            "Stamp a generic QR (version + page + page-count) on every page for "
            "reliable, deterministic splitting and page reordering. Identical "
            "sheets — hand out in any order. Implied by --roster."
        ),
    )
    generate_parser.add_argument(
        "--roster",
        default=None,
        help=(
            "Personalized batch: one anonymous, QR-identified sheet per roster "
            "student (TSV 'ID<TAB>NAME', or name-only rows — a stable 6-digit id "
            "is minted for those). Output is one combined PDF whose first page is "
            "the teacher's master key (id -> name); each sheet prints the id only. "
            "Implies --qr."
        ),
    )
    generate_parser.add_argument(
        "--bubble-sheet",
        action="store_true",
        default=False,
        help=(
            "Generate a machine-readable bubble answer sheet instead of a "
            "written-answer template: one row of circles per multiple-choice "
            "question, with corner registration marks. Read back by "
            "'submission-extractor --bubble-sheet' with no OCR and no AI. "
            "Non-multiple-choice questions cannot be bubbled and are skipped "
            "(they are listed when that happens). Combines with --roster for "
            "one personalized, QR-identified sheet per student."
        ),
    )
    generate_parser.add_argument(
        "--copies",
        type=int,
        default=None,
        metavar="N",
        help=(
            "Print N sheets instead of one. The alternative to --roster when "
            "you do not have (or do not want to use) a class list: the sheets "
            "carry no student id and are identified by the name/ID a student "
            "writes on them. Bubble sheets only."
        ),
    )
    generate_parser.add_argument(
        "--shuffle",
        action="store_true",
        default=False,
        help=(
            "Give every sheet in the batch its own question order, so "
            "neighbours cannot copy by position. Each sheet prints (and QR-"
            "encodes) a short form key that extraction uses to re-derive that "
            "sheet's order — nothing to keep or lose on your side. Needs "
            "--bubble-sheet plus --roster or --copies."
        ),
    )

    return parser


def _versioned_path(base: Path, *, version: str, use_suffix: bool) -> Path:
    if not use_suffix:
        return base
    return base.parent / f"{base.stem}-v{version}{base.suffix}"


def _run_validate(args: object) -> None:
    qa_path = Path(getattr(args, "qa_file"))
    if not qa_path.exists():
        raise FileNotFoundError(f"QA file not found: {qa_path}")
    load_qa_markdown(qa_path)
    print(f"QA file is valid: {qa_path}")


def _run_generate_bubble_sheet(
    *,
    qa_doc,  # noqa: ANN001
    versions: list[str],
    title: str,
    instructions: str | None,
    output_pdf_base: Path,
    roster_path: str | None,
    copies: int | None = None,
    shuffle: bool = False,
) -> None:
    from opengrader.core.pdf.templates.bubble_sheet import (
        generate_bubble_sheet_batch,
        generate_bubble_sheet_pdf,
    )
    from opengrader.core.qa_file.markdown import (
        qa_to_bubble_questions,
        _questions_for_version,
    )

    use_version_suffix = len(versions) > 1
    for version in versions:
        questions = qa_to_bubble_questions(qa_doc, version=version)
        if not questions:
            raise ValueError(
                f"version {version!r} has no multiple-choice questions, so there "
                "is nothing to put on a bubble sheet"
            )

        bubbled = {question.question_id for question in questions}
        skipped = [
            item.id
            for question in _questions_for_version(qa_doc, version)
            for item in (question.subquestions or [question])
            if item.id not in bubbled
        ]
        if skipped:
            print(
                f"Note: {len(skipped)} question(s) are not multiple choice and "
                f"were left off the sheet: {', '.join(skipped)}"
            )

        output_pdf = _versioned_path(
            output_pdf_base, version=version, use_suffix=use_version_suffix
        )
        output_pdf.parent.mkdir(parents=True, exist_ok=True)

        if roster_path is not None or copies is not None:
            if use_version_suffix:
                raise ValueError(
                    "a batch is single-version; drop --all-versions or pass "
                    "one --version."
                )
            students = None
            if roster_path is not None:
                students = load_roster_students(roster_path, allow_missing_id=True)
                if not students:
                    raise ValueError(f"roster has no students: {roster_path}")
            _layout, forms = generate_bubble_sheet_batch(
                output_pdf,
                questions,
                title=title,
                students=students,
                copies=copies,
                shuffle=shuffle,
                instructions=instructions,
                version=version,
            )
            who = (
                f"{len(forms)} students, master key on page 1"
                if students is not None
                else f"{len(forms)} copies"
            )
            scrambling = (
                f", {len({form.form_key for form in forms})} distinct question orders"
                if shuffle
                else ""
            )
            print(
                f"Wrote bubble batch ({who}, {len(questions)} questions"
                f"{scrambling}): {output_pdf}"
            )
            if shuffle:
                print(
                    "      Each sheet prints its own form key (and encodes it in "
                    "the QR); extraction re-derives that sheet's question order "
                    "from it, so scan the whole page including the header."
                )
            continue

        layout = generate_bubble_sheet_pdf(
            output_pdf,
            questions,
            title=title,
            instructions=instructions,
            version=version,
        )
        print(
            f"Wrote bubble sheet ({len(questions)} questions, "
            f"{layout.page_count} page(s)): {output_pdf}"
        )


def _run_generate_template(args: object) -> None:
    qa_doc = load_qa_markdown(getattr(args, "qa_file"))

    if getattr(args, "all_versions") and qa_doc.version_questions:
        versions_to_generate = qa_doc.versions
    else:
        version = getattr(args, "version") or (
            qa_doc.versions[0] if qa_doc.versions else "A"
        )
        versions_to_generate = [version]

    title = getattr(args, "title") or qa_doc.title
    if title is None or not title.strip():
        raise ValueError(
            "title is required (provide title in frontmatter or pass --title)"
        )

    instructions = getattr(args, "instructions")
    if instructions is None:
        instructions = qa_doc.instructions

    continued_page_header = getattr(args, "continued_page_header")
    if continued_page_header is None:
        continued_page_header = qa_doc.continued_page_header

    questions_start_page = getattr(args, "questions_start_page")
    if questions_start_page is None:
        questions_start_page = qa_doc.questions_start_page

    output_pdf_base = Path(getattr(args, "output_pdf"))
    roster_path = getattr(args, "roster", None)
    use_qr = bool(getattr(args, "qr", False)) or roster_path is not None

    copies = getattr(args, "copies", None)
    shuffle = bool(getattr(args, "shuffle", False))
    if getattr(args, "bubble_sheet", False):
        if copies is not None and roster_path is not None:
            raise ValueError(
                "--roster and --copies both decide how many sheets to print; "
                "use one or the other."
            )
        if copies is not None and copies < 1:
            raise ValueError("--copies must be at least 1")
        if shuffle and copies is None and roster_path is None:
            raise ValueError(
                "--shuffle scrambles the sheets in a batch, so it needs "
                "--roster or --copies to say how many there are."
            )
        _run_generate_bubble_sheet(
            qa_doc=qa_doc,
            versions=versions_to_generate,
            title=title,
            instructions=instructions,
            output_pdf_base=output_pdf_base,
            roster_path=roster_path,
            copies=copies,
            shuffle=shuffle,
        )
        return

    if shuffle:
        raise ValueError(
            "--shuffle needs --bubble-sheet: a written template is read back by "
            "aligning every scan to one blank layout, so a per-sheet question "
            "order could not be recovered from the scans."
        )
    if copies is not None:
        raise ValueError("--copies needs --bubble-sheet")

    if roster_path is not None:
        if len(versions_to_generate) > 1:
            raise ValueError(
                "--roster generates a single-version batch; drop --all-versions "
                "or pass one --version."
            )
        students = load_roster_students(roster_path, allow_missing_id=True)
        if not students:
            raise ValueError(f"roster has no students: {roster_path}")
        version = versions_to_generate[0]
        questions = qa_to_template_questions(qa_doc, version=version)
        output_pdf_base.parent.mkdir(parents=True, exist_ok=True)
        assigned = generate_personalized_batch(
            output_path=output_pdf_base,
            students=students,
            version=version,
            questions=questions,
            title=title,
            instructions=instructions,
            continued_page_header=continued_page_header,
            font_regular_file=getattr(args, "font_regular_file"),
            font_bold_file=getattr(args, "font_bold_file"),
            start_questions_on_first_page=(questions_start_page == 1),
        )
        print(
            f"Wrote personalized batch ({len(assigned)} students, master key on "
            f"page 1): {output_pdf_base}"
        )
        return

    use_version_suffix = len(versions_to_generate) > 1

    for version in versions_to_generate:
        questions = qa_to_template_questions(qa_doc, version=version)
        output_pdf = _versioned_path(
            output_pdf_base,
            version=version,
            use_suffix=use_version_suffix,
        )
        output_pdf.parent.mkdir(parents=True, exist_ok=True)

        generate_template_pdf(
            output_path=output_pdf,
            version=version,
            questions=questions,
            title=title,
            instructions=instructions,
            continued_page_header=continued_page_header,
            font_regular_file=getattr(args, "font_regular_file"),
            font_bold_file=getattr(args, "font_bold_file"),
            start_questions_on_first_page=(questions_start_page == 1),
            qr=use_qr,
        )

        print(f"Wrote template{' (QR)' if use_qr else ''}: {output_pdf}")


def run_cli(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "validate":
        _run_validate(args)
        return
    if args.command == "generate-template":
        _run_generate_template(args)
        return
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    run_cli()

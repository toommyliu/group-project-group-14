from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.pdf.templates import (
    CMU_BOLD_FILE,
    CMU_REGULAR_FILE,
    generate_template_pdf,
)
from opengrader.core.pdf.templates.qa_file import (
    load_qa_markdown,
    render_qa_markdown,
    qa_to_template_questions,
)

"""
CLI (Command Line Interface) utility for converting Markdown-based Question & Answer (QA) documents into PDF templates.
"""

EPILOG = """
examples:
  uv run qa-file validate --qa-file samples/qa-single.md
  uv run qa-file validate --qa-file samples/qa-multiple-choice.md
  uv run qa-file generate-template --qa-file samples/qa-single.md --output-pdf out/template-single.pdf
  uv run qa-file generate-template --qa-file samples/qa-multiple-choice.md --output-pdf out/template-mc.pdf
  uv run qa-file generate-template --qa-file samples/qa-multi.md --output-pdf out/template-multi.pdf --all-versions
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
        )

        print(f"Wrote template: {output_pdf}")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "validate":
        _run_validate(args)
        return
    if args.command == "generate-template":
        _run_generate_template(args)
        return
    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    run_cli()

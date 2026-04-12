from __future__ import annotations

from argparse import ArgumentParser, RawDescriptionHelpFormatter
from pathlib import Path

from opengrader.core.ocr.spec import load_spec
from opengrader.core.pdf.templates import CMU_BOLD_FILE, CMU_REGULAR_FILE, generate_template_pdf
from opengrader.core.pdf.templates.qa_file import (
    embed_prompt_snippets,
    load_qa_markdown,
    qa_to_template_questions,
    save_qa_markdown,
    save_spec_json,
)

EPILOG = """
examples:
  uv run qa-file generate-template --qa-file SAMPLE-QA.md --output-pdf out/template-vA.pdf --title "Quiz 1"
  uv run qa-file generate-template --qa-file qa.md --output-pdf out/template.pdf --output-spec out/template.spec.json
  uv run qa-file embed-snippets --qa-file qa.md --source-pdf template.pdf --source-spec template.spec.json --output-qa qa.with-images.md
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Work with QA markdown files (question definitions, template generation, "
            "and prompt snippet embedding)."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser(
        "generate-template", help="Generate template PDF from a QA markdown file."
    )
    generate_parser.add_argument("--qa-file", required=True, help="Path to QA markdown file.")
    generate_parser.add_argument("--output-pdf", required=True, help="Output path for generated template PDF.")
    generate_parser.add_argument("--output-spec", required=False, help="Optional output path for generated spec JSON.")
    generate_parser.add_argument("--version", default=None, help="Template version ID (default: first QA version or 'A').")
    generate_parser.add_argument(
        "--all-versions",
        nargs="+",
        default=None,
        help="All version IDs for spec metadata and anchor options.",
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

    embed_parser = subparsers.add_parser(
        "embed-snippets",
        help="Embed prompt bbox snippets as base64 markdown images in the QA file.",
    )
    embed_parser.add_argument("--qa-file", required=True, help="Path to input QA markdown file.")
    embed_parser.add_argument("--source-pdf", required=True, help="Source PDF used for prompt snippets.")
    embed_parser.add_argument("--source-spec", required=True, help="Spec JSON containing question bbox or prompt_bbox.")
    embed_parser.add_argument("--output-qa", required=True, help="Output QA markdown file.")
    embed_parser.add_argument(
        "--image-format",
        choices=["png", "jpg", "jpeg"],
        default="png",
        help="Embedded image format (default: png).",
    )
    embed_parser.add_argument(
        "--dpi",
        type=int,
        default=200,
        help="Rasterization DPI for cropped snippets (default: 200).",
    )

    return parser


def _run_generate_template(args: object) -> None:
    qa_doc = load_qa_markdown(getattr(args, "qa_file"))
    questions = qa_to_template_questions(qa_doc)

    version = getattr(args, "version") or (qa_doc.versions[0] if qa_doc.versions else "A")
    all_versions = getattr(args, "all_versions") or (qa_doc.versions if qa_doc.versions else [version])

    title = getattr(args, "title") or qa_doc.title
    if title is None or not title.strip():
        raise ValueError("title is required (provide title in frontmatter or pass --title)")

    instructions = getattr(args, "instructions")
    if instructions is None:
        instructions = qa_doc.instructions

    continued_page_header = getattr(args, "continued_page_header")
    if continued_page_header is None:
        continued_page_header = qa_doc.continued_page_header

    questions_start_page = getattr(args, "questions_start_page")
    if questions_start_page is None:
        questions_start_page = qa_doc.questions_start_page

    output_pdf = Path(getattr(args, "output_pdf"))
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    spec = generate_template_pdf(
        output_path=output_pdf,
        version=version,
        questions=questions,
        title=title,
        instructions=instructions,
        continued_page_header=continued_page_header,
        all_versions=all_versions,
        font_regular_file=getattr(args, "font_regular_file"),
        font_bold_file=getattr(args, "font_bold_file"),
        start_questions_on_first_page=(questions_start_page == 1),
    )

    output_spec = getattr(args, "output_spec")
    if output_spec:
        save_spec_json(spec, output_spec)
        print(f"Wrote spec: {output_spec}")

    print(f"Wrote template: {output_pdf}")


def _run_embed_snippets(args: object) -> None:
    qa_path = Path(getattr(args, "qa_file"))
    source_pdf = Path(getattr(args, "source_pdf"))
    source_spec = Path(getattr(args, "source_spec"))

    if not qa_path.exists():
        raise FileNotFoundError(f"QA file not found: {qa_path}")
    if not source_pdf.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf}")
    if not source_spec.exists():
        raise FileNotFoundError(f"source spec not found: {source_spec}")

    qa_doc = load_qa_markdown(qa_path)
    spec = load_spec(source_spec)

    updated = embed_prompt_snippets(
        qa_doc,
        source_pdf=source_pdf,
        spec=spec,
        image_format=getattr(args, "image_format"),
        dpi=getattr(args, "dpi"),
    )

    output_qa = Path(getattr(args, "output_qa"))
    save_qa_markdown(qa_doc, output_qa)

    print(f"Wrote QA file with {updated} embedded prompt snippet(s): {output_qa}")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "generate-template":
        _run_generate_template(args)
        return
    if args.command == "embed-snippets":
        _run_embed_snippets(args)
        return

    raise ValueError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    run_cli()

"""Build a QA answer key from a question file."""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from argparse import ArgumentParser, BooleanOptionalAction, RawDescriptionHelpFormatter
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

from opengrader.core import ai_config
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa

_TEXT_SUFFIXES = {".md", ".txt", ".text"}

EPILOG = """
examples:
  uv run create-answer-key --input questions.md --output qa.md
  uv run create-answer-key --input questions.docx --output qa.md --title "Quiz 1"
  uv run create-answer-key --input answer-key.pdf --output qa.md --auto-ollama
  uv run create-answer-key --input answer-key.pdf --output qa.md --blank-template blank.pdf
"""


def _build_parser() -> ArgumentParser:
    ai = ai_config.resolve()
    parser = ArgumentParser(
        description=(
            "Build a QA-format answer key from a question file — a text2qti "
            "markdown (.md/.txt), a Word document (.docx), or a solved answer-key "
            "PDF (.pdf). The input format is detected from the file extension."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Path to the question file (.md/.txt/.docx/.pdf).")
    parser.add_argument("--output", required=True, help="Path to write the generated QA markdown file.")
    parser.add_argument("--title", default=None, help="Override the answer-key title.")
    parser.add_argument(
        "--instructions",
        default=None,
        help="Override the answer-key instructions/description.",
    )

    text_group = parser.add_argument_group("text2qti / Word inputs")
    text_group.add_argument(
        "--questions-start-page",
        type=int,
        default=1,
        help="Value for the 'questions_start_page' frontmatter field (default: 1).",
    )

    pdf_group = parser.add_argument_group("PDF inputs")
    pdf_group.add_argument(
        "--blank-template",
        metavar="PDF",
        default=None,
        help="Blank version of the assignment PDF (same layout) for alignment.",
    )
    pdf_group.add_argument(
        "--ollama-model",
        default=ai.vision_model,
        help="Ollama vision model (default: %(default)s, from AI Setup).",
    )
    pdf_group.add_argument(
        "--ollama-host",
        default=ai.host,
        help="Ollama server URL (default: %(default)s, from AI Setup).",
    )
    pdf_group.add_argument(
        "--ollama-timeout",
        type=float,
        default=ai.timeout_seconds,
        help="Ollama request timeout in seconds (default: %(default)s).",
    )
    pdf_group.add_argument(
        "--auto-ollama",
        action="store_true",
        help="Start the Ollama server and pull the vision model if needed.",
    )
    pdf_group.add_argument(
        "--llm-read-scans",
        action=BooleanOptionalAction,
        default=ai.read_handwriting,
        help=(
            "Whether a scanned/image PDF answer key is read with the vision "
            "model on top of OCR. A plain-text file and a born-digital PDF "
            "never touch it either way. (default: %(default)s, from AI Setup)"
        ),
    )
    return parser


def _read_docx_text(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "reading .docx files needs the 'python-docx' package (uv sync)."
        ) from exc
    document = docx.Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _qa_from_text(text: str, args) -> str:
    parsed = parse_text2qti(text)
    if not parsed.questions:
        raise ValueError("no questions parsed from the input (is it text2qti format?)")
    return render_qa(
        parsed,
        title_override=args.title,
        instructions_override=args.instructions,
        questions_start_page=args.questions_start_page,
    )


def _start_ollama_server_if_needed(args, emit: Callable[[str], None]):
    parsed_url = urlparse(args.ollama_host)
    host = parsed_url.hostname or "127.0.0.1"
    port = parsed_url.port or 11434
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            return None, None
    emit(f"Ollama server not found at {args.ollama_host}. Starting 'ollama serve'...")
    log_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8")
    try:
        proc = subprocess.Popen(["ollama", "serve"], stdout=log_file, stderr=log_file)
    except FileNotFoundError:
        log_file.close()
        raise RuntimeError("'ollama' binary not found. Please install Ollama first.")
    start_time = time.time()
    while time.time() - start_time < 30:
        time.sleep(1)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
            if s2.connect_ex((host, port)) == 0:
                break
    else:
        emit("Warning: Timed out waiting for Ollama to start; using Tesseract only.")
    return proc, log_file


def _qa_from_pdf(
    input_path: Path,
    args,
    emit: Callable[[str], None],
    progress: "Callable[[int, int], None] | None" = None,
) -> str:
    from opengrader.core.ocr.ollama_vlm import OllamaVisionConfig, ensure_model_available
    from opengrader.core.qa_file.pdf_answer_key import extract_qa_markdown_from_pdf

    server_proc = None
    log_file = None
    try:
        ollama_config = None
        if not args.llm_read_scans:
            emit(
                "The scan will be read with OCR alone (turn on 'Read "
                "handwriting on scans' in AI setup to also use the vision "
                "model)."
            )
        else:
            if args.auto_ollama:
                server_proc, log_file = _start_ollama_server_if_needed(args, emit)
                emit(f"Ensuring model '{args.ollama_model}' is available...")
                subprocess.run(
                    ["ollama", "pull", args.ollama_model],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            resolved_default = ai_config.resolve()
            if (
                args.auto_ollama
                or args.ollama_model != resolved_default.vision_model
                or args.ollama_host != resolved_default.host
            ):
                ollama_config = OllamaVisionConfig(
                    model=args.ollama_model, host=args.ollama_host, timeout_seconds=args.ollama_timeout,
                )
                try:
                    ensure_model_available(ollama_config)
                except Exception as exc:
                    emit(f"Warning: Ollama vision model unavailable ({exc}); using Tesseract only.")
                    ollama_config = None

        return extract_qa_markdown_from_pdf(
            input_path,
            title=args.title or "Extracted Assignment",
            instructions=args.instructions or "Auto-generated",
            blank_template_path=args.blank_template,
            ollama_config=ollama_config,
            emit=emit,
            progress=progress,
        )
    finally:
        if server_proc:
            server_proc.terminate()
        if log_file:
            log_file.close()


def run_cli(
    argv: list[str] | None = None,
    *,
    status_callback: "Callable[[str], None] | None" = None,
    progress_callback: "Callable[[int, int], None] | None" = None,
) -> None:
    def emit(message: str) -> None:
        if status_callback is not None:
            status_callback(message)
        else:
            print(message)

    parser = _build_parser()
    args = parser.parse_args(argv)

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    suffix = input_path.suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            qa_markdown = _qa_from_text(input_path.read_text(), args)
        elif suffix == ".docx":
            qa_markdown = _qa_from_text(_read_docx_text(input_path), args)
        elif suffix == ".pdf":
            qa_markdown = _qa_from_pdf(input_path, args, emit, progress_callback)
        else:
            print(
                f"error: unsupported input type '{suffix or input_path.name}'. "
                f"Use a .md/.txt text2qti file, a .docx, or a .pdf answer key.",
                file=sys.stderr,
            )
            sys.exit(1)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(qa_markdown)

    try:
        parse_qa_markdown(qa_markdown)
    except ValueError as exc:
        emit(
            f"Warning: the generated file needs manual fixes before grading "
            f"(run 'qa-file validate' after editing): {exc}"
        )
    emit(f"Wrote answer key to {output_path}")


if __name__ == "__main__":
    run_cli()

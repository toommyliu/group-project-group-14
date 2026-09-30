"""Build a QA answer key from a question file."""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
from opengrader.core import app_config
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.text2qti import parse_text2qti, render_qa

_TEXT_SUFFIXES = {".md", ".txt", ".text"}

EPILOG = """\
\b
Examples:
  uv run create-answer-key --input questions.md --output qa.md
  uv run create-answer-key --input questions.docx --output qa.md --title "Quiz 1"
  uv run create-answer-key --input answer-key.pdf --output qa.md --auto-ollama
  uv run create-answer-key --input answer-key.pdf --output qa.md --blank-template blank.pdf
"""


def _read_docx_text(path: Path) -> str:
    try:
        import docx
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "reading .docx files needs the 'python-docx' package (uv sync)."
        ) from exc
    document = docx.Document(str(path))
    return "\n".join(paragraph.text for paragraph in document.paragraphs)


def _qa_from_text(
    text: str,
    *,
    title: str | None,
    instructions: str | None,
    questions_start_page: int,
) -> str:
    parsed = parse_text2qti(text)
    if not parsed.questions:
        raise ValueError("no questions parsed from the input (is it text2qti format?)")
    return render_qa(
        parsed,
        title_override=title,
        instructions_override=instructions,
        questions_start_page=questions_start_page,
    )


def _start_ollama_server_if_needed(ollama_host: str, emit: Callable[[str], None]):
    parsed_url = urlparse(ollama_host)
    host = parsed_url.hostname or "127.0.0.1"
    port = parsed_url.port or 11434
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        if s.connect_ex((host, port)) == 0:
            return None, None
    emit(f"Ollama server not found at {ollama_host}. Starting 'ollama serve'...")
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


def _answer_key_pages_phrase(path: Path) -> str:
    try:
        from pypdf import PdfReader

        pages = len(PdfReader(str(path)).pages)
    except Exception:  # noqa: BLE001 - the wording falls back, the send is still stated
        return "the answer key"
    return f"the {pages}-page answer key"


def _qa_from_pdf(
    input_path: Path,
    emit: Callable[[str], None],
    progress: "Callable[[int, int], None] | None" = None,
    *,
    llm_read_scans: bool,
    auto_ollama: bool,
    ollama_model: str,
    ollama_host: str,
    ollama_timeout: float,
    title: str | None,
    instructions: str | None,
    blank_template: str | None,
    yes_send_to_cloud: bool,
    prompt_cloud_send: bool = True,
) -> str:
    from opengrader.core.qa_file.pdf_answer_key import extract_qa_markdown_from_pdf

    server_proc = None
    log_file = None
    try:
        vision_reader = None
        if not llm_read_scans:
            emit(
                "The scan will be read with OCR alone (turn on 'Read "
                "handwriting on scans' in AI setup to also use the vision "
                "model)."
            )
        else:
            resolved_default = app_config.resolve()
            resolved = app_config.with_overrides(
                resolved_default,
                host=ollama_host,
                vision_model=ollama_model,
                timeout_seconds=ollama_timeout,
            )
            if auto_ollama and resolved.provider.kind == "local":
                server_proc, log_file = _start_ollama_server_if_needed(ollama_host, emit)
                emit(f"Ensuring model '{ollama_model}' is available...")
                subprocess.run(
                    ["ollama", "pull", ollama_model],
                    check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            if (
                auto_ollama
                or ollama_model != resolved_default.vision_model
                or ollama_host != resolved_default.host
            ):
                if resolved.kind == "cloud":
                    from opengrader.core.cloud_preflight import (
                        CloudPreflight,
                        cli_confirm_cloud_send,
                    )

                    cli_confirm_cloud_send(
                        resolved,
                        CloudPreflight(
                            provider_label=resolved.provider.label,
                            host=resolved.host,
                            # Up to one image per question the reader finds
                            # (only where OCR reads it badly), and the
                            # questions are only found while the key is read
                            # — so no number is claimed.
                            uncounted={
                                "answer-image": (
                                    "up to one image per question found in "
                                    + _answer_key_pages_phrase(input_path)
                                )
                            },
                        ),
                        yes_send_to_cloud=yes_send_to_cloud,
                        emit=emit,
                        interactive=prompt_cloud_send,
                    )
                try:
                    vision_reader = app_config.vision_reader(resolved)
                    app_config.ensure_ready(resolved, purpose="vision")
                except (app_config.CloudConsentRequired, app_config.CloudDisabledError) as exc:
                    emit(f"Error: {exc}")
                    emit(
                        f"Fix: run 'opengrader providers consent {resolved.provider.name}' "
                        "(or open AI Setup), then retry."
                    )
                    sys.exit(2)
                except Exception as exc:
                    emit(f"Warning: Ollama vision model unavailable ({exc}); using Tesseract only.")
                    vision_reader = None

        return extract_qa_markdown_from_pdf(
            input_path,
            title=title or "Extracted Assignment",
            instructions=instructions or "Auto-generated",
            blank_template_path=blank_template,
            vision_reader=vision_reader,
            emit=emit,
            progress=progress,
        )
    finally:
        if server_proc:
            server_proc.terminate()
        if log_file:
            log_file.close()


@dataclass(frozen=True)
class _Hooks:
    status_callback: Callable[[str], None] | None = None
    progress_callback: Callable[[int, int], None] | None = None
    # prompt_cloud_send=False: a caller that owns the terminal (the TUI) gets
    # CloudConfirmationNeeded instead of an input() prompt; see cli_confirm_cloud_send.
    prompt_cloud_send: bool = True


@click.command("create-answer-key", context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
@click.option(
    "--input",
    "input_path",
    type=click.Path(path_type=Path),
    required=True,
    help="Path to the question file (.md/.txt/.docx/.pdf).",
)
@click.option("--output", required=True, help="Path to write the generated QA markdown file.")
@click.option("--title", help="Override the answer-key title.")
@click.option(
    "--instructions",
    help="Override the answer-key instructions/description.",
)
@click.option(
    "--questions-start-page",
    type=int,
    default=1,
    help="Value for the 'questions_start_page' frontmatter field (default: 1).",
)
@click.option(
    "--blank-template",
    metavar="PDF",
    help="Blank version of the assignment PDF (same layout) for alignment.",
)
@click.option(
    "--ollama-model",
    default=ai_default("vision_model"),
    show_default="from AI Setup",
    help="Ollama vision model.",
)
@click.option(
    "--ollama-host",
    default=ai_default("host"),
    show_default="from AI Setup",
    help="Ollama server URL.",
)
@click.option(
    "--ollama-timeout",
    type=float,
    default=ai_default("timeout_seconds"),
    show_default="from AI Setup",
    help="Ollama request timeout in seconds.",
)
@click.option(
    "--auto-ollama",
    is_flag=True,
    help="Start the Ollama server and pull the vision model if needed.",
)
@click.option(
    "--llm-read-scans/--no-llm-read-scans",
    default=ai_default("read_handwriting"),
    show_default="from AI Setup",
    help=(
        "Whether a scanned/image PDF answer key is read with the vision "
        "model on top of OCR. A plain-text file and a born-digital PDF "
        "never touch it either way."
    ),
)
@click.option(
    "--yes-send-to-cloud",
    is_flag=True,
    help=(
        "Confirm sending the scanned answer key to a cloud AI provider "
        "for --llm-read-scans. The scale and destination are shown first; "
        "nothing is sent without this or an interactive yes."
    ),
)
@click.pass_obj
def command(
    hooks: _Hooks | None,
    *,
    input_path: Path,
    output: str,
    title: str | None,
    instructions: str | None,
    questions_start_page: int,
    blank_template: str | None,
    ollama_model: str,
    ollama_host: str,
    ollama_timeout: float,
    auto_ollama: bool,
    llm_read_scans: bool,
    yes_send_to_cloud: bool,
) -> None:
    """Build a QA-format answer key from a question file — a text2qti
    markdown (.md/.txt), a Word document (.docx), or a solved answer-key
    PDF (.pdf). The input format is detected from the file extension.
    """
    hooks = hooks or _Hooks()

    def emit(message: str) -> None:
        if hooks.status_callback is not None:
            hooks.status_callback(message)
        else:
            print(message)

    if not input_path.exists():
        print(f"error: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    # Scoped to this process; the answer key has no submissions folder of its
    # own, so a cloud call here is recorded next to where the key is written.
    from opengrader.core import egress

    egress.activate(Path(output).parent)

    suffix = input_path.suffix.lower()
    try:
        if suffix in _TEXT_SUFFIXES:
            qa_markdown = _qa_from_text(
                input_path.read_text(),
                title=title,
                instructions=instructions,
                questions_start_page=questions_start_page,
            )
        elif suffix == ".docx":
            qa_markdown = _qa_from_text(
                _read_docx_text(input_path),
                title=title,
                instructions=instructions,
                questions_start_page=questions_start_page,
            )
        elif suffix == ".pdf":
            qa_markdown = _qa_from_pdf(
                input_path, emit, hooks.progress_callback,
                llm_read_scans=llm_read_scans,
                auto_ollama=auto_ollama,
                ollama_model=ollama_model,
                ollama_host=ollama_host,
                ollama_timeout=ollama_timeout,
                title=title,
                instructions=instructions,
                blank_template=blank_template,
                yes_send_to_cloud=yes_send_to_cloud,
                prompt_cloud_send=hooks.prompt_cloud_send,
            )
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

    output_path = Path(output)
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


def run_cli(
    argv: Sequence[str],
    *,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    prompt_cloud_send: bool = True,
) -> None:
    run_command(
        command,
        argv,
        obj=_Hooks(
            status_callback=status_callback,
            progress_callback=progress_callback,
            prompt_cloud_send=prompt_cloud_send,
        ),
    )


if __name__ == "__main__":
    command()

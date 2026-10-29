"""Build a QA answer key from a question file."""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, ai_default, run_command
from opengrader.core import app_config
from opengrader.core.qa_file import parse_qa_markdown
from opengrader.core.qa_file.standard_rubric import DEFAULT_QUESTION_POINTS, format_points
from opengrader.core.qa_file.text2qti import (
    parse_text2qti,
    render_qa,
    summarize_points,
)

_TEXT_SUFFIXES = {".md", ".txt", ".text"}

NO_QUESTIONS_HELP = (
    "no questions were found. Number each question — '1.', '1)', 'Q1.' or "
    "'Question 1:' (parts as '1a)', '1b)') — and put its answer on a line "
    "starting 'Answer:'. Points are optional: '(10 points)' anywhere on the "
    "question's line."
)

EPILOG = """\
\b
Examples:
  opengrader create-answer-key --input questions.md --output qa.md
  opengrader create-answer-key --input questions.docx --output qa.md --title "Quiz 1"
  opengrader create-answer-key --input questions.txt --output qa.md --default-points 5
  opengrader create-answer-key --input answer-key.pdf --output qa.md
  opengrader create-answer-key --input answer-key.pdf --output qa.md --blank-template blank.pdf
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
    default_points: float,
    on_default_points: Callable[[int, int], None],
    emit: Callable[[str], None],
    required: bool = True,
) -> str | None:
    """The answer key for a source read as text; None when `required` is off
    and the text holds no questions (a PDF that is then read as a scan)."""
    parsed = parse_text2qti(text)
    if not parsed.questions:
        if not required:
            return None
        raise ValueError(NO_QUESTIONS_HELP)
    summary = summarize_points(parsed)
    parts = (
        f", {summary.with_parts} of them in parts (1a, 1b, …)"
        if summary.with_parts
        else ""
    )
    emit(f"Read {summary.questions} question(s){parts}.")
    on_default_points(summary.defaulted, summary.graded)
    return render_qa(
        parsed,
        title_override=title,
        instructions_override=instructions,
        questions_start_page=questions_start_page,
        default_points=default_points,
    )


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
    ollama_model: str,
    ollama_host: str,
    ollama_timeout: float,
    title: str | None,
    instructions: str | None,
    blank_template: str | None,
    yes_send_to_cloud: bool,
    prompt_cloud_send: bool = True,
    default_points: float = DEFAULT_QUESTION_POINTS,
    on_default_points: Callable[[int, int], None] | None = None,
) -> str:
    from opengrader.core.qa_file.pdf_answer_key import extract_qa_markdown_from_pdf

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
            emit(f"Warning: the AI vision model is unavailable ({exc}); using Tesseract only.")
            vision_reader = None

    return extract_qa_markdown_from_pdf(
        input_path,
        title=title or "Extracted Assignment",
        instructions=instructions or "Auto-generated",
        blank_template_path=blank_template,
        vision_reader=vision_reader,
        emit=emit,
        progress=progress,
        default_points=default_points,
        on_default_points=on_default_points,
    )


@dataclass(frozen=True)
class _Hooks:
    status_callback: Callable[[str], None] | None = None
    progress_callback: Callable[[int, int], None] | None = None
    # prompt_cloud_send=False: a caller that owns the terminal (the TUI) gets
    # CloudConfirmationNeeded instead of an input() prompt; see cli_confirm_cloud_send.
    prompt_cloud_send: bool = True
    # (given, total): how many questions had no points and got the default.
    default_points_callback: Callable[[int, int], None] | None = None


def default_points_note(given: int, total: int, points: float) -> str:
    """What to tell the user when questions were given the default points;
    empty when every question stated its own."""
    if given <= 0:
        return ""
    which = "any question" if given == total else f"{given} of {total} questions (or parts)"
    return (
        f"No points were found for {which}, so each was given the standard "
        f"{format_points(points)} ({format_points(points / 2)} for partial "
        "credit, 0 for none)."
    )


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
    "--default-points",
    # Whole points: an answer key's question points are whole numbers.
    type=click.IntRange(min=1),
    default=int(DEFAULT_QUESTION_POINTS),
    show_default=True,
    help=(
        "Points for a question whose source states none. Partial credit is "
        "half of it. Points the source does state are always kept."
    ),
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
    default_points: float,
    blank_template: str | None,
    ollama_model: str,
    ollama_host: str,
    ollama_timeout: float,
    llm_read_scans: bool,
    yes_send_to_cloud: bool,
) -> None:
    """Build a QA-format answer key from a question file — a text2qti
    markdown (.md/.txt), a Word document (.docx), or a solved answer-key
    PDF (.pdf). The input format is detected from the file extension.

    A text or Word file — or a PDF that carries its own text — needs only
    numbered questions ("1.", "1)", "Q1."; parts as "1a)", "1b)") and their
    answers ("Answer: …", one line per accepted answer). Points are optional
    and may sit anywhere on the question's line ("… (10 points)"); questions
    with none get --default-points. A scanned PDF is read by OCR into a
    draft to check.
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

    defaulted = {"given": 0, "total": 0}

    def on_default_points(given: int, total: int) -> None:
        defaulted.update(given=given, total=total)

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
                default_points=default_points,
                on_default_points=on_default_points,
                emit=emit,
            )
        elif suffix == ".docx":
            qa_markdown = _qa_from_text(
                _read_docx_text(input_path),
                title=title,
                instructions=instructions,
                questions_start_page=questions_start_page,
                default_points=default_points,
                on_default_points=on_default_points,
                emit=emit,
            )
        elif suffix == ".pdf":

            def from_own_text() -> str | None:
                # A PDF made by a word processor carries its text: read it by
                # the same rules as a text or Word file, exactly.
                from opengrader.core.qa_file.pdf_answer_key import pdf_text_lines

                made = _qa_from_text(
                    pdf_text_lines(input_path),
                    title=title,
                    instructions=instructions,
                    questions_start_page=questions_start_page,
                    default_points=default_points,
                    on_default_points=on_default_points,
                    emit=emit,
                    required=False,
                )
                if made is not None:
                    emit("Read from the PDF's own text (no OCR needed).")
                return made

            # A blank template asks for the scan to be lined up against it,
            # so that comes first there and the PDF's text is the fallback.
            qa_markdown = None if blank_template else from_own_text()
            if qa_markdown is None:
                try:
                    qa_markdown = _qa_from_pdf(
                        input_path, emit, hooks.progress_callback,
                        llm_read_scans=llm_read_scans,
                        ollama_model=ollama_model,
                        ollama_host=ollama_host,
                        ollama_timeout=ollama_timeout,
                        title=title,
                        instructions=instructions,
                        blank_template=blank_template,
                        yes_send_to_cloud=yes_send_to_cloud,
                        prompt_cloud_send=hooks.prompt_cloud_send,
                        default_points=default_points,
                        on_default_points=on_default_points,
                    )
                except ValueError:
                    qa_markdown = from_own_text() if blank_template else None
                    if qa_markdown is None:
                        raise
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
            f"(run 'opengrader qa-file validate' after editing): {exc}"
        )
    note = default_points_note(defaulted["given"], defaulted["total"], default_points)
    if note:
        emit(note)
    if hooks.default_points_callback is not None:
        hooks.default_points_callback(defaulted["given"], defaulted["total"])
    emit(f"Wrote answer key to {output_path}")


def run_cli(
    argv: Sequence[str],
    *,
    status_callback: Callable[[str], None] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    prompt_cloud_send: bool = True,
    default_points_callback: Callable[[int, int], None] | None = None,
) -> None:
    run_command(
        command,
        argv,
        obj=_Hooks(
            status_callback=status_callback,
            progress_callback=progress_callback,
            prompt_cloud_send=prompt_cloud_send,
            default_points_callback=default_points_callback,
        ),
    )


if __name__ == "__main__":
    command()

from __future__ import annotations

import re
import sys
from argparse import ArgumentParser, RawDescriptionHelpFormatter
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

import pytesseract
from PIL import Image

from opengrader.core.ocr.ollama_vlm import (
    OllamaVisionConfig,
    ensure_model_available,
    extract_field_text,
    extract_question_prompt_and_answer,
)
from opengrader.core.submissions import (
    StudentSubmissionDocument,
    parse_student_submission_markdown,
)

"""
Fill in Name, Student ID, and per-question Response text on .submission.md
files that ``submission-extractor`` produced.

submission-extractor saves a Name crop, an ID crop, and a Response crop
per question into each submission folder and references them from the
markdown frontmatter (``name_image`` / ``id_image``) and per-question
(``Response image``). This command picks those crops up, runs tesseract
(and optionally an Ollama vision model) over them, and writes the
extracted text back into the markdown:

  - Name and Student ID are OCR'd. With --id-file, the OCR text is fuzzy-
    matched against a roster (digit-only normalization for the ID, case-
    folded ratio for the name) and the closest roster entry's canonical
    values replace the raw OCR output.
  - Per-question Response text comes from tesseract by default; when an
    Ollama vision model is configured, the VLM's transcription wins
    because the model handles handwriting substantially better.

Existing frontmatter keys (``name_image``, ``id_image``, ``name_field``,
``student_id_field``, etc.) are preserved by targeted line edits — the
file is not parsed and re-rendered, so extra metadata survives.
"""

EPILOG = """
examples:
  uv run submission-OCR --input out/student-submissions/
  uv run submission-OCR --input out/student-submissions/submission-0001/submission-0001.submission.md
  uv run submission-OCR --input out/student-submissions/ --id-file roster.tsv
  uv run submission-OCR --input out/student-submissions/ --id-file roster.tsv \\
    --ollama-host http://gpu.local:11434 --ollama-model qwen2.5vl:7b
"""

_DEFAULT_OLLAMA_MODEL = "qwen2.5vl:7b"
_DEFAULT_OLLAMA_HOST = "http://127.0.0.1:11434"
_DEFAULT_OLLAMA_TIMEOUT = 120.0


@dataclass(frozen=True)
class _RosterEntry:
    student_id: str
    name: str


def _load_roster(path: Path) -> list[_RosterEntry]:
    entries: list[_RosterEntry] = []
    with open(path) as handle:
        for raw in handle:
            line = raw.rstrip("\n").rstrip("\r")
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 2:
                raise ValueError(
                    f"roster line is not 'ID<TAB>NAME': {line!r} (in {path})"
                )
            entries.append(
                _RosterEntry(student_id=parts[0].strip(), name=parts[1].strip())
            )
    if not entries:
        raise ValueError(f"roster file is empty: {path}")
    return entries


def _digits_only(text: str) -> str:
    return re.sub(r"\D", "", text)


def _normalize_name(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _match_roster(
    ocr_id: str,
    ocr_name: str,
    roster: list[_RosterEntry],
) -> tuple[_RosterEntry, float] | None:
    """Return the roster entry whose ID + name best match the OCR output."""
    ocr_id_digits = _digits_only(ocr_id)
    ocr_name_norm = _normalize_name(ocr_name)
    if not ocr_id_digits and not ocr_name_norm:
        return None

    best: tuple[float, _RosterEntry] | None = None
    for entry in roster:
        entry_id_digits = _digits_only(entry.student_id)
        id_score = (
            SequenceMatcher(None, ocr_id_digits, entry_id_digits).ratio()
            if ocr_id_digits or entry_id_digits
            else 0.0
        )
        name_score = SequenceMatcher(
            None, ocr_name_norm, _normalize_name(entry.name)
        ).ratio()
        score = 0.65 * id_score + 0.35 * name_score
        if best is None or score > best[0]:
            best = (score, entry)

    if best is None:
        return None
    return best[1], best[0]


def _ocr_image(image: Image.Image, *, psm: int) -> str:
    try:
        return pytesseract.image_to_string(image, config=f"--psm {psm}").strip()
    except Exception:
        return ""


def _ocr_image_digits(image: Image.Image) -> str:
    """OCR a field crop restricted to digits; suited to student IDs."""
    digit_only = "tessedit_char_whitelist=0123456789"
    best = ""
    for config in (
        f"--psm 11 -c {digit_only}",
        f"--psm 7 -c {digit_only}",
        f"--psm 6 -c {digit_only}",
    ):
        try:
            text = pytesseract.image_to_string(image, config=config)
        except Exception:
            continue
        digits = re.sub(r"\D", "", text)
        if len(digits) > len(best):
            best = digits
    return best


def _vlm_field_text(
    crop_path: Path | None,
    *,
    field_kind: str,
    ollama_config: OllamaVisionConfig,
) -> str:
    """OCR a saved field crop via the Ollama vision model."""
    if crop_path is None or not crop_path.exists():
        return ""
    try:
        return extract_field_text(
            image_path=crop_path,
            field_kind=field_kind,
            config=ollama_config,
        ).strip()
    except Exception as exc:
        print(
            f"warning: Ollama {field_kind} OCR failed on {crop_path.name}: {exc}",
            file=sys.stderr,
        )
        return ""


def _vlm_response_text(
    crop_path: Path | None,
    *,
    ollama_config: OllamaVisionConfig,
) -> str:
    """Transcribe a question response crop via the Ollama vision model.

    Returns just the answer text (not the prompt); the VLM helper returns
    both, and we keep only the answer half so it can be written into
    ``Response text``.
    """
    if crop_path is None or not crop_path.exists():
        return ""
    try:
        _prompt, response = extract_question_prompt_and_answer(
            image_path=crop_path,
            config=ollama_config,
        )
    except Exception as exc:
        print(
            f"warning: Ollama response OCR failed on {crop_path.name}: {exc}",
            file=sys.stderr,
        )
        return ""
    return (response or "").strip()


# ---------------------------------------------------------------------------
# Markdown surgery: targeted edits that preserve unknown frontmatter keys
# (like name_image / id_image / name_field / student_id_field) which
# StudentSubmissionDocument doesn't model.
# ---------------------------------------------------------------------------

_FRONTMATTER_FENCE = "---"


def _replace_frontmatter_field(content: str, key: str, value: str) -> str:
    """Replace the value of a frontmatter key, or insert it if missing."""
    safe = value.replace("\\", "\\\\").replace('"', '\\"')
    new_line = f'{key}: "{safe}"'
    suffix = "\n" if content.endswith("\n") else ""

    lines = content.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return content

    end_fm: int | None = None
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_FENCE:
            end_fm = i
            break
    if end_fm is None:
        return content

    key_pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for i in range(1, end_fm):
        if key_pattern.match(lines[i]):
            lines[i] = new_line
            return "\n".join(lines) + suffix

    lines.insert(end_fm, new_line)
    return "\n".join(lines) + suffix


_QUESTION_HEADER_RE = re.compile(r"^-\s+([A-Za-z]\w*):\s*$")
_RESPONSE_TEXT_RE = re.compile(r"^\s{2}-\s+Response\s+text\s*:")
_NESTED_FIELD_RE = re.compile(r"^\s{2}-\s+[^:]+:")


def _replace_question_response_text(content: str, qid: str, text: str) -> str:
    """Replace the Response text field within a question block."""
    lines = content.splitlines()
    out: list[str] = []
    qheader_re = re.compile(rf"^-\s+{re.escape(qid)}:\s*$")

    in_target = False
    i = 0
    while i < len(lines):
        line = lines[i]

        if qheader_re.match(line):
            in_target = True
            out.append(line)
            i += 1
            continue

        if in_target and _RESPONSE_TEXT_RE.match(line):
            i += 1
            while i < len(lines):
                next_line = lines[i]
                if _NESTED_FIELD_RE.match(next_line):
                    break
                if _QUESTION_HEADER_RE.match(next_line):
                    break
                if next_line.startswith("    ") or next_line.strip() == "":
                    i += 1
                    continue
                break

            stripped = text.strip()
            if not stripped:
                out.append("  - Response text:")
            elif "\n" in stripped:
                out.append("  - Response text: |")
                for body_line in stripped.splitlines():
                    out.append(f"    {body_line}")
            else:
                out.append(f"  - Response text: {stripped}")

            in_target = False
            continue

        if in_target and _QUESTION_HEADER_RE.match(line):
            in_target = False

        out.append(line)
        i += 1

    suffix = "\n" if content.endswith("\n") else ""
    return "\n".join(out) + suffix


def _frontmatter_value(content: str, key: str) -> str | None:
    """Read a single string field from the YAML frontmatter via regex."""
    pattern = re.compile(rf'^{re.escape(key)}\s*:\s*"((?:[^"\\]|\\.)*)"', re.MULTILINE)
    match = pattern.search(content)
    if match is None:
        return None
    return match.group(1).replace('\\"', '"').replace("\\\\", "\\")


# ---------------------------------------------------------------------------
# Per-submission OCR pipeline
# ---------------------------------------------------------------------------


def _best_roster_match(
    *,
    ocr_id: str,
    ocr_name: str,
    name_crop_path: Path | None,
    id_crop_path: Path | None,
    roster: list[_RosterEntry],
    ollama_config: OllamaVisionConfig | None,
) -> tuple[_RosterEntry, float] | None:
    """Pick the higher-scoring roster match across tesseract and VLM OCRs."""
    best = _match_roster(ocr_id, ocr_name, roster)
    if ollama_config is None:
        return best
    if name_crop_path is None and id_crop_path is None:
        return best
    vlm_name = _vlm_field_text(
        name_crop_path, field_kind="name", ollama_config=ollama_config
    )
    vlm_id = _vlm_field_text(
        id_crop_path, field_kind="sid", ollama_config=ollama_config
    )
    vlm_match = _match_roster(vlm_id, vlm_name, roster)
    if vlm_match is not None and (best is None or vlm_match[1] > best[1]):
        return vlm_match
    return best


def _ocr_response_text(
    response_path: Path,
    *,
    ollama_config: OllamaVisionConfig | None,
) -> str:
    """Transcribe a question response crop, preferring the VLM when configured."""
    if ollama_config is not None:
        text = _vlm_response_text(response_path, ollama_config=ollama_config)
        if text:
            return text
    with Image.open(response_path) as image:
        return _ocr_image(image, psm=6)


def _resolved_crop_path(
    submission_dir: Path,
    relative: str | None,
) -> Path | None:
    """Return the absolute Path of a crop reference, if the file actually exists."""
    if not relative:
        return None
    path = submission_dir / relative
    return path if path.exists() else None


def _process_submission(
    md_path: Path,
    *,
    roster: list[_RosterEntry] | None,
    ollama_config: OllamaVisionConfig | None,
) -> tuple[int, int]:
    """OCR all crops referenced by one submission file and write updates.

    Returns (fields_updated, responses_updated) for the caller to report.
    """
    submission_dir = md_path.parent
    content = md_path.read_text()
    try:
        document: StudentSubmissionDocument = parse_student_submission_markdown(
            content, base_path=submission_dir
        )
    except Exception as exc:
        print(f"warning: failed to parse {md_path}: {exc}", file=sys.stderr)
        return (0, 0)

    name_crop_path = _resolved_crop_path(
        submission_dir, _frontmatter_value(content, "name_image")
    )
    id_crop_path = _resolved_crop_path(
        submission_dir, _frontmatter_value(content, "id_image")
    )

    ocr_name = ""
    if name_crop_path is not None:
        with Image.open(name_crop_path) as image:
            ocr_name = _ocr_image(image, psm=7)

    ocr_id = ""
    if id_crop_path is not None:
        with Image.open(id_crop_path) as image:
            ocr_id = _ocr_image_digits(image)

    student_name = ocr_name
    student_id = ocr_id

    if roster:
        match = _best_roster_match(
            ocr_id=ocr_id,
            ocr_name=ocr_name,
            name_crop_path=name_crop_path,
            id_crop_path=id_crop_path,
            roster=roster,
            ollama_config=ollama_config,
        )
        if match is not None:
            entry, _score = match
            student_name = entry.name
            student_id = entry.student_id

    fields_updated = 0
    if name_crop_path is not None:
        content = _replace_frontmatter_field(content, "name", student_name)
        fields_updated += 1
    if id_crop_path is not None:
        content = _replace_frontmatter_field(content, "student_id", student_id)
        fields_updated += 1

    responses_updated = 0
    for question in document.questions:
        if not question.response_image_path:
            continue
        response_path = submission_dir / question.response_image_path
        if not response_path.exists():
            continue

        response_text = _ocr_response_text(response_path, ollama_config=ollama_config)
        content = _replace_question_response_text(
            content, question.question_id, response_text
        )
        responses_updated += 1

    md_path.write_text(content)
    return (fields_updated, responses_updated)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Run OCR (tesseract, optionally + Ollama vision model) over the "
            "Name, ID, and response crops referenced from .submission.md "
            "files produced by submission-extractor, then write the "
            "transcribed values back into the frontmatter and each "
            "question's Response text."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        required=True,
        help=(
            "Path to a single .submission.md file or a directory containing "
            "per-student submission folders."
        ),
    )
    parser.add_argument(
        "--id-file",
        metavar="TSV",
        default=None,
        help=(
            "Tab-separated roster file with one 'ID<TAB>NAME' line per "
            "student. When given, OCR'd name/ID values are fuzzy-matched "
            "against the roster and replaced with the closest entry's "
            "canonical values."
        ),
    )
    parser.add_argument(
        "--ollama-fallback",
        action="store_true",
        help=(
            "Use an Ollama vision model alongside tesseract. The VLM "
            "transcribes each crop; for name/ID the higher-scoring roster "
            "match wins, and for response text the VLM transcription "
            "replaces tesseract's. Implicitly enabled whenever any "
            "--ollama-* option is set to a non-default value."
        ),
    )
    parser.add_argument(
        "--ollama-model",
        default=_DEFAULT_OLLAMA_MODEL,
        help=f"Ollama vision model (default: {_DEFAULT_OLLAMA_MODEL}).",
    )
    parser.add_argument(
        "--ollama-host",
        default=_DEFAULT_OLLAMA_HOST,
        help=f"Ollama server URL (default: {_DEFAULT_OLLAMA_HOST}).",
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=_DEFAULT_OLLAMA_TIMEOUT,
        help=f"Ollama request timeout in seconds (default: {_DEFAULT_OLLAMA_TIMEOUT}).",
    )
    return parser


def _ollama_enabled(args) -> bool:
    """Whether to wire up the VLM for this run."""
    return (
        args.ollama_fallback
        or args.ollama_host != _DEFAULT_OLLAMA_HOST
        or args.ollama_model != _DEFAULT_OLLAMA_MODEL
        or args.ollama_timeout != _DEFAULT_OLLAMA_TIMEOUT
    )


def _load_ollama_config(args) -> OllamaVisionConfig | None:
    if not _ollama_enabled(args):
        return None
    config = OllamaVisionConfig(
        model=args.ollama_model,
        host=args.ollama_host,
        timeout_seconds=args.ollama_timeout,
    )
    try:
        ensure_model_available(config)
    except Exception as exc:
        print(
            f"error: Ollama vision model not available ({exc}). "
            "Install/start Ollama and pull the model, or drop the --ollama-* options.",
            file=sys.stderr,
        )
        sys.exit(1)
    print(f"Ollama VLM enabled: {args.ollama_model} @ {args.ollama_host}")
    return config


def _collect_submission_files(input_path: str) -> list[Path]:
    path = Path(input_path)
    if path.is_file():
        if not path.name.endswith(".submission.md"):
            raise ValueError(f"input file must be a .submission.md: {path}")
        return [path]
    if path.is_dir():
        direct = sorted(path.glob("*.submission.md"))
        nested = sorted(path.glob("*/*.submission.md"))
        files = [*direct, *nested]
        if not files:
            raise ValueError(f"no .submission.md files found in {path}")
        return files
    raise FileNotFoundError(f"input path not found: {path}")


def run_cli() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    roster: list[_RosterEntry] | None = None
    if args.id_file:
        try:
            roster = _load_roster(Path(args.id_file))
        except (FileNotFoundError, ValueError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)

    ollama_config = _load_ollama_config(args)

    try:
        files = _collect_submission_files(args.input)
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Found {len(files)} .submission.md file(s)")
    for index, md_path in enumerate(files, start=1):
        fields_updated, responses_updated = _process_submission(
            md_path,
            roster=roster,
            ollama_config=ollama_config,
        )
        print(
            f"[{index}/{len(files)}] {md_path.name}: "
            f"{fields_updated} field(s), {responses_updated} response(s)"
        )


if __name__ == "__main__":
    run_cli()

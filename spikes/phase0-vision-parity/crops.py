"""Crop discovery for the Phase 0 vision-parity spike.

Walks `samples/**/student-submissions/*/*.submission.md`, which is the real
on-disk interchange format `core/submissions/student_file.py` writes (see
AI_README.md §2.1). Each `*.submission.md` file lists one or more questions;
each question with a `Response image:` line is one "crop" — a handwriting
image the current Ollama vision path (or Tesseract) already read once.

This module does NOT treat the `Response text:` value as ground truth. It is
the *incumbent's own prior output* (see README.md "The ground-truth trap").
Callers must label it `ollama-incumbent-recorded` and must not score other
backends against it. The only legitimate reference is `gold.toml`.

The parser below is a small hand-rolled line scanner, not a YAML loader.
The body of a `*.submission.md` file is YAML-*flavoured* but not valid YAML
(free-text answers contain bare colons, e.g. "5 to binary [A]:", which break
a real YAML scanner mid-document). Re-deriving the exact on-disk format here
is deliberate: it keeps this spike from importing or depending on anything
under `src/opengrader`, other than the two modules the task explicitly names
for backend parity (`core.ocr.ollama_vlm`, `core.ai_config`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

_QUESTION_HEADER = re.compile(r"^- (\S+):\s*$")
_FIELD_LINE = re.compile(r"^  - ([^:]+):[ \t]?(.*)$")
_BLOCK_CONTINUATION = re.compile(r"^    (.*)$")


@dataclass(frozen=True)
class Crop:
    """One answer-region image plus what the incumbent pipeline recorded for it."""

    sample: str
    """Directory name under samples/, e.g. 'cs146-quiz1'."""

    submission: str
    """Submission directory name, e.g. 'submission-0001'."""

    question: str
    """Question id within the submission.md, e.g. 'q2'."""

    image_path: Path
    """Absolute path to the response crop PNG."""

    read_by: str | None
    """What the incumbent pipeline used for this crop: 'vision-model' or 'tesseract'."""

    incumbent_response_text: str | None
    """The `Response text:` value already on disk. NOT ground truth — see module docstring."""

    @property
    def crop_id(self) -> str:
        return f"{self.sample}/{self.submission}/{self.question}"


def _parse_submission_md(path: Path) -> list[dict[str, str]]:
    """Parse the question blocks out of one *.submission.md file.

    Returns a list of dicts, one per question, with whatever fields were
    present (at minimum 'question'; usually also 'response_image', 'read_by',
    'response_text').
    """
    text = path.read_text(encoding="utf-8")
    # Drop the YAML front matter (student name/id/etc.) — not needed here and
    # genuinely valid YAML, but irrelevant to crop discovery.
    parts = text.split("---\n", 2)
    body = parts[2] if len(parts) >= 3 else text

    lines = body.splitlines()
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    current_field: str | None = None
    block_lines: list[str] | None = None

    def flush_field() -> None:
        nonlocal current_field, block_lines
        if current is not None and current_field is not None and block_lines is not None:
            current[current_field] = "\n".join(block_lines).rstrip("\n")
        current_field = None
        block_lines = None

    for line in lines:
        header = _QUESTION_HEADER.match(line)
        if header is not None:
            flush_field()
            current = {"question": header.group(1)}
            blocks.append(current)
            continue

        field = _FIELD_LINE.match(line)
        if field is not None and current is not None:
            flush_field()
            name, value = field.group(1).strip(), field.group(2).strip()
            key = name.lower().replace(" ", "_")
            if value == "|":
                current_field = key
                block_lines = []
            else:
                current[key] = value
            continue

        if block_lines is not None:
            cont = _BLOCK_CONTINUATION.match(line)
            if cont is not None:
                block_lines.append(cont.group(1))
                continue
            if line.strip() == "":
                # Blank line inside/after a block scalar: keep scanning, a
                # true block scalar under this format never has blank
                # interior lines in the samples observed, so treat this as
                # end-of-block.
                flush_field()
                continue
            # A non-continuation, non-field line ends the block scalar.
            flush_field()

    flush_field()
    return blocks


def discover_crops(samples_root: Path) -> list[Crop]:
    """Find every answer-region crop under samples_root.

    Only questions with a `Response image:` field whose referenced file
    exists on disk are returned — a missing image is skipped rather than
    producing a crop with no picture to send.
    """
    crops: list[Crop] = []
    for submission_md in sorted(samples_root.glob("*/student-submissions/*/*.submission.md")):
        submission_dir = submission_md.parent
        sample_name = submission_md.parents[2].name
        submission_name = submission_dir.name

        for block in _parse_submission_md(submission_md):
            question = block.get("question")
            image_field = block.get("response_image")
            if not question or not image_field:
                continue
            match = re.search(r"\((.*?)\)", image_field)
            if match is None:
                continue
            image_rel = match.group(1)
            image_path = (submission_dir / image_rel).resolve()
            if not image_path.is_file():
                continue

            crops.append(
                Crop(
                    sample=sample_name,
                    submission=submission_name,
                    question=question,
                    image_path=image_path,
                    read_by=block.get("read_by"),
                    incumbent_response_text=block.get("response_text"),
                )
            )
    return crops

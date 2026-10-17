"""Looking for a student's name or ID in their answer images before they leave.

The packet's text is scrubbed and checked, but an answer crop is a picture:
a student who signs an answer box, or writes their ID in the margin of one,
is in it. Each crop going into a packet is read with Tesseract on this
computer, and one in which the student's own name or ID turns up is held
back — the task then says so, and the teacher grades that answer.

Tesseract reads handwriting badly, so this catches clear writing and print
and misses scrawl. Matching is forgiving where OCR errs (an ID with one
digit misread, "0" read as "O") and strict where answers would trip it: one
name part alone counts only when it is capitalised, so a student named Max
does not lose every crop whose code says max().
"""

from __future__ import annotations

import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path

from .common import AgentError

__all__ = ["CropFinding", "find_identity", "read_crop_text", "require_tesseract", "scan_crops"]

_DIGIT_LOOKALIKES = str.maketrans({"O": "0", "o": "0", "D": "0", "l": "1", "I": "1", "|": "1", "i": "1", "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9"})
_MIN_NAME_PART = 3
_NAME_SIMILARITY = 0.84
_MIN_ID_DIGITS = 4


@dataclass(frozen=True)
class CropFinding:
    image: Path
    alias: str
    question_id: str
    what: str  # "name", "student id", or "unreadable image"


def require_tesseract() -> None:
    try:
        import pytesseract

        pytesseract.get_tesseract_version()
    except Exception as exc:  # noqa: BLE001 - any failure means it cannot check
        raise AgentError(
            "Tesseract, which checks each answer image for the student's name "
            f"before it goes in the copy, is not working here ({exc}). Install it "
            "(opengrader doctor says how), or skip the check on purpose."
        ) from None


def read_crop_text(image_path: Path) -> str:
    """Everything Tesseract can read in the crop: as scattered words, and as a block."""
    import pytesseract
    from PIL import Image, ImageOps

    with Image.open(image_path) as opened:
        image = ImageOps.grayscale(opened)
        if image.width < 1200:
            # Small crops read far better enlarged.
            scale = max(1, 1200 // max(image.width, 1))
            image = image.resize((image.width * scale, image.height * scale))
        # Two layouts: a name scribbled in a corner is found as sparse text,
        # one written on a line as a block.
        return "\n".join(
            pytesseract.image_to_string(image, config=f"--psm {psm}") for psm in (11, 6)
        )


def _id_found(text: str, student_id: str) -> bool:
    wanted = re.sub(r"\D", "", student_id)
    if len(wanted) < _MIN_ID_DIGITS:
        return False
    for digits in _digit_runs(text):
        if wanted in digits:
            return True
        if len(wanted) < 6:
            continue
        # A long ID with one digit misread still counts.
        for start in range(0, len(digits) - len(wanted) + 1):
            window = digits[start : start + len(wanted)]
            if sum(a != b for a, b in zip(window, wanted)) <= 1:
                return True
    return False


def _digit_runs(text: str) -> list[str]:
    """Numbers as OCR may have written them: look-alike letters read as
    digits, but only inside a token that is mostly digits already ("12O4"),
    so "is not my id" never turns into digits; groups split by a space or a
    dash ("1234 5678") are joined."""
    runs: list[str] = []
    current = ""
    for token in re.findall(r"[0-9A-Za-z|]+|[^0-9A-Za-z|]+", text):
        if token.strip(" -") == "":
            continue  # a separator inside a number
        real = sum(character.isdigit() for character in token)
        if real and real * 2 >= len(token):
            current += re.sub(r"\D", "", token.translate(_DIGIT_LOOKALIKES))
            continue
        if current:
            runs.append(current)
        current = ""
    if current:
        runs.append(current)
    return runs


def _name_found(text: str, name: str) -> bool:
    parts = [part for part in re.findall(r"[A-Za-z]+", name) if len(part) >= _MIN_NAME_PART]
    if not parts:
        return False
    words = re.findall(r"[A-Za-z]+", text)

    def matches(part: str, word: str) -> bool:
        a, b = part.casefold(), word.casefold()
        if len(a) <= 3:
            return a == b
        return SequenceMatcher(None, a, b).ratio() >= _NAME_SIMILARITY

    matched: set[str] = set()
    for part in parts:
        for word in words:
            if matches(part, word):
                matched.add(part)
                # One name part alone counts only written as a name is:
                # capitalised, and long enough not to be a common word.
                if len(part) >= 4 and word[:1].isupper():
                    return True
    return len(matched) >= 2


def find_identity(text: str, *, name: str, student_id: str) -> str:
    """"student id", "name", or "" when neither is in the text."""
    if _id_found(text, student_id):
        return "student id"
    if name.strip() and _name_found(text, name):
        return "name"
    return ""


def scan_crops(
    crops: list[tuple[Path, str, str, str, str]], *, jobs: int | None = None
) -> list[CropFinding]:
    """crops: (image, alias, question_id, student name, student id). Parallel:
    each read is its own Tesseract process."""

    def one(item: tuple[Path, str, str, str, str]) -> CropFinding | None:
        image, alias, question_id, name, student_id = item
        try:
            text = read_crop_text(image)
        except Exception:  # noqa: BLE001
            # A crop that cannot be read cannot be cleared either: held back.
            return CropFinding(image, alias, question_id, "unreadable image")
        what = find_identity(text, name=name, student_id=student_id)
        return CropFinding(image, alias, question_id, what) if what else None

    workers = jobs or min(8, os.cpu_count() or 2)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return [finding for finding in pool.map(one, crops) if finding is not None]

"""Math crop → LaTeX string: the seam, and the backends behind it.

Handwritten mathematics has been transcribable, by asking the
Ollama vision model to emit LaTeX (``submission-extractor --llm-math``). That
was chosen over a dedicated math-OCR model because it added no dependency and
reused a server instructors already run — with a note that a specialist backend
was the natural upgrade if field accuracy proved insufficient, since both ends
of the operation are the same: *a crop goes in, a LaTeX string comes out*.

This module is that seam, made explicit. A backend is a
:data:`MathTranscriber` — ``Path -> str | None`` — and the two that exist are:

* **vlm** (default) — the Ollama vision model, unchanged. It reads the *whole*
  answer, prose and all, and marks the mathematics up inline, so it is right for
  the ordinary exam question that says "explain, showing your working".
* **pix2tex** — a dedicated image-to-LaTeX transformer. It is markedly better at
  dense notation, and it transcribes the crop as **one mathematical
  expression**: handed a paragraph it will return nonsense with great
  confidence. It belongs on an exam whose answers *are* equations.

That difference is a property of the tools, not a bug to paper over, so the
choice is the instructor's (``--math-backend``) and is documented rather than
guessed at. Three rules keep the swap safe:

* **Never lose an answer.** A backend that is unavailable, errors, or returns
  nothing falls back to the existing path. Swapping the transcriber can change
  how good a transcription is; it must never change whether there is one.
* **The heavy import is lazy, and its absence is an ordinary answer.** pix2tex
  pulls in torch, which is a gigabyte-scale dependency an offline-first tool
  cannot make mandatory. :func:`describe_backend` reports availability without
  importing anything expensive, so the CLI and the setup screen can *say* the
  backend is not installed instead of crashing on it.
* **The model is loaded once per process.** Building a pix2tex ``LatexOCR`` costs
  seconds; doing it per crop would make the specialist backend slower than the
  generalist it replaces. Extraction runs in a process pool, so the cache is
  per-worker by construction — which is also why a backend crosses the process
  boundary as a **name**, never as a callable (`submissions/batch.py` requires
  every job field to stay picklable).
"""

from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

# A backend: hand it a crop, get LaTeX back (or ``None`` when it has nothing to
# say). Deliberately the narrowest possible contract — everything else about a
# transcription (confidence, prose, fallback) is the caller's business.
MathTranscriber = Callable[[Path], "str | None"]

MathBackendName = Literal["vlm", "pix2tex"]

DEFAULT_MATH_BACKEND: MathBackendName = "vlm"
MATH_BACKEND_NAMES: tuple[MathBackendName, ...] = ("vlm", "pix2tex")

# The pip extra that installs the specialist backend, named in every message
# about it being missing so the fix is never a search.
PIX2TEX_EXTRA = "uv sync --extra math"

# A transcription this short is not an equation, it is the model shrugging.
# pix2tex always returns *something*; an empty or one-character result is how it
# says it found nothing, and treating that as an answer would overwrite a
# perfectly good Tesseract read with "x".
_MIN_USEFUL_LATEX = 2


class MathOcrError(RuntimeError):
    """A math backend was asked for and could not be provided."""


@dataclass(frozen=True)
class MathBackend:
    """What a backend is, and whether this machine can actually run it."""

    name: MathBackendName
    available: bool
    summary: str
    detail: str = ""

    def describe(self) -> str:
        state = "available" if self.available else "not installed"
        line = f"{self.name} ({state}): {self.summary}"
        return f"{line}\n  {self.detail}" if self.detail else line


def normalize_latex(raw: str | None) -> str:
    """Wrap a backend's bare LaTeX in ``$…$`` unless it already carries math.

    Load-bearing rather than cosmetic: the grade screen decides whether to offer
    "View rendered math" with `tui/latex_render.response_has_latex`, which looks
    for ``$…$``. A specialist backend returns ``\\frac{1}{2}`` with no delimiters,
    so without this the transcription would be stored, be correct, and silently
    never render.
    """
    if raw is None:
        return ""
    text = raw.strip()
    if len(text) < _MIN_USEFUL_LATEX:
        return ""
    if "$" in text:
        return text
    # Strip a display-math wrapper a backend may have added; ``$…$`` is the one
    # delimiter the renderer understands.
    text = re.sub(r"^\\\[(.*)\\\]$", r"\1", text, flags=re.DOTALL).strip()
    text = re.sub(r"^\\\((.*)\\\)$", r"\1", text, flags=re.DOTALL).strip()
    if not text:
        return ""
    return f"${text}$"


def pix2tex_installed() -> bool:
    """Is the specialist backend importable, without importing it?

    ``find_spec`` answers the question for the price of a path lookup. Actually
    importing pix2tex drags in torch, which costs seconds and hundreds of
    megabytes of RSS — far too much to pay just to print a ``--help`` line.
    """
    try:
        return importlib.util.find_spec("pix2tex") is not None
    except (ImportError, ValueError):
        return False


def describe_backend(name: str) -> MathBackend:
    """Describe one backend, including whether it can run here."""
    if name == "pix2tex":
        available = pix2tex_installed()
        return MathBackend(
            name="pix2tex",
            available=available,
            summary="dedicated image-to-LaTeX model (best on dense notation)",
            detail=(
                "Transcribes the crop as one mathematical expression, so use it "
                "for answers that *are* equations — on a prose answer it will "
                "return nonsense. "
                + (
                    "Runs locally; the model is downloaded once on first use."
                    if available
                    else f"Not installed — add it with: {PIX2TEX_EXTRA}"
                )
            ),
        )
    return MathBackend(
        name="vlm",
        available=True,
        summary="the Ollama vision model already used for handwriting",
        detail=(
            "Reads the whole answer and marks the mathematics up inline, so it "
            "suits questions that mix prose and working. Needs a running Ollama "
            "server, like every other vision step."
        ),
    )


def available_backends() -> tuple[MathBackend, ...]:
    """Every backend, for a ``--help`` epilog or a setup-screen hint."""
    return tuple(describe_backend(name) for name in MATH_BACKEND_NAMES)


# --------------------------------------------------------------------------- #
# pix2tex
# --------------------------------------------------------------------------- #

# One model per process. Building it costs seconds and it is stateless across
# calls, so rebuilding per crop would make the *faster* backend the slower one.
_PIX2TEX_MODEL = None


def _load_pix2tex():
    """Import and build the pix2tex model, once.

    Imported inside the function on purpose: this module is imported by the
    extractor on every run, and pix2tex pulls in torch.
    """
    global _PIX2TEX_MODEL
    if _PIX2TEX_MODEL is not None:
        return _PIX2TEX_MODEL
    try:
        from pix2tex.cli import LatexOCR  # noqa: PLC0415 — deliberately lazy
    except ImportError as exc:
        raise MathOcrError(
            "the pix2tex math backend is not installed — add it with: "
            f"{PIX2TEX_EXTRA}"
        ) from exc
    try:
        _PIX2TEX_MODEL = LatexOCR()
    except Exception as exc:  # noqa: BLE001 — any load failure is the same answer
        raise MathOcrError(f"could not load the pix2tex model: {exc}") from exc
    return _PIX2TEX_MODEL


def transcribe_with_pix2tex(image_path: Path) -> str | None:
    """Read one crop as a single LaTeX expression.

    Returns ``None`` — never raises — when the model cannot read the image, so
    the caller falls back to the ordinary transcription path rather than losing
    the student's answer to a backend choice.
    """
    try:
        model = _load_pix2tex()
    except MathOcrError:
        return None
    try:
        from PIL import Image  # noqa: PLC0415 — kept beside its only use

        with Image.open(image_path) as image:
            raw = model(image.convert("RGB"))
    except Exception:  # noqa: BLE001 — an unreadable crop is not an error here
        return None
    return normalize_latex(raw) or None


def resolve_math_transcriber(
    name: str = DEFAULT_MATH_BACKEND, *, strict: bool = True
) -> MathTranscriber | None:
    """The transcriber for a backend name, or ``None`` for the built-in path.

    ``None`` means "no dedicated backend" — the caller keeps using the vision
    model exactly as before, which is why ``vlm`` resolves to it. Resolving a
    backend is deliberately separate from *using* one so a CLI can fail at
    startup with an actionable message instead of on the first crop of a long
    batch (``strict``); pass ``strict=False`` to degrade to the built-in path
    instead.
    """
    if name == "pix2tex":
        if not pix2tex_installed():
            if strict:
                raise MathOcrError(
                    "--math-backend pix2tex needs the pix2tex package, which is "
                    f"not installed. Add it with: {PIX2TEX_EXTRA}  (or use "
                    "--math-backend vlm, which needs no extra install)"
                )
            return None
        return transcribe_with_pix2tex
    if name != DEFAULT_MATH_BACKEND and strict:
        raise MathOcrError(
            f"unknown math backend {name!r} — choose one of: "
            f"{', '.join(MATH_BACKEND_NAMES)}"
        )
    return None

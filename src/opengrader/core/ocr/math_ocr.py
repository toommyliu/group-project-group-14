from __future__ import annotations

import importlib.util
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

MathTranscriber = Callable[[Path], "str | None"]

MathBackendName = Literal["vlm", "pix2tex"]

DEFAULT_MATH_BACKEND: MathBackendName = "vlm"
MATH_BACKEND_NAMES: tuple[MathBackendName, ...] = ("vlm", "pix2tex")

PIX2TEX_EXTRA = "uv sync --extra math"

_MIN_USEFUL_LATEX = 2


class MathOcrError(RuntimeError):
    pass


@dataclass(frozen=True)
class MathBackend:
    name: MathBackendName
    available: bool
    summary: str
    detail: str = ""

    def describe(self) -> str:
        state = "available" if self.available else "not installed"
        line = f"{self.name} ({state}): {self.summary}"
        return f"{line}\n  {self.detail}" if self.detail else line


def normalize_latex(raw: str | None) -> str:
    if raw is None:
        return ""
    text = raw.strip()
    if len(text) < _MIN_USEFUL_LATEX:
        return ""
    if "$" in text:
        return text
    text = re.sub(r"^\\\[(.*)\\\]$", r"\1", text, flags=re.DOTALL).strip()
    text = re.sub(r"^\\\((.*)\\\)$", r"\1", text, flags=re.DOTALL).strip()
    if not text:
        return ""
    return f"${text}$"


def pix2tex_installed() -> bool:
    try:
        return importlib.util.find_spec("pix2tex") is not None
    except (ImportError, ValueError):
        return False


def describe_backend(name: str) -> MathBackend:
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
    return tuple(describe_backend(name) for name in MATH_BACKEND_NAMES)


_PIX2TEX_MODEL = None


def _load_pix2tex():
    global _PIX2TEX_MODEL
    if _PIX2TEX_MODEL is not None:
        return _PIX2TEX_MODEL
    try:
        from pix2tex.cli import LatexOCR  # noqa: PLC0415
    except ImportError as exc:
        raise MathOcrError(
            "the pix2tex math backend is not installed — add it with: "
            f"{PIX2TEX_EXTRA}"
        ) from exc
    try:
        _PIX2TEX_MODEL = LatexOCR()
    except Exception as exc:  # noqa: BLE001
        raise MathOcrError(f"could not load the pix2tex model: {exc}") from exc
    return _PIX2TEX_MODEL


def transcribe_with_pix2tex(image_path: Path) -> str | None:
    try:
        model = _load_pix2tex()
    except MathOcrError:
        return None
    try:
        from PIL import Image  # noqa: PLC0415

        with Image.open(image_path) as image:
            raw = model(image.convert("RGB"))
    except Exception:  # noqa: BLE001
        return None
    return normalize_latex(raw) or None


def resolve_math_transcriber(
    name: str = DEFAULT_MATH_BACKEND, *, strict: bool = True
) -> MathTranscriber | None:
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

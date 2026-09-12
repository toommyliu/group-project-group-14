from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opengrader.core.pdf.templates.fonts import resolve_template_fonts

if TYPE_CHECKING:  # pragma: no cover
    import fitz

# china-s covers Latin and Cyrillic as well as CJK, allowing mixed-script lines.
_CJK_FONT_NAME = "china-s"

_MISSING_GLYPH_PLACEHOLDER = "?"


@dataclass(frozen=True)
class DrawableText:
    fontname: str
    text: str
    metrics: fitz.Font

    def width(self, fontsize: float) -> float:
        return self.metrics.text_length(self.text, fontsize=fontsize)


@dataclass(frozen=True)
class _FontCandidate:
    fontname: str
    fontfile: str | None
    metrics: fitz.Font

    def can_render(self, text: str) -> bool:
        return all(
            character.isspace() or self.metrics.has_glyph(ord(character)) > 0
            for character in text
        )

    def substitute(self, text: str) -> str:
        return "".join(
            character
            if character.isspace() or self.metrics.has_glyph(ord(character)) > 0
            else _MISSING_GLYPH_PLACEHOLDER
            for character in text
        )


@dataclass(frozen=True)
class TextFonts:
    regular: tuple[_FontCandidate, ...]
    bold: tuple[_FontCandidate, ...]

    def choose(self, text: str, *, bold: bool = False) -> DrawableText:
        candidates = self.bold if bold else self.regular
        for candidate in candidates:
            if candidate.can_render(text):
                return DrawableText(
                    fontname=candidate.fontname,
                    text=text,
                    metrics=candidate.metrics,
                )

        primary = candidates[0]
        return DrawableText(
            fontname=primary.fontname,
            text=primary.substitute(text),
            metrics=primary.metrics,
        )

    def fontfile_for(self, fontname: str) -> str | None:
        for candidate in (*self.regular, *self.bold):
            if candidate.fontname == fontname:
                return candidate.fontfile
        return None


def _cjk_candidate() -> _FontCandidate | None:
    import fitz

    try:
        metrics = fitz.Font(fontname=_CJK_FONT_NAME)
    except Exception:  # pragma: no cover
        return None
    return _FontCandidate(fontname=_CJK_FONT_NAME, fontfile=None, metrics=metrics)


@functools.lru_cache(maxsize=1)
def load_text_fonts() -> TextFonts:
    template_fonts = resolve_template_fonts()

    regular = _FontCandidate(
        fontname=template_fonts.regular_name,
        fontfile=template_fonts.regular_file,
        metrics=template_fonts.regular_metrics,
    )
    bold = _FontCandidate(
        fontname=template_fonts.bold_name,
        fontfile=template_fonts.bold_file,
        metrics=template_fonts.bold_metrics,
    )

    cjk = _cjk_candidate()
    # The built-in CJK font has no bold face.
    regular_chain = (regular, cjk) if cjk else (regular,)
    bold_chain = (bold, cjk) if cjk else (bold,)

    return TextFonts(
        regular=tuple(c for c in regular_chain if c is not None),
        bold=tuple(c for c in bold_chain if c is not None),
    )

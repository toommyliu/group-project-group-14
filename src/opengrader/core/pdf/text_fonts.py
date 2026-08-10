"""Fonts for generated PDF text that has to render *anyone's* name.

The returnable Grade Summary page prints a student's real name, and that name is
not under our control: a class list can hold "Jose", "Jose Garcia", "Dmitri" in
Cyrillic, "Nguyen" with Vietnamese tone marks, or a Chinese name written in Han
characters. PDF's built-in ("base-14") fonts encode text as **WinAnsi** — a
256-character set — so every character outside it is silently replaced with a dot
when the page is written. Nothing raises, nothing warns: the student simply
receives a returned exam with their own name mangled.

This module answers "which font can actually draw this string?" by trying, in
order:

1. the bundled Computer Modern TTFs — the same typeface the printed exam template
   uses, covering Latin, Latin Extended (accents and Vietnamese tone marks),
   Greek and Cyrillic;
2. PyMuPDF's built-in CJK font, which covers Chinese/Japanese/Korean *and* plain
   Latin, so a mixed line like "Student: <Han name> (id 41)" stays in one font;
3. a visible ``?`` in place of any character no available font can draw (Arabic,
   Hebrew, emoji). A question mark is deliberate: an obviously missing character
   is something a human notices and can work around, whereas a silently dropped
   one looks like the name was never there.

A font is chosen **per string** rather than per character because a line of text
is almost always a single script, and mixing fonts inside one line would break
the width measurement that wrapping depends on.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from typing import TYPE_CHECKING

from opengrader.core.pdf.templates.fonts import resolve_template_fonts

if TYPE_CHECKING:  # pragma: no cover - typing only
    # fitz (PyMuPDF, ~110ms cold) is only needed once a font is actually
    # resolved/measured (see _cjk_candidate below); merely defining this
    # module's dataclasses must stay cheap.
    import fitz

# PyMuPDF ships built-in CJK fonts under short names; "china-s" (Simplified
# Chinese) has the widest repertoire of the four and also covers Latin and
# Cyrillic, which is what lets it serve as a single fallback for a mixed line.
_CJK_FONT_NAME = "china-s"

# Drawn in place of a character no available font can render. See the module
# docstring for why a visible placeholder beats dropping the character.
_MISSING_GLYPH_PLACEHOLDER = "?"


@dataclass(frozen=True)
class DrawableText:
    """A string paired with a font that is able to draw every character in it.

    ``text`` may differ from what was asked for: characters no font could render
    have been replaced by ``_MISSING_GLYPH_PLACEHOLDER``. Callers must draw and
    measure *this* string, or the layout will not match the page.
    """

    fontname: str
    text: str
    metrics: fitz.Font

    def width(self, fontsize: float) -> float:
        """Return how wide this text renders, in PDF points.

        ``fitz.get_text_length`` only accepts the base-14 font names, so an
        embedded font has to be measured through its own ``fitz.Font``.
        """
        return self.metrics.text_length(self.text, fontsize=fontsize)


@dataclass(frozen=True)
class _FontCandidate:
    """One typeface we can try: what to call it, what to embed, how to measure."""

    fontname: str
    # The TTF to embed in the PDF, or None for a PyMuPDF built-in font that
    # needs no file (the CJK fonts and the base-14 fallbacks).
    fontfile: str | None
    metrics: fitz.Font

    def can_render(self, text: str) -> bool:
        """True when this font has a glyph for every character in `text`.

        Whitespace is skipped: a space is a valid glyph in every font, but some
        fonts report nothing for exotic space characters and a name should not
        fall through to the CJK font over one non-breaking space.
        """
        return all(
            character.isspace() or self.metrics.has_glyph(ord(character)) > 0
            for character in text
        )

    def substitute(self, text: str) -> str:
        """Return `text` with every character this font cannot draw replaced."""
        return "".join(
            character
            if character.isspace() or self.metrics.has_glyph(ord(character)) > 0
            else _MISSING_GLYPH_PLACEHOLDER
            for character in text
        )


@dataclass(frozen=True)
class TextFonts:
    """The ordered regular/bold candidates used to draw generated PDF text."""

    regular: tuple[_FontCandidate, ...]
    bold: tuple[_FontCandidate, ...]

    def choose(self, text: str, *, bold: bool = False) -> DrawableText:
        """Pick the first candidate that can render `text` in full.

        When none can — a script we bundle no font for — the *first* candidate is
        used with the unrenderable characters substituted, so the surrounding
        text still reads normally in the document's own typeface.
        """
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
        """Return the TTF to embed for a chosen font, or None if it needs none."""
        for candidate in (*self.regular, *self.bold):
            if candidate.fontname == fontname:
                return candidate.fontfile
        return None


def _cjk_candidate() -> _FontCandidate | None:
    """Build the CJK fallback, or None if this PyMuPDF build has no such font.

    Treated as optional rather than required: losing it costs Han characters a
    question mark each, which is far better than the whole summary page failing
    to generate over a font that was never essential.
    """
    import fitz  # see the TYPE_CHECKING import above for why this is deferred

    try:
        metrics = fitz.Font(fontname=_CJK_FONT_NAME)
    except Exception:  # pragma: no cover - depends on the PyMuPDF build
        return None
    return _FontCandidate(fontname=_CJK_FONT_NAME, fontfile=None, metrics=metrics)


@functools.lru_cache(maxsize=1)
def load_text_fonts() -> TextFonts:
    """Resolve the font stack once per process.

    Cached because building a ``fitz.Font`` parses a font program off disk, and
    the summary page is regenerated on *every* grade save — re-reading the TTFs
    each time would put a file read in the middle of the grading loop.
    """
    # Reuses the exam template's font resolution, so a returned summary page is
    # set in the same typeface as the paper the student sat. It already falls
    # back to a base-14 font when the bundled assets are missing.
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
    # The CJK font has no bold cut, so bold text falls back to the same face —
    # a name in the wrong weight is a cosmetic loss, an unreadable one is not.
    regular_chain = (regular, cjk) if cjk else (regular,)
    bold_chain = (bold, cjk) if cjk else (bold,)

    return TextFonts(
        regular=tuple(c for c in regular_chain if c is not None),
        bold=tuple(c for c in bold_chain if c is not None),
    )

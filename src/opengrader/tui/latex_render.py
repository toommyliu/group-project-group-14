"""Render a response's LaTeX math to an image for the grade screen.

When ``submission-extractor --llm-math`` is used, a math answer is transcribed
with LaTeX (``$...$``) inside ``response_text`` instead of flattened plain text.
This module turns that text into a small PNG the grade screen can show beside the
handwriting, so a grader reads proper fractions/exponents/integrals rather than
squinting at ``\\frac{dy}{dx}``.

Rendering uses matplotlib's built-in **mathtext**, not a system LaTeX install —
so it stays offline and dependency-light (matplotlib is already a dependency for
the statistics charts). mathtext covers the common notation (fractions,
super/subscripts, roots, integrals, sums, Greek); anything it can't parse makes
the render fail *cleanly* (returns ``False``), and the caller simply keeps
showing the raw text — never a crash or a half-written file.
"""

from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

# Matches an inline ``$...$`` or display ``$$...$$`` math span. Non-greedy so
# two separate spans on one line don't merge; DOTALL so a span may wrap lines.
_LATEX_SPAN_RE = re.compile(r"\$\$?.+?\$\$?", re.DOTALL)


def response_has_latex(text: str | None) -> bool:
    """Whether ``text`` contains at least one ``$…$`` math span worth rendering."""
    return bool(_LATEX_SPAN_RE.search(text or ""))


def render_response_latex_png(
    text: str | None,
    dest: str | Path,
    *,
    width: int = 1100,
    dpi: int = 150,
) -> bool:
    """Render ``text`` (prose + ``$…$`` math) to a PNG at ``dest``.

    Returns ``True`` on success, ``False`` when there is no math to render or the
    LaTeX can't be parsed by mathtext. The file is written only on success (we
    render to memory first), so a failed parse never leaves a corrupt PNG behind.
    """
    if not response_has_latex(text):
        return False

    # Import lazily: matplotlib is only needed on the (opt-in) math path, and
    # importing at module load would tax every grade-screen import.
    from matplotlib.figure import Figure

    lines = [line for line in (text or "").splitlines() if line.strip()] or [text or ""]
    try:
        height = max(0.6, 0.5 * len(lines) + 0.3)
        figure = Figure(
            figsize=(width / dpi, height),
            dpi=dpi,
            facecolor="#181818",
        )
        count = len(lines)
        for index, line in enumerate(lines):
            y = 1.0 - (index + 0.5) / count
            figure.text(
                0.03,
                y,
                line,
                color="#E4E4E4",
                fontsize=18,
                va="center",
                ha="left",
            )
        buffer = BytesIO()
        # mathtext parse errors surface here (at draw time), so this is the
        # single point we guard.
        figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    except Exception:
        return False

    Path(dest).write_bytes(buffer.getvalue())
    return True

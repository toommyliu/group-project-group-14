from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path

_LATEX_SPAN_RE = re.compile(r"\$\$?.+?\$\$?", re.DOTALL)


def response_has_latex(text: str | None) -> bool:
    return bool(_LATEX_SPAN_RE.search(text or ""))


def render_response_latex_png(
    text: str | None,
    dest: str | Path,
    *,
    width: int = 1100,
    dpi: int = 150,
) -> bool:
    if not response_has_latex(text):
        return False

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
        # mathtext can fail during drawing, after parsing appears to succeed.
        figure.savefig(buffer, format="png", facecolor=figure.get_facecolor())
    except Exception:
        return False

    Path(dest).write_bytes(buffer.getvalue())
    return True

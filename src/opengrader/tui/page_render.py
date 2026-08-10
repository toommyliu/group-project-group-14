"""Render a source page of a student's scan to a PNG for the grade screen.

The sibling of ``tui/latex_render.py``: both exist because the response viewer
displays a *file*, so anything other than the extracted crop has to be
rasterized to disk first. Here that is a whole page of the student's scanned
PDF, with the region the crop came from outlined — the "show me what's around
this answer" view behind the grade screen's ``w`` hotkey.

**Anonymous grading** is handled here rather than by hiding the hotkey: with
``redact_identity`` set, the page's Name/Student-ID header is painted out
before the PNG is written (``core/submissions/identity_redaction.py``), so the
only copy that ever reaches the disk cache — and therefore the only thing the
``o`` external-viewer hatch can open — is already redacted. When the header
cannot be located at all the page is refused instead of shown, which is the one
case that degrades to "turn on teacher mode".

Failures are reported as a falsey :class:`PageRenderOutcome` rather than
raised: a scan that has been moved, a page number a stale record points past
the end of, or a decompression-bomb guard tripping in ``render_page_to_image``
must all leave the grader on the crop with a status line, never crash
mid-grading. The PNG is written only after a successful render, so a failure
never leaves a corrupt file behind.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from opengrader.core.pdf_utils import draw_page_overlays, render_pdf_page
from opengrader.core.submissions.identity_redaction import plan_identity_redaction
from opengrader.core.submissions.page_context import PageContextEntry

# Full pages are rendered coarser than the 300 DPI extraction crops: a whole
# US-Letter sheet at 200 DPI is ~1700x2200, which is plenty to read handwriting
# in the terminal (and in the external viewer the `o` hotkey opens) while
# keeping the render fast enough to feel instant when paging.
DEFAULT_PAGE_DPI = 200


@dataclass(frozen=True)
class PageRenderOutcome:
    """Result of one page render. Falsey when nothing was written.

    ``reason`` is written for the grade screen's status line, so it says what
    went wrong in the grader's terms; ``redacted`` records whether anything was
    actually painted out, which the page caption reports.
    """

    ok: bool
    redacted: bool = False
    reason: str = ""

    def __bool__(self) -> bool:
        return self.ok


def render_page_context_png(
    pdf_path: str | Path,
    entry: PageContextEntry,
    dest: str | Path,
    *,
    dpi: int = DEFAULT_PAGE_DPI,
    redact_identity: bool = False,
) -> PageRenderOutcome:
    """Render one page-context entry to ``dest``.

    With ``redact_identity`` the student's name/id header is blacked out first;
    the question's own crop region is protected from the redaction, since
    covering the answer under review would be worse than the leak it prevents.
    """
    if not pdf_path:
        return PageRenderOutcome(
            False, reason="No source scan is recorded for this submission."
        )

    try:
        image, width, height = render_pdf_page(
            str(pdf_path), entry.page_number, dpi=dpi
        )
    except Exception:
        return PageRenderOutcome(
            False,
            reason=(
                f"Could not render page {entry.page_number} of the source scan."
            ),
        )

    redactions: tuple[tuple[float, float, float, float], ...] = ()
    note = ""
    if redact_identity:
        plan = plan_identity_redaction(image, protect=entry.highlights)
        if not plan.available:
            return PageRenderOutcome(
                False,
                reason=(
                    "Anonymous grading hides whole pages here: "
                    f"{plan.reason}. Turn on teacher mode (t) to view them."
                ),
            )
        redactions = plan.bands
        note = plan.reason

    try:
        page = draw_page_overlays(
            image,
            width,
            height,
            highlights=entry.highlights,
            redactions=redactions,
        )
        buffer = BytesIO()
        page.save(buffer, format="PNG")
    except Exception:
        return PageRenderOutcome(
            False,
            reason=f"Could not render page {entry.page_number} of the source scan.",
        )

    Path(dest).write_bytes(buffer.getvalue())
    return PageRenderOutcome(True, redacted=bool(redactions), reason=note)

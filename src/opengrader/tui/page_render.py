from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from opengrader.core.pdf_utils import draw_page_overlays, render_pdf_page
from opengrader.core.submissions.identity_redaction import plan_identity_redaction
from opengrader.core.submissions.page_context import PageContextEntry

DEFAULT_PAGE_DPI = 200


@dataclass(frozen=True)
class PageRenderOutcome:
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

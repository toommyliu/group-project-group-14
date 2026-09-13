"""Render student-facing assignment PDFs through the CLI's template renderer."""

from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import HTTPException
from fastapi.responses import Response

from opengrader.core.pdf.templates import generate_template_pdf
from opengrader.core.qa_file import QADocument, qa_to_template_questions
from opengrader.server.assignment_qa import AssignmentInput


class AssignmentPDFInput(AssignmentInput):
    version: str | None = None


PDF_RESPONSES = {
    200: {"content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}}}
}


def assignment_pdf_response(document: QADocument, version: str | None) -> Response:
    """Return a complete PDF, cleaning temporary files even if rendering fails."""
    versions = document.versions or ["A"]
    selected = version if version is not None else versions[0]
    if selected not in versions:
        raise HTTPException(422, "Choose an available assignment version.")
    try:
        with TemporaryDirectory(prefix="opengrader-assignment-") as temporary:
            output = Path(temporary) / "assignment.pdf"
            generate_template_pdf(
                output_path=output,
                version=selected,
                questions=qa_to_template_questions(document, version=selected),
                title=document.title or "Untitled assignment",
                instructions=document.instructions,
                continued_page_header=document.continued_page_header,
                start_questions_on_first_page=document.questions_start_page == 1,
            )
            content = output.read_bytes()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(500, "Could not generate the PDF. Check server storage and try again.") from exc
    return Response(
        content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="assignment.pdf"', "Cache-Control": "no-store"},
    )

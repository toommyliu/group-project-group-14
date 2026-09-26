"""Parse assignment setup content using the same QA model as grading."""

from pydantic import BaseModel

from opengrader.core.grading.questions import flatten_question_contexts
from opengrader.core.qa_file import QADocument, QALineNote, parse_qa_markdown, qa_line_notes
from opengrader.core.qa_file.editing import DocumentEdit, QuestionEdit, edit_qa_markdown
from opengrader.core.qa_file.errors import QAErrorLocation, QAParseError


class AssignmentInput(BaseModel):
    qa_markdown: str


class QAPreview(BaseModel):
    document: QADocument
    warnings: list[str]
    notes: list[QALineNote]


class VisualEditInput(AssignmentInput):
    edits: list[QuestionEdit]
    metadata: DocumentEdit | None = None


class VisualEditResult(AssignmentInput):
    preview: QAPreview | None
    validation_error: str | None
    validation_location: QAErrorLocation | None = None


def edit_assignment_draft(request: VisualEditInput) -> VisualEditResult:
    source = edit_qa_markdown(request.qa_markdown, request.edits, request.metadata)
    try:
        preview = _parse_preview(source)
    except ValueError as exc:
        return VisualEditResult(
            qa_markdown=source,
            preview=None,
            validation_error=str(exc),
            validation_location=exc.location if isinstance(exc, QAParseError) else None,
        )
    return VisualEditResult(
        qa_markdown=source,
        preview=preview,
        validation_error=_question_validation_error(preview.document),
    )


def preview_qa(content: str) -> QAPreview:
    preview = _parse_preview(content)
    error = _question_validation_error(preview.document)
    if error:
        raise ValueError(error)
    return preview


def _question_validation_error(document: QADocument) -> str | None:
    versions = document.version_questions or {"": document.questions}
    for version, questions in versions.items():
        if not flatten_question_contexts(questions):
            suffix = f" in version {version!r}" if version else ""
            return f"No grading questions found{suffix}."
    return None


def _parse_preview(content: str) -> QAPreview:
    document = parse_qa_markdown(content)
    warnings = []
    if "![" in content or "<img" in content.lower():
        warnings.append(
            "Referenced images are unavailable in this preview. "
            "Their references will be preserved in the saved QA file."
        )
    return QAPreview(document=document, warnings=warnings, notes=qa_line_notes(content))

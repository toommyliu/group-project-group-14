"""Parse assignment setup content using the same QA model as grading."""

from pydantic import BaseModel

from opengrader.core.grading.questions import flatten_question_contexts
from opengrader.core.qa_file import QADocument, parse_qa_markdown


class AssignmentInput(BaseModel):
    qa_markdown: str


class QAPreview(BaseModel):
    document: QADocument
    warnings: list[str]


def preview_qa(content: str) -> QAPreview:
    document = parse_qa_markdown(content)
    versions = document.version_questions or {"": document.questions}
    for version, questions in versions.items():
        if not flatten_question_contexts(questions):
            suffix = f" in version {version!r}" if version else ""
            raise ValueError(f"No grading questions found{suffix}.")
    warnings = []
    if "![" in content or "<img" in content.lower():
        warnings.append(
            "Referenced images are unavailable in this preview. "
            "Their references will be preserved in the saved QA file."
        )
    return QAPreview(document=document, warnings=warnings)

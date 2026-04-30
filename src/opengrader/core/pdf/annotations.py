from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import fitz

from opengrader.core.source_location import QuestionSourceSegment


_ANNOTATION_SUBJECT_PREFIX = "cm:"
_ANNOTATION_HASH_KEY = b"opengrader-pdf-comment-v1"


def sync_question_comment_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    question_id: str,
    comment: str,
    source_segments: tuple[QuestionSourceSegment, ...],
) -> None:
    """Synchronize one question comment into an annotated PDF copy."""
    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")
    if not source_segments:
        raise ValueError(
            "Question source metadata missing; regenerate submission files to enable PDF annotations."
        )

    stripped_comment = comment.strip()
    if not stripped_comment and not annotated_pdf_path.exists():
        return

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    subject = _comment_subject(question_id)
    with fitz.open(annotated_pdf_path) as document:
        _remove_existing_question_annotations(document, subject=subject)
        if stripped_comment:
            _add_comment_annotation(
                document,
                segment=source_segments[0],
                subject=subject,
                comment=stripped_comment,
            )
        document.saveIncr()


def _comment_subject(question_id: str) -> str:
    """Return the stable opaque PDF subject used to identify one question note.

    The subject must be deterministic so repeated comment saves replace the
    prior annotation instead of adding duplicates. It must also avoid readable
    identifiers because students may inspect annotation metadata in the PDF.
    """
    digest = hashlib.blake2s(
        question_id.encode("utf-8"),
        digest_size=16,
        key=_ANNOTATION_HASH_KEY,
        person=b"pdfcmt1",
    ).hexdigest()
    return f"{_ANNOTATION_SUBJECT_PREFIX}{digest}"


def _remove_existing_question_annotations(
    document: fitz.Document,
    *,
    subject: str,
) -> None:
    """Delete the current opaque annotation for a question before rewriting it."""
    for page in document:
        annot = page.first_annot
        while annot is not None:
            next_annot = annot.next
            info = annot.info or {}
            if info.get("subject") == subject:
                page.delete_annot(annot)
            annot = next_annot


def _add_comment_annotation(
    document: fitz.Document,
    *,
    segment: QuestionSourceSegment,
    subject: str,
    comment: str,
) -> None:
    page_index = segment.page_number - 1
    if page_index < 0 or page_index >= len(document):
        raise ValueError(f"source segment page out of range: {segment.page_number}")

    page = document[page_index]
    page_rect = page.rect
    x_norm, y_norm, _w_norm, _h_norm = segment.bbox_norm
    x0 = x_norm * page_rect.width
    y0 = y_norm * page_rect.height
    x = max(12.0, x0 - 28.0)
    y = min(max(12.0, y0 + 4.0), page_rect.height - 24.0)

    annot = page.add_text_annot(fitz.Point(x, y), comment, icon="Comment")
    annot.set_info(
        subject=subject,
        content=comment,
    )
    annot.update()

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import fitz

from opengrader.core.source_location import QuestionSourceSegment


_ANNOTATION_SUBJECT_PREFIX = "cm:"
_TOTAL_SCORE_SUBJECT = "og:total-score"
_ANNOTATION_HASH_KEY = b"opengrader-pdf-comment-v1"


def sync_total_score_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    total_points: float,
    possible_points: float,
) -> None:
    """Synchronize the total score summary onto the first page of the PDF."""
    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")

    score_text = f"Total Score: {total_points:g} / {possible_points:g}"

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    with fitz.open(annotated_pdf_path) as document:
        _remove_existing_question_annotations(document, subject=_TOTAL_SCORE_SUBJECT)

        # Always add the total score to the first page (index 0)
        if len(document) > 0:
            page = document[0]
            # Position the annotation at the top right of the page
            # page.rect.width - 150 provides a consistent right-aligned feel.
            rect = fitz.Rect(page.rect.width - 160, 20, page.rect.width - 10, 50)
            annot = page.add_freetext_annot(
                rect,
                score_text,
                fontsize=14,
                text_color=(0.8, 0, 0),  # Dark Red
            )
            annot.set_info(subject=_TOTAL_SCORE_SUBJECT, content=score_text)
            annot.update()

        document.saveIncr()


def sync_question_comment_annotation(
    *,
    source_pdf_path: Path,
    annotated_pdf_path: Path,
    question_id: str,
    comment: str,
    points_awarded: float | None = None,
    possible_points: float | None = None,
    source_segments: tuple[QuestionSourceSegment, ...],
) -> None:
    """Synchronize one question comment into an annotated PDF copy."""
    if not source_pdf_path.exists():
        raise FileNotFoundError(f"source PDF not found: {source_pdf_path}")
    if not source_segments:
        raise ValueError(
            "Question source metadata missing; regenerate submission files to enable PDF annotations."
        )

    # Build the annotation text: Score followed by comment if present
    text_parts = []
    if points_awarded is not None:
        score_str = f"Score: {points_awarded:g}"
        if possible_points is not None:
            score_str += f"/{possible_points:g}"
        text_parts.append(score_str)

    if comment.strip():
        text_parts.append(comment.strip())

    full_text = "\n\n".join(text_parts)

    if not full_text and not annotated_pdf_path.exists():
        return

    annotated_pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if not annotated_pdf_path.exists():
        shutil.copy2(source_pdf_path, annotated_pdf_path)

    subject = _comment_subject(question_id)
    with fitz.open(annotated_pdf_path) as document:
        _remove_existing_question_annotations(document, subject=subject)
        if full_text:
            _add_comment_annotation(
                document,
                segment=source_segments[0],
                subject=subject,
                comment=full_text,
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

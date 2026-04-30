from pathlib import Path

import fitz

from opengrader.core.pdf.annotations import sync_question_comment_annotation
from opengrader.core.source_location import QuestionSourceSegment


def _create_pdf(path: Path) -> None:
    doc = fitz.open()
    doc.new_page(width=300, height=400)
    doc.save(path)
    doc.close()


def _opengrader_comments(path: Path) -> list[dict[str, str]]:
    with fitz.open(path) as document:
        comments: list[dict[str, str]] = []
        for page in document:
            annot = page.first_annot
            while annot is not None:
                info = dict(annot.info or {})
                if info.get("content"):
                    comments.append(info)
                annot = annot.next
        return comments


def test_sync_question_comment_annotation_creates_and_replaces_note(
    tmp_path: Path,
) -> None:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "annotated" / "submission.annotated.pdf"
    _create_pdf(source)

    segment = QuestionSourceSegment(page_number=1, bbox_norm=(0.2, 0.3, 0.5, 0.2))
    sync_question_comment_annotation(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        question_id="q1",
        comment="Show your conversion steps.",
        source_segments=(segment,),
    )
    sync_question_comment_annotation(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        question_id="q1",
        comment="Use repeated division.",
        source_segments=(segment,),
    )

    comments = _opengrader_comments(annotated)
    assert len(comments) == 1
    assert comments[0].get("title", "") == ""
    assert comments[0]["subject"].startswith("cm:")
    assert "opengrader" not in comments[0]["subject"].lower()
    assert "q1" not in comments[0]["subject"].lower()
    assert comments[0]["content"] == "Use repeated division."


def test_sync_question_comment_annotation_clears_empty_comment(tmp_path: Path) -> None:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)
    segment = QuestionSourceSegment(page_number=1, bbox_norm=(0.2, 0.3, 0.5, 0.2))

    sync_question_comment_annotation(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        question_id="q1",
        comment="Needs units.",
        source_segments=(segment,),
    )
    sync_question_comment_annotation(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        question_id="q1",
        comment=" ",
        source_segments=(segment,),
    )

    assert _opengrader_comments(annotated) == []

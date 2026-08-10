from pathlib import Path

import fitz

from opengrader.core.pdf.annotations import (
    FeedbackQuestionSummary,
    FeedbackSummary,
    sync_feedback_summary_page,
    sync_question_comment_annotation,
    sync_total_score_annotation,
)
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


def _summary_page_count(path: Path) -> int:
    with fitz.open(path) as document:
        count = 0
        for page in document:
            annot = page.first_annot
            while annot is not None:
                if (annot.info or {}).get("subject") == "og:feedback-summary":
                    count += 1
                    break
                annot = annot.next
        return count


def _last_page_text(path: Path) -> str:
    with fitz.open(path) as document:
        return document[-1].get_text()


def _sample_summary() -> FeedbackSummary:
    return FeedbackSummary(
        title="Quiz 1 (A)",
        student_label="Tommy Liu · ID: s1",
        total_awarded=8.0,
        total_possible=20.0,
        questions=(
            FeedbackQuestionSummary(
                label="Question 1",
                possible_points=10.0,
                points_awarded=8.0,
                rubric_labels=("Partial credit (+8)",),
                comment="Nice work, watch the units.",
            ),
            FeedbackQuestionSummary(
                label="Question 2",
                possible_points=10.0,
                points_awarded=None,
            ),
        ),
    )


def test_sync_feedback_summary_page_appends_summary(tmp_path: Path) -> None:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "annotated" / "submission.annotated.pdf"
    _create_pdf(source)

    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),
    )

    with fitz.open(annotated) as document:
        assert len(document) == 2  # original page + one summary page
    assert _summary_page_count(annotated) == 1

    text = _last_page_text(annotated)
    assert "Grade Summary" in text
    assert "Tommy Liu" in text
    assert "Total: 8 / 20" in text
    assert "Question 1" in text
    assert "Partial credit (+8)" in text
    assert "not yet graded" in text  # ungraded Question 2 shown explicitly


def test_sync_feedback_summary_page_is_idempotent(tmp_path: Path) -> None:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),
    )
    # A second save (e.g. after grading Question 2) must replace, not stack.
    updated = FeedbackSummary(
        title="Quiz 1 (A)",
        student_label="Tommy Liu · ID: s1",
        total_awarded=18.0,
        total_possible=20.0,
        questions=(
            FeedbackQuestionSummary(
                label="Question 1",
                possible_points=10.0,
                points_awarded=8.0,
            ),
            FeedbackQuestionSummary(
                label="Question 2",
                possible_points=10.0,
                points_awarded=10.0,
            ),
        ),
    )
    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=updated,
    )

    with fitz.open(annotated) as document:
        assert len(document) == 2  # still exactly one summary page
    assert _summary_page_count(annotated) == 1
    text = _last_page_text(annotated)
    assert "Total: 18 / 20" in text
    assert "not yet graded" not in text


# --- Unicode on the returned page ---------------------------------------
#
# The summary page prints the student's real name. Before these tests it was
# drawn with a base-14 font, whose WinAnsi encoding silently replaced every
# character outside Latin-1 with a dot — so a Cyrillic or Han name was mangled
# on the one artifact the student actually receives. Each case below asserts the
# name survives a write/read round trip.


def _summary_for(student_label: str, *, comment: str = "Good work.") -> FeedbackSummary:
    return FeedbackSummary(
        title="Quiz 1 (A)",
        student_label=student_label,
        total_awarded=9.0,
        total_possible=10.0,
        questions=(
            FeedbackQuestionSummary(
                label="Question 1",
                possible_points=10.0,
                points_awarded=9.0,
                comment=comment,
            ),
        ),
    )


def _summary_text_for(tmp_path: Path, summary: FeedbackSummary) -> str:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)
    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=summary,
    )
    return _last_page_text(annotated)


def test_summary_page_renders_accented_latin_name(tmp_path: Path) -> None:
    text = _summary_text_for(tmp_path, _summary_for("José García · ID: s1"))
    assert "José García" in text


def test_summary_page_renders_cyrillic_name(tmp_path: Path) -> None:
    text = _summary_text_for(tmp_path, _summary_for("Дмитрий Иванов · ID: s2"))
    assert "Дмитрий Иванов" in text


def test_summary_page_renders_vietnamese_name(tmp_path: Path) -> None:
    # Vietnamese tone marks sit in Latin Extended Additional, outside Latin-1.
    text = _summary_text_for(tmp_path, _summary_for("Nguyễn Văn A · ID: s3"))
    assert "Nguyễn Văn A" in text


def test_summary_page_renders_han_name(tmp_path: Path) -> None:
    # Han characters need the CJK fallback font, which covers Latin too — so the
    # surrounding "ID: s4" has to survive on the same line.
    text = _summary_text_for(tmp_path, _summary_for("李明 · ID: s4"))
    assert "李明" in text
    assert "ID: s4" in text


def test_summary_page_renders_unicode_in_title_and_comment(tmp_path: Path) -> None:
    summary = _summary_for("Ada Lovelace", comment="Très bien — 很好, keep going")
    text = _summary_text_for(tmp_path, summary)
    assert "Très bien — 很好, keep going" in text


def test_summary_page_marks_unrenderable_characters_visibly(tmp_path: Path) -> None:
    # No bundled font covers Arabic. The requirement is only that the gap is
    # *visible*: a dropped name reads as a bug, a row of "?" reads as a limit.
    text = _summary_text_for(tmp_path, _summary_for("عبد الله"))
    assert "?" in text
    assert "Grade Summary" in text  # the rest of the page is unaffected


def test_repeated_syncing_does_not_grow_the_annotated_pdf(tmp_path: Path) -> None:
    """The annotated scan is what the student is handed; it must not bloat.

    The two annotation writers use ``saveIncr``, which appends a revision
    rather than rewriting, so this full save is the only point that can discard
    the old ones. Without compaction the file grew on *every* re-sync — even
    when nothing changed — and ordinary grading re-syncs constantly.
    """
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    def sync() -> None:
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=_sample_summary(),
        )

    sync()
    after_first = annotated.stat().st_size
    for _ in range(20):
        sync()
    after_many = annotated.stat().st_size

    # Allow a little slack for object-ordering noise, but nothing like the
    # unbounded ~1.25 MB-per-sync growth this replaced.
    assert after_many <= after_first * 1.1, (
        f"annotated PDF grew from {after_first} to {after_many} bytes "
        "across 20 no-op syncs"
    )
    # ...and it is still a correct, single-summary document.
    assert _summary_page_count(annotated) == 1
    assert "Grade Summary" in _last_page_text(annotated)


# --- Skipping work that would change nothing ------------------------------
#
# Grading walks question by question and re-syncs the PDF on every save, so the
# common case is re-rendering a page identical to the one already there and
# rewriting the whole document to store it. The hidden marker carries a digest
# of the summary it was drawn from, and the two annotation writers report
# whether they actually wrote, so an unchanged save touches no PDF at all.


def _mtime_ns(path: Path) -> int:
    return path.stat().st_mtime_ns


def test_unchanged_summary_is_not_rewritten(tmp_path: Path) -> None:
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    assert (
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=_sample_summary(),
        )
        is True
    )
    before = annotated.read_bytes()

    assert (
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=_sample_summary(),
        )
        is False
    )
    assert annotated.read_bytes() == before  # byte-identical: nothing was written


def test_changed_summary_is_rewritten(tmp_path: Path) -> None:
    """The skip must key on content, not on "have I run before"."""
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),
    )
    graded = FeedbackSummary(
        title="Quiz 1 (A)",
        student_label="Tommy Liu · ID: s1",
        total_awarded=18.0,
        total_possible=20.0,
        questions=(
            FeedbackQuestionSummary(
                label="Question 1",
                possible_points=10.0,
                points_awarded=8.0,
                rubric_labels=("Partial credit (+8)",),
                comment="Nice work, watch the units.",
            ),
            FeedbackQuestionSummary(
                label="Question 2",
                possible_points=10.0,
                points_awarded=10.0,
            ),
        ),
    )
    assert (
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=graded,
        )
        is True
    )
    assert _summary_page_count(annotated) == 1
    assert "Total: 18 / 20" in _last_page_text(annotated)


def test_summary_skip_ignores_equivalent_number_formatting(tmp_path: Path) -> None:
    """8 and 8.0 draw the same page, so they must not force a rewrite."""
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    integral = FeedbackSummary(
        title="Quiz 1 (A)",
        student_label="Tommy Liu · ID: s1",
        total_awarded=8,
        total_possible=20,
        questions=(
            FeedbackQuestionSummary(
                label="Question 1",
                possible_points=10,
                points_awarded=8,
                rubric_labels=("Partial credit (+8)",),
                comment="Nice work, watch the units.",
            ),
            FeedbackQuestionSummary(
                label="Question 2",
                possible_points=10,
                points_awarded=None,
            ),
        ),
    )
    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),  # the same numbers as floats
    )
    assert (
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=integral,
        )
        is False
    )


def test_summary_digest_is_opaque_in_the_returned_pdf(tmp_path: Path) -> None:
    """A student can read annotation metadata; the digest must leak nothing."""
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),
    )

    with fitz.open(annotated) as document:
        markers = [
            dict(annot.info or {})
            for page in document
            for annot in page.annots()
            if (annot.info or {}).get("subject") == "og:feedback-summary"
        ]
    assert len(markers) == 1
    content = markers[0]["content"]
    # A 16-byte keyed digest and nothing else — no name, score or feedback text.
    assert len(content) == 32
    assert all(char in "0123456789abcdef" for char in content)


def test_unchanged_annotations_are_not_rewritten(tmp_path: Path) -> None:
    """The two ``saveIncr`` writers append a revision; an unchanged one must not."""
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)
    segment = QuestionSourceSegment(page_number=1, bbox_norm=(0.2, 0.3, 0.5, 0.2))

    def sync_comment(comment: str) -> bool:
        return sync_question_comment_annotation(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            question_id="q1",
            comment=comment,
            points_awarded=8.0,
            possible_points=10.0,
            source_segments=(segment,),
        )

    def sync_total(points: float) -> bool:
        return sync_total_score_annotation(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            total_points=points,
            possible_points=10.0,
        )

    assert sync_comment("Watch the units.") is True
    assert sync_total(8.0) is True
    before = annotated.read_bytes()

    assert sync_comment("Watch the units.") is False
    assert sync_total(8.0) is False
    assert annotated.read_bytes() == before

    assert sync_comment("Watch the units, and show your work.") is True
    assert sync_total(9.0) is True
    assert annotated.read_bytes() != before


def test_full_grade_save_does_not_grow_the_annotated_pdf(tmp_path: Path) -> None:
    """The whole save path, not just the summary, must stay flat when re-run.

    The regression this guards is subtle: skipping the summary rewrite also
    skips the ``garbage=4`` compaction, which is the *only* thing that discards
    the revisions ``saveIncr`` appends. If an annotation write ever happens
    without the summary save that follows it, the file grows unbounded again —
    exactly the 1.25 MB-per-sync bug, re-entered through the optimisation meant
    to make it cheaper.
    """
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)
    segment = QuestionSourceSegment(page_number=1, bbox_norm=(0.2, 0.3, 0.5, 0.2))

    def full_save(comment: str) -> None:
        """One grade save: total, then the note, then the summary."""
        wrote = sync_total_score_annotation(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            total_points=8.0,
            possible_points=20.0,
        )
        wrote |= sync_question_comment_annotation(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            question_id="q1",
            comment=comment,
            points_awarded=8.0,
            possible_points=10.0,
            source_segments=(segment,),
        )
        sync_feedback_summary_page(
            source_pdf_path=source,
            annotated_pdf_path=annotated,
            summary=_sample_summary(),
            force=wrote,
        )

    full_save("Nice work, watch the units.")
    after_first = annotated.stat().st_size

    # Alternate: saves that change the note (annotations write, summary digest
    # is unchanged because `_sample_summary` is fixed — the exact case where a
    # naive skip would leak revisions) and saves that change nothing at all.
    for index in range(20):
        full_save(f"Nice work, watch the units. ({index})")
        full_save(f"Nice work, watch the units. ({index})")

    assert annotated.stat().st_size <= after_first * 1.1, (
        f"annotated PDF grew from {after_first} to {annotated.stat().st_size} "
        "bytes across 40 grade saves"
    )
    assert _summary_page_count(annotated) == 1


def test_compaction_preserves_existing_comment_annotations(tmp_path: Path) -> None:
    """Garbage collection must not drop the grader's own annotations.

    They are live objects on live pages, so they survive — but the summary
    save is the one place that rewrites the whole document, and silently
    losing a comment there would be invisible until a student asked.
    """
    source = tmp_path / "submission.pdf"
    annotated = tmp_path / "submission.annotated.pdf"
    _create_pdf(source)

    sync_question_comment_annotation(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        question_id="q1",
        comment="Watch the units.",
        points_awarded=8.0,
        possible_points=10.0,
        source_segments=[QuestionSourceSegment(page_number=1, bbox_norm=(0.1, 0.1, 0.5, 0.2))],
    )
    assert any(
        "Watch the units." in (info.get("content") or "")
        for info in _opengrader_comments(annotated)
    )

    sync_feedback_summary_page(
        source_pdf_path=source,
        annotated_pdf_path=annotated,
        summary=_sample_summary(),
    )

    assert any(
        "Watch the units." in (info.get("content") or "")
        for info in _opengrader_comments(annotated)
    )

from pathlib import Path

from opengrader.core.submissions.extra_pages import (
    read_extra_pages_for,
    read_extra_pages_map,
    resolve_extra_pages_path,
    write_extra_pages_map,
)


def test_map_round_trips(tmp_path: Path) -> None:
    write_extra_pages_map(tmp_path, {"submission-0001": (4, 5), "submission-0002": ()})
    assert read_extra_pages_map(tmp_path) == {
        "submission-0001": (4, 5),
        "submission-0002": (),
    }


def test_map_lives_in_the_dot_dir(tmp_path: Path) -> None:
    path = write_extra_pages_map(tmp_path, {"a": (2,)})
    assert path == resolve_extra_pages_path(tmp_path)
    assert path.parent.name == ".opengrader"


def test_pages_are_deduplicated_sorted_and_range_checked(tmp_path: Path) -> None:
    write_extra_pages_map(tmp_path, {"a": [5, 2, 5, 0, -1]})
    assert read_extra_pages_map(tmp_path) == {"a": (2, 5)}


def test_missing_file_reads_as_empty(tmp_path: Path) -> None:
    assert read_extra_pages_map(tmp_path) == {}


def test_malformed_file_degrades_to_empty(tmp_path: Path) -> None:
    path = resolve_extra_pages_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("{not json")
    assert read_extra_pages_map(tmp_path) == {}


def test_unrecorded_pdf_is_none_but_a_recorded_empty_list_is_authoritative(
    tmp_path: Path,
) -> None:
    write_extra_pages_map(tmp_path, {"checked": ()})
    assert read_extra_pages_for(tmp_path / "checked.pdf") == ()
    assert read_extra_pages_for(tmp_path / "never-split.pdf") is None


def test_lookup_is_by_stem_so_the_folder_can_move(tmp_path: Path) -> None:
    write_extra_pages_map(tmp_path, {"submission-0007": (3,)})
    assert read_extra_pages_for(tmp_path / "submission-0007.pdf") == (3,)


import fitz  # noqa: E402

from opengrader.core.source_location import QuestionSourceSegment  # noqa: E402
from opengrader.core.submissions.processor import _apply_extra_pages  # noqa: E402
from opengrader.core.submissions.student_file import (  # noqa: E402
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _scan(tmp_path: Path, pages: int) -> Path:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page(width=300, height=400)
    path = tmp_path / "student.pdf"
    doc.save(str(path))
    doc.close()
    return path


def _document(*covered: int) -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="Ann Lee",
        student_id="1",
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="student.pdf",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="",
                possible_points=None,
                response_image_path=None,
                response_text="",
                source_segments=tuple(
                    QuestionSourceSegment(page_number=page, bbox_norm=(0.1, 0.1, 0.5, 0.2))
                    for page in covered
                ),
            )
        ],
    )


def test_explicit_pages_win(tmp_path: Path) -> None:
    pdf = _scan(tmp_path, 3)
    assert _apply_extra_pages(_document(1), pdf, (2,)).extra_pages == (2,)


def test_sidecar_is_used_when_no_explicit_pages(tmp_path: Path) -> None:
    pdf = _scan(tmp_path, 3)
    write_extra_pages_map(tmp_path, {"student": (3,)})
    assert _apply_extra_pages(_document(1), pdf, None).extra_pages == (3,)


def test_recorded_empty_sidecar_suppresses_inference(tmp_path: Path) -> None:
    pdf = _scan(tmp_path, 3)
    write_extra_pages_map(tmp_path, {"student": ()})
    assert _apply_extra_pages(_document(1), pdf, None).extra_pages == ()


def test_uncovered_pages_are_inferred_without_a_sidecar(tmp_path: Path) -> None:
    pdf = _scan(tmp_path, 3)
    assert _apply_extra_pages(_document(1), pdf, None).extra_pages == (2, 3)


def test_fully_covered_scan_is_left_untouched(tmp_path: Path) -> None:
    pdf = _scan(tmp_path, 2)
    document = _document(1, 2)
    assert _apply_extra_pages(document, pdf, None) is document


def test_unreadable_pdf_infers_nothing(tmp_path: Path) -> None:
    assert _apply_extra_pages(_document(1), tmp_path / "gone.pdf", None).extra_pages == ()

from pathlib import Path

import pytest

from opengrader.core.submissions.student_file import load_student_submission_markdown

SCAN = Path(__file__).resolve().parents[4] / "samples" / "cmpe142-midterm2" / "submissions.pdf"


@pytest.fixture(scope="module")
def extracted(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, Path]:
    pytest.importorskip("rapidocr")
    from opengrader.cli import submission_extractor, submission_splitter

    root = tmp_path_factory.mktemp("cmpe142")
    split, out = root / "split", root / "out"
    submission_splitter.run_cli(["--input", str(SCAN), "--output", str(split), "--fix-rotation", "--fix-order"],
                                status_callback=lambda _m: None)
    submission_extractor.run_cli(["--input", str(split), "--output", str(out), "--jobs", "1",
                                  "--ollama-host", "http://127.0.0.1:9"])
    return split, out


def test_each_student_gets_their_own_two_pages(extracted):
    import fitz

    split, _ = extracted

    assert [(p.name, len(fitz.open(p))) for p in sorted(split.glob("*.pdf"))] == [("submission-0001.pdf", 2), ("submission-0002.pdf", 2)]


def test_every_question_is_cropped_and_the_page_break_answer_spans_both_pages(extracted):
    _, out = extracted

    for name in ("submission-0001", "submission-0002"):
        document = load_student_submission_markdown(out / name / f"{name}.submission.md")
        questions = {q.question_id: q for q in document.questions}
        assert sorted(questions, key=lambda k: int(k[1:])) == [f"q{n}" for n in range(1, 9)]
        assert all((out / name / q.response_image_path).exists() for q in questions.values())
        assert [s.page_number for s in questions["q4"].source_segments] == [1, 2]
        assert questions["q2"].possible_points == 1 and questions["q1"].possible_points == 50
        assert (out / name / document.name_image_path).exists() and (out / name / document.id_image_path).exists()
        assert (document.course_code, document.assignment_title, document.assignment_version) == ("CMPE142", "Exam 2", "B")
        assert not document.scan_warnings

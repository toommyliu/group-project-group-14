"""Joining two submissions after they were read: a six-page paper split 5 + 1
whose pieces may already carry grades."""

from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from opengrader.cli import submission_extractor
from opengrader.core.history import read_journal
from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.submissions.manifest import ExtractionRecord, append_record
from opengrader.core.submissions.merge_split import (
    MergeUnavailable,
    pending_carried,
    merge_in_folder,
    neighbours,
    pieces,
    record_extract_command,
)

QUESTION = """- {qid}:
  - Question type: Free Response
  - Prompt: (10 points) Question {qid}
{crop}  - Response text: {text}
  - Possible points: 10
  - Points:
  - Rubric:
  - Analysis:
"""
CROP = """  - Response image: ![{qid} response](responses/{qid}.png)
  - Source segments: [{{"page":{page},"bbox":[0.1,0.1,0.8,0.2]}}]
"""


def _submission(path: Path, answers: dict[str, int | None]) -> None:
    """`answers` maps each question to the page its answer is on, or None
    when this piece does not hold that page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [
        QUESTION.format(
            qid=qid,
            crop="" if page is None else CROP.format(qid=qid, page=page),
            text="" if page is None else f"answer {qid}",
        )
        for qid, page in answers.items()
    ]
    path.write_text("---\nstudent_name: \"Ada\"\nstudent_id: \"1\"\nsource_pdf: \"x.pdf\"\n---\n\n" + "\n".join(blocks))


def _grade(path: Path, qid: str, points: float, label: str, analysis: str = "") -> None:
    update_student_submission_file(
        path,
        StudentSubmissionGradeUpdate(
            question_id=qid, possible_points=10, points_awarded=points,
            rubric_labels=(label,), analysis=analysis,
        ),
    )


def _pdf(path: Path, labels: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for label in labels:
        doc.new_page().insert_text((72, 72), label)
    doc.save(str(path))
    doc.close()
    path.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": f"g{n}", "page_number": n} for n in range(1, len(labels) + 1)],
    }))


@pytest.fixture
def cut(tmp_path):
    """A class folder after the pipeline: q1, q2 on pages 1–5 of the first
    piece, q3 on the page that became the second piece, a third student fine."""
    split, subs = tmp_path / "split", tmp_path / "submissions"
    _pdf(split / "submission-0001.pdf", ["A1", "A2", "A3", "A4", "A5"])
    _pdf(split / "submission-0002.pdf", ["A6"])
    _pdf(split / "submission-0003.pdf", ["B1", "B2", "B3", "B4", "B5", "B6"])
    for stem, answers in (
        ("submission-0001", {"q1": 1, "q2": 5, "q3": None}),
        ("submission-0002", {"q1": None, "q2": None, "q3": 1}),
        ("submission-0003", {"q1": 1, "q2": 5, "q3": 6}),
    ):
        md = subs / stem / f"{stem}.submission.md"
        _submission(md, answers)
        append_record(subs, ExtractionRecord(
            key=stem, source=str(split / f"{stem}.pdf"), source_sha256="x", settings_id="s",
            outputs=(f"{stem}/{stem}.submission.md",),
        ))
    record_extract_command(subs, split, ["--input", str(split), "--output", str(subs), "--resume"])
    return tmp_path


@pytest.fixture
def reread(monkeypatch):
    """The extractor, reading the joined PDF: every answer found, no grades."""
    calls: list[list[str]] = []

    def run(argv, *, status_callback=None, prompt_cloud_send=True, **_kwargs):
        calls.append(list(argv))
        if "--adopt-only" in argv:
            return
        subs = Path(argv[argv.index("--output") + 1])
        _submission(subs / "submission-0001" / "submission-0001.submission.md", {"q1": 1, "q2": 5, "q3": 6})

    monkeypatch.setattr(submission_extractor, "run_cli", run)
    return calls


def _pieces(root: Path):
    found = {piece.stem: piece for piece in pieces(root / "submissions")}
    return found["submission-0001"], found["submission-0002"]


def test_neighbours_are_the_pieces_scanned_either_side(cut):
    before, after = neighbours(cut / "submissions", "submission-0002")
    assert (before.stem, after.stem) == ("submission-0001", "submission-0003")
    assert neighbours(cut / "submissions", "submission-0001")[0] is None


def test_joining_carries_only_grades_given_while_looking_at_the_answer(cut, reread):
    subs = cut / "submissions"
    first, second = _pieces(cut)
    _grade(first.submission, "q1", 10, "Correct", "Nice.")
    # q3 was marked wrong in the first piece because its page was not there…
    _grade(first.submission, "q3", 0, "Incorrect", "No answer.")
    # …and graded properly in the one-page piece that held it.
    _grade(second.submission, "q3", 6, "Partially Correct")

    result = merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history", actor="ta")
    path = result.path

    assert result.grades == 2
    graded = {q.question_id: q for q in load_student_submission_markdown(path).questions}
    assert (graded["q1"].points_awarded, graded["q1"].rubric_labels, graded["q1"].analysis) == (10, ("Correct",), "Nice.")
    assert (graded["q3"].points_awarded, graded["q3"].rubric_labels) == (6, ("Partially Correct",))
    assert graded["q2"].points_awarded is None
    assert not (subs / "submission-0002").exists()
    assert not (cut / "split" / "submission-0002.pdf").exists()
    with fitz.open(str(cut / "split" / "submission-0001.pdf")) as doc:
        assert len(doc) == 6
    assert pending_carried(subs, "submission-0001") is None
    # Every other submission re-recorded first, then only the joined PDF read.
    assert ["--adopt-only" in argv for argv in reread] == [True, False]
    assert reread[1][reread[1].index("--input") + 1].endswith("submission-0001.pdf")
    entries = read_journal(subs / ".opengrader" / "history")
    assert {(e.question_id, e.actor) for e in entries if e.kind == "grade"} >= {("q1", "ta"), ("q3", "ta")}


def test_no_recorded_command_changes_nothing(cut, reread):
    subs = cut / "submissions"
    (subs / ".opengrader" / "extract-command.json").unlink()
    first, second = _pieces(cut)

    with pytest.raises(MergeUnavailable, match="Run the pipeline on this folder once more"):
        merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history")

    assert (cut / "split" / "submission-0002.pdf").exists()
    assert (subs / "submission-0002").exists()
    assert reread == []


def test_a_join_interrupted_while_reading_finishes_on_the_second_try(cut, monkeypatch):
    subs = cut / "submissions"
    first, second = _pieces(cut)
    _grade(second.submission, "q3", 6, "Partially Correct")

    def broken(argv, **_kwargs):
        if "--adopt-only" not in argv:
            raise RuntimeError("Tesseract is not installed")

    monkeypatch.setattr(submission_extractor, "run_cli", broken)
    with pytest.raises(RuntimeError):
        merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history")
    # The PDFs are joined, the second piece is still there to be asked about,
    # and its grade is noted on disk.
    assert not (cut / "split" / "submission-0002.pdf").exists()
    first, second = _pieces(cut)
    assert second.joined
    assert "q3" in pending_carried(subs, "submission-0001").grades

    def run(argv, **_kwargs):
        if "--adopt-only" in argv:
            return
        _submission(subs / "submission-0001" / "submission-0001.submission.md", {"q1": 1, "q2": 5, "q3": 6})

    monkeypatch.setattr(submission_extractor, "run_cli", run)
    result = merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history")
    path = result.path

    assert result.grades == 1
    graded = {q.question_id: q for q in load_student_submission_markdown(path).questions}
    assert graded["q3"].points_awarded == 6
    assert not (subs / "submission-0002").exists()


def test_only_readings_a_person_corrected_are_carried(cut, reread):
    from opengrader.core.submissions import SOURCE_HUMAN

    subs = cut / "submissions"
    first, second = _pieces(cut)
    # A person fixed the OCR of q3 on the one-page piece that held it…
    update_student_submission_file(second.submission, StudentSubmissionGradeUpdate(
        question_id="q3", response_text="x = 42", response_confidence=100, response_source=SOURCE_HUMAN,
    ))
    # …and q2's machine reading in the first piece is left as the machine read it.
    result = merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history")

    assert result.readings == 1
    read = {q.question_id: q for q in load_student_submission_markdown(result.path).questions}
    assert (read["q3"].response_text, read["q3"].response_source) == ("x = 42", SOURCE_HUMAN)
    # The fresh reading of the joined paper, not either piece's.
    assert read["q2"].response_text == "answer q2"


def test_a_cloud_yes_reaches_only_the_reading(cut, reread):
    subs = cut / "submissions"
    first, second = _pieces(cut)

    merge_in_folder(subs, first, second, history_dir=subs / ".opengrader" / "history", send_to_cloud_confirmed=True)

    adopt, joined = reread
    assert "--yes-send-to-cloud" not in adopt and "--yes-send-to-cloud" in joined

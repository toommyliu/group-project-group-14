from __future__ import annotations

import json
from pathlib import Path

import fitz
import pytest

from opengrader.cli import merge_submissions, run_command, submission_extractor
from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.submissions.manifest import ExtractionRecord, append_record
from opengrader.core.submissions.merge_split import record_extract_command


def _submission(path: Path, answers: dict[str, int | None]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = []
    for qid, page in answers.items():
        crop = "" if page is None else (
            f"  - Response image: ![{qid}](responses/{qid}.png)\n"
            f"  - Source segments: [{{\"page\":{page},\"bbox\":[0.1,0.1,0.8,0.2]}}]\n"
        )
        blocks.append(
            f"- {qid}:\n  - Question type: Free Response\n  - Prompt: (10 points) {qid}\n{crop}"
            "  - Response text:\n  - Possible points: 10\n  - Points:\n  - Rubric:\n  - Analysis:\n"
        )
    path.write_text("---\nstudent_name: \"\"\nstudent_id: \"\"\nsource_pdf: \"x.pdf\"\n---\n\n" + "\n".join(blocks))


@pytest.fixture
def folder(tmp_path, monkeypatch):
    split, subs = tmp_path / "split", tmp_path / "submissions"
    for stem, count, answers in (
        ("submission-0001", 5, {"q1": 1, "q2": None}),
        ("submission-0002", 1, {"q1": None, "q2": 1}),
        ("submission-0003", 6, {"q1": 1, "q2": 6}),
    ):
        split.mkdir(exist_ok=True)
        doc = fitz.open()
        for _ in range(count):
            doc.new_page()
        doc.save(str(split / f"{stem}.pdf"))
        doc.close()
        (split / f"{stem}{SIDECAR_SUFFIX}").write_text(json.dumps(
            {"pages": [{"group": f"g{n}", "page_number": n} for n in range(1, count + 1)]}
        ))
        _submission(subs / stem / f"{stem}.submission.md", answers)
        append_record(subs, ExtractionRecord(
            key=stem, source=str(split / f"{stem}.pdf"), source_sha256="x", settings_id="s",
            outputs=(f"{stem}/{stem}.submission.md",),
        ))
    record_extract_command(subs, split, ["--input", str(split), "--output", str(subs), "--resume"])
    update_student_submission_file(
        subs / "submission-0002" / "submission-0002.submission.md",
        StudentSubmissionGradeUpdate(question_id="q2", possible_points=10, points_awarded=7),
    )
    monkeypatch.setattr(
        submission_extractor,
        "run_cli",
        lambda argv, **_k: None if "--adopt-only" in argv else _submission(
            subs / "submission-0001" / "submission-0001.submission.md", {"q1": 1, "q2": 6}
        ),
    )
    return tmp_path


def test_joins_a_class_folder_and_says_what_it_kept(folder, capsys) -> None:
    run_command(merge_submissions.command, ["submission-0001", "submission-0002", "--folder", str(folder)])

    out = capsys.readouterr().out
    assert "Joined submission-0002 onto submission-0001" in out
    assert "1 grade(s) and 0 corrected reading(s) carried over." in out
    graded = {q.question_id: q for q in load_student_submission_markdown(
        folder / "submissions" / "submission-0001" / "submission-0001.submission.md").questions}
    assert graded["q2"].points_awarded == 7


def test_names_given_backwards_are_put_in_scan_order(folder) -> None:
    run_command(merge_submissions.command, ["submission-0002.pdf", "submission-0001", "--folder", str(folder)])

    assert not (folder / "split" / "submission-0002.pdf").exists()


def test_submissions_that_are_not_neighbours_are_refused(folder, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_command(merge_submissions.command, ["submission-0001", "submission-0003", "--folder", str(folder)])

    assert exit_info.value.code == 2
    assert "not scanned one after the other" in capsys.readouterr().err
    assert (folder / "split" / "submission-0002.pdf").exists()


def test_a_published_folder_is_joined_through_the_workspace(folder, monkeypatch, capsys) -> None:
    from opengrader.server.workspace import client, merge
    from opengrader.core.submissions.merge_split import pieces

    by_stem = {piece.stem: piece for piece in pieces(folder / "submissions")}
    calls: list = []
    monkeypatch.setattr(merge_submissions, "_workspace_assignment", lambda _subs: (folder, "a1"))
    monkeypatch.setattr(merge, "workspace_pieces", lambda _root, _aid: (folder, {11: by_stem["submission-0001"], 12: by_stem["submission-0002"]}))
    monkeypatch.setattr(client, "ensure_service", lambda _root: "connection")
    monkeypatch.setattr(client, "WorkspaceClient", lambda connection: connection)
    monkeypatch.setattr(merge, "merge_in_workspace", lambda c, aid, a, b, **_k: calls.append((c, aid, a, b)) or (20, merge.JoinResult(folder, 1, 0)))

    run_command(merge_submissions.command, ["submission-0001", "submission-0002", "--folder", str(folder)])

    assert calls == [("connection", "a1", 11, 12)]
    assert "now submission 20 in the workspace" in capsys.readouterr().out

"""`X` on the grade screen: join a paper the splitter cut in two, read it
again, and keep the grades given while its answers were on screen."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import fitz
from PIL import Image

from opengrader.cli import submission_extractor
from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.core.submissions.manifest import ExtractionRecord, append_record
from opengrader.core.submissions.merge_split import record_extract_command
from opengrader.tui.state import PAGE_GRADE
from opengrader.tui.tui import TuiApp

from .fakes import DEFAULT_IMAGE_PATH

RUBRIC = """  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
"""
QA = "---\ntitle: Split quiz\n---\n" + "".join(
    f"- (10 points) Question {n}.\n  - **Answer**\n    - {n}\n{RUBRIC}" for n in (1, 2, 3)
)


def _submission(folder: Path, stem: str, pages: dict[str, int | None], graded: dict[str, str] | None = None) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    blocks = []
    for n, (qid, page) in enumerate(pages.items(), start=1):
        crop = ""
        if page is not None:
            (folder / "responses").mkdir(exist_ok=True)
            Image.new("RGB", (40, 20), "white").save(folder / "responses" / f"{qid}.png")
            crop = (
                f"  - Response image: ![{qid} response](responses/{qid}.png)\n"
                f"  - Source segments: [{{\"page\":{page},\"bbox\":[0.1,0.1,0.8,0.2]}}]\n"
            )
        blocks.append(
            f"- {qid}:\n  - Question type: Free Response\n  - Prompt: (10 points) Question {n}.\n"
            f"{crop}  - Response text: {n if page else ''}\n  - Possible points: 10\n"
            f"  - Points:\n  - Rubric:\n  - Analysis:\n"
        )
    path = folder / f"{stem}.submission.md"
    path.write_text(
        f"---\nstudent_name: \"\"\nstudent_id: \"\"\nsource_pdf: \"../../split/{stem}.pdf\"\n---\n\n" + "\n".join(blocks)
    )
    points = {"Correct": 10, "Partially Correct": 6, "Incorrect": 0}
    for qid, label in (graded or {}).items():
        update_student_submission_file(path, StudentSubmissionGradeUpdate(
            question_id=qid, possible_points=10, points_awarded=points[label], rubric_labels=(label,),
        ))


def _pdf(path: Path, count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for _ in range(count):
        doc.new_page()
    doc.save(str(path))
    doc.close()
    path.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": f"g{n}", "page_number": n} for n in range(1, count + 1)],
    }))


def _class_folder(root: Path) -> Path:
    split, subs = root / "split", root / "submissions"
    root.mkdir()
    (root / "qa.md").write_text(QA)
    _pdf(split / "submission-0001.pdf", 5)
    _pdf(split / "submission-0002.pdf", 1)
    _pdf(split / "submission-0003.pdf", 6)
    for stem, pages, graded in (
        ("submission-0001", {"q1": 1, "q2": 5, "q3": None}, {"q1": "Correct", "q3": "Incorrect"}),
        ("submission-0002", {"q1": None, "q2": None, "q3": 1}, {"q3": "Partially Correct"}),
        ("submission-0003", {"q1": 1, "q2": 5, "q3": 6}, {}),
    ):
        _submission(subs / stem, stem, pages, graded)
        append_record(subs, ExtractionRecord(
            key=stem, source=str(split / f"{stem}.pdf"), source_sha256="x", settings_id="s",
            outputs=(f"{stem}/{stem}.submission.md",),
        ))
    record_extract_command(subs, split, ["--input", str(split), "--output", str(subs), "--resume"])
    return subs


async def _settle(pilot, predicate, limit: int = 200) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _has(app, selector: str) -> bool:  # noqa: ANN001
    try:
        app.screen.query_one(selector)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_x_joins_the_one_page_piece_onto_the_paper_before_it(tmp_path, monkeypatch) -> None:
    subs = _class_folder(tmp_path / "quiz")

    def reread(argv, **_kwargs):
        if "--adopt-only" in argv:
            return
        _submission(subs / "submission-0001", "submission-0001", {"q1": 1, "q2": 5, "q3": 6})

    monkeypatch.setattr(submission_extractor, "run_cli", reread)
    seen: dict = {}

    async def run() -> None:
        app = TuiApp(
            qa_file_path=tmp_path / "quiz" / "qa.md",
            submissions_path=subs,
            default_image_path=DEFAULT_IMAGE_PATH,
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.grade_controller.open_review_target("s2", "q3")
            await pilot.pause()
            app.grade_controller.on_rubric_selected(0)  # an undo entry that must not survive
            await pilot.press("X")
            await _settle(pilot, lambda: _has(app, "#join-before"))
            seen["here"] = str(app.screen.query_one("#join-here").render())
            seen["after"] = _has(app, "#join-after")
            app.screen.query_one("#join-before").press()
            await _settle(pilot, lambda: not app._joining_split and not _has(app, "#join-dialog"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            seen["students"] = [item.item_id for item in app.workflow_state.student_items]
            seen["selected"] = app.workflow_state.selection.student_item_id
            seen["page"] = app.workflow_state.current_page
            seen["log"] = len(app.workflow_state.grading_log)

    asyncio.run(run())

    assert seen["here"] == "This submission: submission-0002 · 1 page · 1 graded"
    assert seen["after"] is True
    graded = {q.question_id: q for q in load_student_submission_markdown(
        subs / "submission-0001" / "submission-0001.submission.md").questions}
    assert graded["q1"].rubric_labels == ("Correct",)
    # The press of 1 above regraded q3 on the one-page piece as Correct; that is
    # the grade carried over, not the first piece's "Incorrect" for a missing page.
    assert graded["q3"].rubric_labels == ("Correct",)
    assert not (subs / "submission-0002").exists()
    assert seen["students"] == ["s1", "s2"]
    assert (seen["selected"], seen["page"]) == ("s1", PAGE_GRADE)
    assert seen["log"] == 0


def test_a_cloud_reading_asks_first_and_finishes_the_join_on_yes(tmp_path, monkeypatch) -> None:
    from opengrader.core.cloud_preflight import CloudConfirmationNeeded, CloudPreflight

    subs = _class_folder(tmp_path / "quiz")
    reads: list[list[str]] = []

    def reread(argv, **_kwargs):
        if "--adopt-only" in argv:
            return
        reads.append(list(argv))
        if "--yes-send-to-cloud" not in argv:
            raise CloudConfirmationNeeded(CloudPreflight(provider_label="Cloud AI", host="ai.example.edu"))
        _submission(subs / "submission-0001", "submission-0001", {"q1": 1, "q2": 5, "q3": 6})

    monkeypatch.setattr(submission_extractor, "run_cli", reread)
    seen: dict = {}

    async def run() -> None:
        app = TuiApp(
            qa_file_path=tmp_path / "quiz" / "qa.md",
            submissions_path=subs,
            default_image_path=DEFAULT_IMAGE_PATH,
        )
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            app.grade_controller.open_review_target("s2", "q3")
            await pilot.pause()
            await pilot.press("X")
            await _settle(pilot, lambda: _has(app, "#join-before"))
            app.screen.query_one("#join-before").press()
            await _settle(pilot, lambda: _has(app, "#consent-accept"))
            seen["asked"] = _has(app, "#consent-accept")
            app.screen.query_one("#consent-accept").press()
            await _settle(pilot, lambda: not app._joining_split and not _has(app, "#consent-accept"))
            await app.workers.wait_for_complete()
            await pilot.pause()
            seen["students"] = [item.item_id for item in app.workflow_state.student_items]

    asyncio.run(run())

    assert seen["asked"]
    assert ["--yes-send-to-cloud" in argv for argv in reads] == [False, True]
    assert seen["students"] == ["s1", "s2"]
    graded = {q.question_id: q for q in load_student_submission_markdown(
        subs / "submission-0001" / "submission-0001.submission.md").questions}
    assert graded["q3"].rubric_labels == ("Partially Correct",)

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input

from opengrader.core.history import GradeJournal, read_journal, resolve_history_dir
from opengrader.core.history.restore import AspectValue, journal_payload
from opengrader.core.submissions.student_file import (
    StudentSubmissionGradeUpdate,
    load_student_submission_markdown,
    update_student_submission_file,
)
from opengrader.tui.ui.screens.backup_screen import BackupScreen
from opengrader.tui.ui.screens.handoff_screen import (
    GraderPacketScreen,
    MergeScreen,
    _ConflictRow,
)

SUBMISSION = """---
student_name: "{name}"
student_id: "{sid}"
course_code: "CS146"
assignment_title: "Quiz 1"
source_pdf: ""
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is a loop?
  - Response text: a repeat
  - Possible points: 5
  - Points:
  - Rubric:
  - Analysis:

- q2:
  - Question type: Free Response
  - Prompt: What is a stack?
  - Response text: LIFO
  - Possible points: 5
  - Points:
  - Rubric:
  - Analysis:
"""


class _Host(App):
    def __init__(self, screen) -> None:  # noqa: ANN001
        super().__init__()
        self._screen = screen

    def on_mount(self) -> None:
        self.push_screen(self._screen)


@pytest.fixture()
def cohort(tmp_path: Path) -> Path:
    root = tmp_path / "master"
    for index, (name, sid) in enumerate(
        [("Ada Lovelace", "1001"), ("Alan Turing", "1002")], start=1
    ):
        student = root / f"sub-{index:04d}"
        student.mkdir(parents=True)
        (student / f"sub-{index:04d}.submission.md").write_text(
            SUBMISSION.format(name=name, sid=sid)
        )
    return root


def _grade(
    folder: Path, actor: str, question_id: str, points: float, *, index: int = 0
) -> None:
    path = sorted(folder.glob("*/*.submission.md"))[index]
    document = load_student_submission_markdown(path)
    before = next(q for q in document.questions if q.question_id == question_id)
    update_student_submission_file(
        path,
        StudentSubmissionGradeUpdate(
            question_id=question_id,
            points_awarded=points,
            rubric_labels=("Correct",),
        ),
    )
    GradeJournal(resolve_history_dir(folder), actor=actor).append(
        kind="grade",
        student_id=document.student_id,
        student_name=document.student_name,
        question_id=question_id,
        summary="",
        old=journal_payload(
            "grade",
            AspectValue(points=before.points_awarded, rubric=tuple(before.rubric_labels)),
        ),
        new=journal_payload("grade", AspectValue(points=points, rubric=("Correct",))),
    )


def _grade_packet(
    cohort: Path, packet: Path, actor: str, question_id: str, points: float
) -> None:
    from opengrader.core.handoff.packet import (
        read_handoff_key,
        read_packet_manifest,
        resolve_handoff_dir,
    )

    # Keyed aliases share no order with the master; find the student via the key.
    master = sorted(cohort.glob("*/*.submission.md"))[0]
    student_id = load_student_submission_markdown(master).student_id
    packet_id = read_packet_manifest(packet).packet_id
    key = read_handoff_key(resolve_handoff_dir(cohort) / f"{packet_id}.json")
    alias = next(m.alias for m in key.members if m.student_id == student_id)
    paths = sorted((packet / "submissions").glob("*/*.submission.md"))
    position = next(i for i, path in enumerate(paths) if path.parent.name == alias)
    _grade(packet / "submissions", actor, question_id, points, index=position)


def _points(folder: Path, question_id: str) -> float | None:
    path = sorted(folder.glob("*/*.submission.md"))[0]
    document = load_student_submission_markdown(path)
    return next(
        q for q in document.questions if q.question_id == question_id
    ).points_awarded


async def _settle(pilot, predicate, limit: int = 80) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def test_creating_a_copy_writes_a_redacted_packet(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(
            submissions_path=str(cohort), output_path=str(tmp_path / "out")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-grader", Input).value = "alice"
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )

            packet = tmp_path / "out" / "quiz-1-for-alice"
            assert packet.is_dir()
            blob = b"".join(
                path.read_bytes() for path in packet.rglob("*") if path.is_file()
            )
            assert b"Ada" not in blob and b"1001" not in blob

    asyncio.run(run())


def test_the_key_location_is_stated_in_the_result(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(
            submissions_path=str(cohort), output_path=str(tmp_path / "out")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )
            rendered = "".join(
                str(line) for line in screen.query_one("#packet-log").lines
            )
            assert "Keep this file" in rendered
            assert "Do not send it" in rendered

    asyncio.run(run())


def test_a_failure_is_shown_rather_than_raised(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(
            submissions_path=str(tmp_path / "nope"), output_path=str(tmp_path / "out")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )
            rendered = "".join(
                str(line) for line in screen.query_one("#packet-log").lines
            )
            assert "Could not create the copy" in rendered

    asyncio.run(run())


def _pack(cohort: Path, out: Path) -> Path:
    from opengrader.core.handoff.packet import build_packet

    return build_packet(cohort, out, packet_id="p1").packet_path


def test_preview_lists_changes_and_conflicts_and_writes_nothing(
    cohort: Path, tmp_path: Path
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 5.0)
    _grade_packet(cohort, packet, "alice", "q2", 2.0)
    _grade(cohort, "prof", "q2", 4.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)

            plan = screen._plan
            assert plan is not None
            assert [change.question_id for change in plan.changes] == ["q1"]
            assert [conflict.question_id for conflict in plan.conflicts] == ["q2"]
            assert len(list(screen.query(_ConflictRow))) == 1
            assert _points(cohort, "q1") is None
            assert _points(cohort, "q2") == 4.0

    asyncio.run(run())


def test_a_conflict_row_defaults_to_keeping_ours_and_is_focused(
    cohort: Path, tmp_path: Path
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 2.0)
    _grade(cohort, "prof", "q1", 5.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)

            rows = list(screen.query(_ConflictRow))
            assert rows[0].decision == "ours"
            assert screen.focused is rows[0]

    asyncio.run(run())


def test_t_and_o_set_one_conflict_and_apply_honours_it(
    cohort: Path, tmp_path: Path
) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 2.0)
    _grade_packet(cohort, packet, "alice", "q2", 1.0)
    _grade(cohort, "prof", "q1", 5.0)
    _grade(cohort, "prof", "q2", 4.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)

            rows = list(screen.query(_ConflictRow))
            assert len(rows) == 2
            by_question = {row.conflict.question_id: row for row in rows}
            by_question["q1"].focus()
            await pilot.press("t")
            await pilot.pause()
            assert by_question["q1"].decision == "theirs"
            assert by_question["q2"].decision == "ours"

            await pilot.click("#merge-apply")
            await _settle(pilot, lambda: screen._plan is None)

            assert _points(cohort, "q1") == 2.0
            assert _points(cohort, "q2") == 4.0

    asyncio.run(run())


def test_j_and_k_move_between_conflict_rows(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 2.0)
    _grade_packet(cohort, packet, "alice", "q2", 1.0)
    _grade(cohort, "prof", "q1", 5.0)
    _grade(cohort, "prof", "q2", 4.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)

            rows = list(screen.query(_ConflictRow))
            await pilot.press("j")
            await pilot.pause()
            assert screen.focused is rows[1]
            await pilot.press("k")
            await pilot.pause()
            assert screen.focused is rows[0]

    asyncio.run(run())


def test_take_theirs_for_all_sets_every_row(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 2.0)
    _grade_packet(cohort, packet, "alice", "q2", 1.0)
    _grade(cohort, "prof", "q1", 5.0)
    _grade(cohort, "prof", "q2", 4.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)

            await pilot.click("#merge-all-theirs")
            await pilot.pause()
            assert all(row.decision == "theirs" for row in screen.query(_ConflictRow))
            await pilot.click("#merge-all-ours")
            await pilot.pause()
            assert all(row.decision == "ours" for row in screen.query(_ConflictRow))

    asyncio.run(run())


def test_apply_is_disabled_until_a_preview_has_run(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 5.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert screen.query_one("#merge-apply", Button).disabled

            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)
            assert not screen.query_one("#merge-apply", Button).disabled

            await pilot.click("#merge-apply")
            await _settle(pilot, lambda: screen._plan is None)
            assert screen._plan is None

    asyncio.run(run())


def test_the_merge_is_journalled_so_it_can_be_undone(cohort: Path, tmp_path: Path) -> None:
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 5.0)

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)
            await pilot.click("#merge-apply")
            await _settle(pilot, lambda: screen._plan is None)

            actors = {entry.actor for entry in read_journal(resolve_history_dir(cohort))}
            assert "merge:p1" in actors
            result = str(screen.query_one("#merge-body").children[0].render())
            assert "--without-actor merge:p1" in result

    asyncio.run(run())


def test_history_import_is_unchecked_by_default(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert screen.query_one("#merge-import-history", Checkbox).value is False

    asyncio.run(run())


def test_a_missing_handoff_key_is_reported_not_read_as_no_changes(
    cohort: Path, tmp_path: Path
) -> None:
    from opengrader.core.handoff.packet import resolve_handoff_dir

    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 5.0)
    (resolve_handoff_dir(cohort) / "p1.json").unlink()

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(
                pilot,
                lambda: "Could not compare"
                in str(screen.query_one("#merge-summary").render()),
            )
            summary = str(screen.query_one("#merge-summary").render())
            assert "Could not compare" in summary
            assert screen._plan is None

    asyncio.run(run())


def test_the_destination_field_is_hidden_until_the_box_is_ticked(cohort: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert screen.query_one("#packet-custom-output", Checkbox).value is False
            assert screen.query_one("#packet-output-area").has_class("hidden")

            screen.query_one("#packet-custom-output", Checkbox).value = True
            await pilot.pause()
            assert not screen.query_one("#packet-output-area").has_class("hidden")

    asyncio.run(run())


def test_the_default_destination_is_named_before_anything_is_written(
    cohort: Path,
) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            hint = str(screen.query_one("#packet-destination-hint").render())
            assert str(cohort.parent / "grader-copies") in hint

    asyncio.run(run())


def test_leaving_the_box_unticked_writes_to_the_default(cohort: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-grader", Input).value = "alice"
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )
            assert (cohort.parent / "grader-copies" / "quiz-1-for-alice").is_dir()

    asyncio.run(run())


def test_a_ticked_box_with_a_path_wins(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-grader", Input).value = "alice"
            screen.query_one("#packet-custom-output", Checkbox).value = True
            screen.query_one("#packet-output", Input).value = str(tmp_path / "elsewhere")
            await pilot.pause()
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )
            assert (tmp_path / "elsewhere" / "quiz-1-for-alice").is_dir()
            assert not (cohort.parent / "grader-copies").exists()

    asyncio.run(run())


def test_a_stale_path_in_a_hidden_field_is_ignored_entirely(
    cohort: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-grader", Input).value = "alice"
            screen.query_one("#packet-custom-output", Checkbox).value = True
            screen.query_one("#packet-output", Input).value = str(tmp_path / "stale")
            await pilot.pause()
            screen.query_one("#packet-custom-output", Checkbox).value = False
            await pilot.pause()
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )
            assert not (tmp_path / "stale").exists()
            assert (cohort.parent / "grader-copies" / "quiz-1-for-alice").is_dir()

    asyncio.run(run())


def _launch(cohort: Path, qa: Path):  # noqa: ANN202
    from opengrader.tui.tui import TuiApp

    return TuiApp(qa_file_path=qa, submissions_path=cohort)


@pytest.fixture()
def sample_session(tmp_path: Path) -> tuple[Path, Path]:
    import shutil

    repo = Path(__file__).resolve().parents[3]
    shutil.copytree(repo / "samples" / "cs146-quiz1", tmp_path / "cs146-quiz1")
    shutil.copy(repo / "samples" / "qa-multi.md", tmp_path / "qa-multi.md")
    return (
        tmp_path / "qa-multi.md",
        tmp_path / "cs146-quiz1" / "student-submissions",
    )


def test_the_tools_are_reachable_from_a_real_grading_session(
    sample_session: tuple[Path, Path],
) -> None:
    qa, cohort = sample_session

    async def run() -> None:
        app = _launch(cohort, qa)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            from opengrader.tui.state import PAGE_QA_PICKER

            assert app.workflow_state.current_page == PAGE_QA_PICKER

            for key, screen_type in (
                ("E", GraderPacketScreen),
                ("M", MergeScreen),
                ("B", BackupScreen),
            ):
                await pilot.press(key)
                await pilot.pause()
                assert isinstance(app.screen, screen_type), key
                app.screen.dismiss(None)
                await pilot.pause()

    asyncio.run(run())


def test_the_tools_are_reachable_mid_question_too(
    sample_session: tuple[Path, Path],
) -> None:
    qa, cohort = sample_session

    async def run() -> None:
        app = _launch(cohort, qa)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("enter")
            await pilot.pause()
            from opengrader.tui.state import PAGE_GRADE

            assert app.workflow_state.current_page == PAGE_GRADE
            await pilot.press("E")
            await pilot.pause()
            assert isinstance(app.screen, GraderPacketScreen)

    asyncio.run(run())


def test_the_merge_screen_names_students_by_id_outside_teacher_mode(
    sample_session: tuple[Path, Path],
) -> None:
    qa, cohort = sample_session

    async def run() -> None:
        app = _launch(cohort, qa)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            assert app.workflow_state.teacher_mode is False
            await pilot.press("M")
            await pilot.pause()
            assert app.screen._teacher_mode is False
            app.screen.dismiss(None)
            await pilot.pause()

            await pilot.press("t")
            await pilot.pause()
            await pilot.press("M")
            await pilot.pause()
            assert app.screen._teacher_mode is True

    asyncio.run(run())


def test_a_merge_reloads_the_session_so_the_next_save_still_works(
    sample_session: tuple[Path, Path], tmp_path: Path
) -> None:
    qa, cohort = sample_session
    packet = _pack(cohort, tmp_path / "out")
    _grade_packet(cohort, packet, "alice", "q1", 5.0)

    async def run() -> None:
        app = _launch(cohort, qa)
        async with app.run_test(size=(140, 45)) as pilot:
            await pilot.pause()
            await pilot.press("M")
            await pilot.pause()
            merge = app.screen
            merge.query_one("#merge-incoming", Input).value = str(packet)
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: merge._plan is not None)
            await pilot.click("#merge-apply")
            await _settle(pilot, lambda: merge._plan is None)
            merge.dismiss(merge._merged)
            await _settle(pilot, lambda: not isinstance(app.screen, MergeScreen))

            provider = app.data_provider
            questions = {
                question.question_id: question
                for question in provider.build_question_seeds(student_item_id="s1")
            }
            assert questions["q1"].points_awarded == 5.0

            provider.save_question_grade(
                student_item_id="s1",
                question_id="q2",
                possible_points=questions["q2"].points,
                points_awarded=questions["q2"].points,
                rubric_labels=("Correct",),
            )
            assert _points(cohort, "q2") == questions["q2"].points
            assert _points(cohort, "q1") == 5.0

    asyncio.run(run())


def test_a_passphrase_seals_the_copy_into_one_file(cohort: Path, tmp_path: Path) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(
            submissions_path=str(cohort), output_path=str(tmp_path / "out")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-grader", Input).value = "alice"
            screen.query_one("#packet-encrypt", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#packet-passphrase", Input).value = "correct horse"
            screen.query_one("#packet-passphrase-confirm", Input).value = "correct horse"
            await pilot.click("#packet-run")
            await _settle(
                pilot, lambda: not screen.query_one("#packet-run", Button).disabled
            )

            sealed = tmp_path / "out" / "quiz-1-for-alice.zip.enc"
            assert sealed.is_file()
            assert not (tmp_path / "out" / "quiz-1-for-alice").exists()
            assert b"Ada" not in sealed.read_bytes()

    asyncio.run(run())


def test_mismatched_passphrases_do_not_create_anything(
    cohort: Path, tmp_path: Path
) -> None:
    async def run() -> None:
        screen = GraderPacketScreen(
            submissions_path=str(cohort), output_path=str(tmp_path / "out")
        )
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#packet-encrypt", Checkbox).value = True
            await pilot.pause()
            screen.query_one("#packet-passphrase", Input).value = "one"
            screen.query_one("#packet-passphrase-confirm", Input).value = "two"
            await pilot.click("#packet-run")
            await pilot.pause()
            assert not (tmp_path / "out").exists()

    asyncio.run(run())


def test_the_merge_dialog_asks_for_a_passphrase_only_when_the_copy_is_sealed(
    cohort: Path, tmp_path: Path
) -> None:
    from opengrader.core.handoff.packet import build_packet, seal_packet

    plain = build_packet(cohort, tmp_path / "plain", packet_id="p1").packet_path
    sealed = seal_packet(
        build_packet(cohort, tmp_path / "sealed", packet_id="p2").packet_path, "p"
    )

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            field = screen.query_one("#merge-incoming", Input)

            field.value = str(plain)
            await pilot.pause()
            assert screen.query_one("#merge-passphrase-area").has_class("hidden")

            field.value = str(sealed)
            await pilot.pause()
            assert not screen.query_one("#merge-passphrase-area").has_class("hidden")

    asyncio.run(run())


def test_the_merge_dialog_previews_a_sealed_copy(cohort: Path, tmp_path: Path) -> None:
    from opengrader.core.handoff.packet import build_packet, seal_packet

    packet = build_packet(cohort, tmp_path / "out", packet_id="p1").packet_path
    _grade_packet(cohort, packet, "alice", "q1", 5.0)
    sealed = seal_packet(packet, "correct horse")

    async def run() -> None:
        screen = MergeScreen(submissions_path=str(cohort))
        app = _Host(screen)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen.query_one("#merge-incoming", Input).value = str(sealed)
            await pilot.pause()
            screen.query_one("#merge-passphrase", Input).value = "correct horse"
            await pilot.click("#merge-preview")
            await _settle(pilot, lambda: screen._plan is not None)
            assert screen._plan is not None
            assert len(screen._plan.changes) == 1

    asyncio.run(run())

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from opengrader.cli import run_command
from opengrader.cli import automated_grading, run as run_module
from opengrader.cli import submission_extractor, submission_splitter
from opengrader.core import app_config
from opengrader.core.pipeline import (
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    PipelineReport,
    PipelineRequest,
    StageOutcome,
    StageStatus,
)


def _outcome(stage: str, status: StageStatus, message: str = "did a thing") -> StageOutcome:
    return StageOutcome(stage=stage, status=status, message=message)


def _cohort(tmp_path: Path, submissions: int = 2) -> Path:
    root = tmp_path / "cohort" / "submissions"
    for index in range(1, submissions + 1):
        stem = f"submission-{index:04d}"
        (root / stem).mkdir(parents=True, exist_ok=True)
        (root / stem / f"{stem}.submission.md").write_text("# stub\n", encoding="utf-8")
    return root


def _report(tmp_path: Path, *stages: StageOutcome, submissions: int = 2) -> PipelineReport:
    return PipelineReport(
        stages=tuple(stages),
        submissions_dir=_cohort(tmp_path, submissions),
        qa_file=tmp_path / "qa.md",
    )


@pytest.fixture
def captured(monkeypatch, tmp_path):
    seen: dict[str, object] = {}

    def fake_run(request: PipelineRequest, *, progress=None):
        seen["request"] = request
        seen["progress"] = progress
        return seen.get(
            "report",
            _report(
                tmp_path,
                _outcome(STAGE_SCAN_CHECK, StageStatus.RAN, "4 page(s) checked"),
                _outcome(STAGE_SPLIT, StageStatus.REUSED, "reused 42 split PDF(s) from the previous run"),
                _outcome(STAGE_EXTRACT, StageStatus.RAN, "42 submission(s) ready to grade"),
                _outcome(STAGE_AUTO_GRADE, StageStatus.SKIPPED, "no answer key given"),
            ),
        )

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)

    def publish(report, root, assignment_id):
        seen["publication"] = report, root, assignment_id
        return replace(report, workspace=root or tmp_path, assignment_id=assignment_id)

    monkeypatch.setattr(run_module, "_publish_results", publish)
    return seen


def _argv(tmp_path, *extra: str) -> list[str]:
    return ["--scan", str(tmp_path / "bundle.pdf"), "--out", str(tmp_path / "cohort"), *extra]


def _exit_status(argv: list[str]) -> int:
    try:
        run_command(run_module.command, argv)
    except SystemExit as exc:
        return exc.code
    return 0


def test_the_required_pair_is_scan_and_out(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path))
    request = captured["request"]
    assert request.scan == tmp_path / "bundle.pdf"
    assert request.output_dir == tmp_path / "cohort"
    assert request.split_dir == tmp_path / "cohort" / "split"
    assert request.submissions_dir == tmp_path / "cohort" / "submissions"


def test_every_option_lands_in_the_request(tmp_path, captured) -> None:
    run_command(
        run_module.command,
        _argv(
            tmp_path,
            "--qa", str(tmp_path / "qa.md"),
            "--roster", str(tmp_path / "roster.tsv"),
            "--blank-template", str(tmp_path / "blank.pdf"),
            "--pages-per-submission", "6",
            "--jobs", "4",
            "--llm",
            "--llm-vision-model", "qwen2.5vl:7b",
            "--llm-text-model", "llama3.1",
            "--ollama-host", "http://box:11434",
            "--auto-ollama",
            "--llm-min-confidence", "0.75",
            "--force",
            "--fresh",
        )
    )
    request = captured["request"]
    assert request.qa_file == tmp_path / "qa.md"
    assert request.roster == tmp_path / "roster.tsv"
    assert request.blank_template == tmp_path / "blank.pdf"
    assert request.pages_per_submission == 6
    assert request.jobs == 4
    assert request.auto_grade is True
    assert request.force is True
    assert request.fresh is True
    assert request.llm.enabled is True
    assert request.llm.vision_model == "qwen2.5vl:7b"
    assert request.llm.text_model == "llama3.1"
    assert request.llm.host == "http://box:11434"
    assert request.llm.auto_ollama is True
    assert request.llm.min_confidence == 0.75


def test_no_auto_grade_turns_the_stage_off(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path, "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_llm_absent_follows_the_two_settings_independently(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=False)
    )
    run_command(run_module.command, _argv(tmp_path))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is False


def test_llm_absent_follows_the_two_settings_the_other_way(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=False, grade_written=True)
    )
    run_command(run_module.command, _argv(tmp_path))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is True


def test_llm_flag_is_a_blunt_override_for_both(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=False, grade_written=False)
    )
    run_command(run_module.command, _argv(tmp_path, "--llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is True


def test_no_llm_flag_turns_both_off_even_if_ai_setup_says_otherwise(
    tmp_path, captured
) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=True)
    )
    run_command(run_module.command, _argv(tmp_path, "--no-llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is False


def test_auto_grade_defaults_on_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=True))
    run_command(run_module.command, _argv(tmp_path))
    assert captured["request"].auto_grade is True


def test_auto_grade_defaults_off_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=False))
    run_command(run_module.command, _argv(tmp_path))
    assert captured["request"].auto_grade is False


def test_no_auto_grade_still_works_with_ai_setup_on(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=True))
    run_command(run_module.command, _argv(tmp_path, "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_auto_ollama_defaults_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_start=False))
    run_command(run_module.command, _argv(tmp_path))
    assert captured["request"].llm.auto_ollama is False

    app_config.save_settings(app_config.AISettings(auto_start=True))
    run_command(run_module.command, _argv(tmp_path))
    assert captured["request"].llm.auto_ollama is True


def test_absent_paths_stay_none_rather_than_becoming_dot(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path))
    request = captured["request"]
    assert request.qa_file is None
    assert request.roster is None
    assert request.blank_template is None
    assert request.llm.min_confidence is None


def test_the_summary_names_every_stage_and_what_it_did(tmp_path, captured, capsys) -> None:
    run_command(run_module.command, _argv(tmp_path))
    out = capsys.readouterr().out
    assert "Summary" in out
    for stage in (STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE):
        assert stage in out
    assert "ran" in out and "reused" in out and "skipped" in out


def test_the_reused_line_is_concrete_about_what_resume_saved(
    tmp_path, captured, capsys
) -> None:
    run_command(run_module.command, _argv(tmp_path))
    assert "reused 42 split PDF(s) from the previous run" in capsys.readouterr().out


def test_a_finished_run_prints_how_to_open_it(tmp_path, captured, capsys) -> None:
    workspace = tmp_path / "grading workspace"
    run_command(
        run_module.command,
        _argv(tmp_path, "--qa", str(tmp_path / "qa.md"), "--workspace", str(workspace))
    )
    out = capsys.readouterr().out
    assert "2 submission(s) ready" in out
    assert "uv run opengrader tui --workspace" in out
    assert f"--assignment {captured['publication'][2]}" in out
    assert captured["publication"][1] == workspace


@pytest.mark.parametrize("status", [StageStatus.BLOCKED, StageStatus.FAILED])
def test_failed_pipeline_does_not_publish_leftover_outputs(tmp_path, captured, status):
    captured["report"] = _report(tmp_path, _outcome(STAGE_EXTRACT, status))
    code = _exit_status(_argv(tmp_path, "--qa", str(tmp_path / "qa.md")))
    assert code == (2 if status is StageStatus.BLOCKED else 1)
    assert "publication" not in captured


def test_run_without_answer_key_explains_how_to_publish(tmp_path, captured, capsys):
    run_command(run_module.command, _argv(tmp_path))
    assert "publication" not in captured
    assert "--qa" in capsys.readouterr().out


def test_publication_failure_exits_nonzero_and_preserves_outputs(
    tmp_path, monkeypatch, capsys
):
    from opengrader.server.workspace.client import ServiceError

    report = _report(tmp_path, _outcome(STAGE_EXTRACT, StageStatus.RAN))
    report.qa_file.write_text("- (2 points) Question\n")
    monkeypatch.setattr(run_module, "run_pipeline", lambda *args, **kwargs: report)

    def unavailable(root):
        raise ServiceError(0, "Service unavailable")

    monkeypatch.setattr(
        "opengrader.server.workspace.client.ensure_service", unavailable
    )
    assert _exit_status(_argv(tmp_path, "--qa", str(report.qa_file), "--json")) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] is False
    assert result["stages"][-1]["stage"] == "publish"
    assert result["stages"][-1]["status"] == "failed"
    assert result["assignment_id"] is None
    assert report.submission_count == 2


def test_stages_the_run_never_reached_are_named_as_such(
    tmp_path, captured, capsys
) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_SCAN_CHECK, StageStatus.FAILED, "no PDF found"),
    )
    assert _exit_status(_argv(tmp_path)) == 1
    out = capsys.readouterr().out
    assert "not reached" in out
    assert "split, extract, draft-rubrics, auto-grade" in out


def test_a_blocked_run_says_how_to_proceed(tmp_path, captured, capsys) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "2 high-severity finding(s)"),
    )
    assert _exit_status(_argv(tmp_path)) == 2
    out = capsys.readouterr().out
    assert "--force" in out
    assert "before anything was graded" in out


def test_progress_callbacks_are_wired_up(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path))
    progress = captured["progress"]
    assert progress is not None
    assert progress.on_stage_start is not None
    assert progress.on_stage_finish is not None
    assert progress.on_status is not None


def test_a_clean_run_exits_zero(tmp_path, captured) -> None:
    assert _exit_status(_argv(tmp_path)) == 0


def test_a_failed_stage_exits_one(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SPLIT, StageStatus.FAILED, "exited with status 1")
    )
    assert _exit_status(_argv(tmp_path)) == 1


def test_a_blocked_run_exits_two(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "high-severity")
    )
    assert _exit_status(_argv(tmp_path)) == 2


def test_dry_run_prints_the_plan_and_touches_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    def explode(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("--dry-run ran the pipeline")

    monkeypatch.setattr(run_module, "run_pipeline", explode)
    monkeypatch.setattr(run_module, "_publish_results", explode)
    assert (
        _exit_status(_argv(tmp_path, "--dry-run", "--qa", str(tmp_path / "qa.md")))
        == 0
    )

    out = capsys.readouterr().out
    assert "Plan" in out
    assert "submission-splitter --input" in out
    assert "submission-extractor --input" in out
    assert not (tmp_path / "cohort").exists()


def test_dry_run_returns_no_report(tmp_path) -> None:
    assert run_command(run_module.command, _argv(tmp_path, "--dry-run")) is None


def test_json_is_machine_readable(tmp_path, captured, capsys) -> None:
    run_command(run_module.command, _argv(tmp_path, "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["submission_count"] == 2
    assert payload["ready_to_grade"] is True
    assert [stage["stage"] for stage in payload["stages"]] == [
        STAGE_SCAN_CHECK,
        STAGE_SPLIT,
        STAGE_EXTRACT,
        STAGE_AUTO_GRADE,
    ]
    assert payload["stages"][1]["status"] == "reused"


def test_the_cli_drives_the_real_engine(tmp_path, monkeypatch, capsys) -> None:
    import fitz
    from fastapi.testclient import TestClient

    from opengrader.core.submissions import scan_check as scan_check_module
    from opengrader.core.submissions.scan_check import (
        PageScanFeatures,
        build_scan_report,
    )
    from opengrader.core.workspace.store import Workspace
    from opengrader.server.workspace.app import create_app
    from opengrader.server.workspace.client import ServiceConnection, WorkspaceClient

    scans = tmp_path / "scans"
    scans.mkdir()
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "hello")
    doc.save(str(scans / "submission-0001.pdf"))
    doc.close()

    monkeypatch.setattr(
        scan_check_module,
        "analyze_scan_pdf",
        lambda *_a, **_k: build_scan_report([PageScanFeatures(page=1)], source="s.pdf"),
    )
    monkeypatch.setattr(submission_splitter, "run_cli", lambda *a, **k: None)

    submissions = tmp_path / "cohort" / "submissions" / "submission-0001"

    def fake_extract(argv, **_kwargs):
        submissions.mkdir(parents=True, exist_ok=True)
        (submissions / "submission-0001.submission.md").write_text(
            "---\nstudent_name: Student\nstudent_id: '123'\n"
            f"source_pdf: {scans / 'submission-0001.pdf'}\n---\n"
            "- q1:\n  - Response text: 4\n  - Possible points: 2\n",
            encoding="utf-8",
        )

    monkeypatch.setattr(submission_extractor, "run_cli", fake_extract)
    monkeypatch.setattr(automated_grading, "run_cli", lambda *a, **k: None)

    qa = tmp_path / "qa.md"
    qa.write_text("- (2 points) Two plus two?\n  - **Answer**\n    - 4\n")
    workspace = Workspace.create(tmp_path / "workspace")
    connection = ServiceConnection(
        root=str(workspace.root),
        url="http://testserver",
        pid=0,
        workspace_id=workspace.info().id,
    )

    def attach(root):
        assert root == workspace.root
        return connection

    monkeypatch.setattr("opengrader.server.workspace.client.ensure_service", attach)
    with TestClient(create_app(workspace.root, process_jobs=False)) as client:

        def request(self, method, path, body=None, **kwargs):
            response = client.request(
                method, path, json=body.model_dump() if body else None
            )
            response.raise_for_status()
            return response.content

        monkeypatch.setattr(WorkspaceClient, "request", request)
        code = _exit_status(
            [
                "--scan",
                str(scans),
                "--out",
                str(tmp_path / "cohort"),
                "--qa",
                str(qa),
                "--no-auto-grade",
                "--workspace",
                str(workspace.root),
                "--json",
            ]
        )
    assert code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["submission_count"] == 1
    assert {stage["stage"]: stage["status"] for stage in payload["stages"]} == {
        STAGE_SCAN_CHECK: "ran",
        STAGE_SPLIT: "skipped",
        STAGE_EXTRACT: "ran",
        STAGE_DRAFT_RUBRICS: "skipped",
        STAGE_AUTO_GRADE: "skipped",
        "publish": "ran",
    }
    assert payload["workspace"] == str(workspace.root)
    assignment = payload["assignment_id"]
    assert workspace.assignments()[0].id == assignment
    assert workspace.answer(assignment, 1, "q1").response_text == "4"
    document = workspace.document(assignment, 1)
    assert (
        workspace.asset(document.source_pdf).read_bytes()
        == (scans / "submission-0001.pdf").read_bytes()
    )
    assert (tmp_path / "cohort" / ".opengrader" / "run-ledger.jsonl").exists()


def test_json_stdout_is_not_polluted_by_the_inner_clis(
    tmp_path, monkeypatch, capsys
) -> None:
    import fitz

    from opengrader.core.submissions import scan_check as scan_check_module
    from opengrader.core.submissions.scan_check import (
        PageScanFeatures,
        build_scan_report,
    )

    scans = tmp_path / "scans"
    scans.mkdir()
    doc = fitz.open()
    doc.new_page(width=612, height=792).insert_text((72, 100), "hello")
    doc.save(str(scans / "submission-0001.pdf"))
    doc.close()

    monkeypatch.setattr(
        scan_check_module,
        "analyze_scan_pdf",
        lambda *_a, **_k: build_scan_report([PageScanFeatures(page=1)], source="s.pdf"),
    )

    def chatty_extract(argv, **_kwargs):
        print("Found 2 submission PDF(s)")

    monkeypatch.setattr(submission_extractor, "run_cli", chatty_extract)

    _exit_status(["--scan", str(scans), "--out", str(tmp_path / "cohort"), "--json"])
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is True
    assert "Found 2 submission PDF(s)" in captured.err


_QA_TEXT = """---
title: Quiz
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""


def _class_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "cs146-quiz1"
    folder.mkdir()
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "qa.md").write_text(_QA_TEXT, encoding="utf-8")
    (folder / "roster.tsv").write_text("1\tAda\n", encoding="utf-8")
    (folder / "blank-template.pdf").write_bytes(b"%PDF-1.4\n")
    return folder


def test_without_a_class_folder_scan_and_out_are_still_required(
    tmp_path, captured, capsys
) -> None:
    with pytest.raises(SystemExit) as raised:
        run_command(run_module.command, ["--scan", str(tmp_path / "bundle.pdf")])
    assert raised.value.code == 2
    assert "class folder" in capsys.readouterr().err
    assert "request" not in captured


def test_a_class_folder_fills_every_input_and_is_the_output(
    tmp_path, captured, capsys
) -> None:
    folder = _class_folder(tmp_path)
    run_command(run_module.command, [str(folder)])

    request = captured["request"]
    assert request.scan == folder / "scans.pdf"
    assert request.qa_file == folder / "qa.md"
    assert request.roster == folder / "roster.tsv"
    assert request.blank_template == folder / "blank-template.pdf"
    assert request.output_dir == folder
    out = capsys.readouterr().out
    assert f"Class folder {folder}" in out
    assert "✓ answer key" in out


def test_run_folder_uses_its_parent_workspace(tmp_path, captured) -> None:
    from opengrader.core.workspace.store import Workspace

    course = tmp_path / "course"
    course.mkdir()
    folder = _class_folder(course)
    workspace = Workspace.create(course)

    run_command(run_module.command, [str(folder)])

    assert captured["publication"][1] == course
    binding = workspace.assignment_inputs()[0]
    assert binding.folder == "cs146-quiz1"
    assert binding.scan == "scans.pdf"
    assert binding.qa == "qa.md"
    assert captured["publication"][2] == binding.id


def test_run_folder_creates_its_own_workspace_when_none_exists(tmp_path, captured) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)

    run_command(run_module.command, [str(folder)])

    assert captured["publication"][1] == folder
    assert Workspace(folder).assignment_inputs()[0].folder == "."


def test_bare_run_from_an_assignment_subfolder_reuses_saved_inputs(
    tmp_path, monkeypatch, captured
) -> None:
    folder = _class_folder(tmp_path)
    run_command(run_module.command, [str(folder)])
    incoming = folder / "incoming"
    incoming.mkdir()
    monkeypatch.chdir(incoming)

    run_command(run_module.command, [])

    assert captured["request"].scan == folder / "scans.pdf"
    assert captured["request"].qa_file == folder / "qa.md"
    assert captured["publication"][1] == folder


def test_changed_scan_requires_fresh_and_keeps_the_previous_assignment(
    tmp_path, captured, capsys
) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)
    run_command(run_module.command, [str(folder)])
    workspace = Workspace(folder)
    old_id = workspace.assignment_inputs()[0].id
    workspace.create_assignment(_QA_TEXT, assignment_id=old_id)
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\nchanged\n")

    with pytest.raises(SystemExit) as raised:
        run_command(run_module.command, [str(folder)])
    assert raised.value.code == 2
    assert "--fresh" in capsys.readouterr().err
    assert workspace.assignment_inputs()[0].id == old_id

    run_command(run_module.command, [str(folder), "--fresh"])

    new_id = workspace.assignment_inputs()[0].id
    assert new_id != old_id
    assert captured["publication"][2] == new_id
    assert workspace.assignments()[0].id == old_id


def test_failed_fresh_publication_keeps_the_previous_assignment_selected(
    tmp_path, captured, monkeypatch
) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)
    run_command(run_module.command, [str(folder)])
    workspace = Workspace(folder)
    old_id = workspace.assignment_inputs()[0].id
    workspace.create_assignment(_QA_TEXT, assignment_id=old_id)
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\nchanged\n")

    def failed_publish(report, root, assignment_id):
        return replace(
            report,
            stages=(*report.stages, _outcome("publish", StageStatus.FAILED)),
        )

    monkeypatch.setattr(run_module, "_publish_results", failed_publish)
    assert _exit_status([str(folder), "--fresh"]) == 1
    assert workspace.assignment_inputs()[0].id == old_id


def test_a_flag_overrides_what_the_folder_offered(tmp_path, captured, capsys) -> None:
    folder = _class_folder(tmp_path)
    other_key = tmp_path / "other-key.md"
    run_command(
        run_module.command,
        [str(folder), "--qa", str(other_key), "--out", str(tmp_path / "elsewhere")]
    )

    request = captured["request"]
    assert request.qa_file == other_key
    assert request.scan == folder / "scans.pdf"
    assert request.output_dir == tmp_path / "elsewhere"
    assert "(from --qa)" in capsys.readouterr().out


def test_two_possible_scans_stop_the_run_and_name_both(tmp_path, captured, capsys) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "morning.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "afternoon.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as raised:
        run_command(run_module.command, [str(folder)])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "--scan" in err and "morning.pdf" in err and "afternoon.pdf" in err
    assert "request" not in captured


def test_an_explicit_scan_settles_an_ambiguous_folder(tmp_path, captured) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "morning.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "afternoon.pdf").write_bytes(b"%PDF-1.4\n")

    run_command(run_module.command, [str(folder), "--scan", str(folder / "afternoon.pdf")])
    assert captured["request"].scan == folder / "afternoon.pdf"


def test_two_templates_are_left_out_with_a_warning_not_a_stop(
    tmp_path, captured, capsys
) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "scan.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "template-vA.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "template-vB.pdf").write_bytes(b"%PDF-1.4\n")

    run_command(run_module.command, [str(folder)])
    assert captured["request"].blank_template is None
    assert "blank template not used — pass --blank-template" in capsys.readouterr().out


def test_a_folder_with_no_scan_says_so(tmp_path, captured, capsys) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()

    with pytest.raises(SystemExit) as raised:
        run_command(run_module.command, [str(folder)])
    assert raised.value.code == 2
    assert "no scanned bundle" in capsys.readouterr().err


def test_a_class_folder_that_does_not_exist_is_a_usage_error(tmp_path, captured) -> None:
    with pytest.raises(SystemExit) as raised:
        run_command(run_module.command, [str(tmp_path / "nope")])
    assert raised.value.code == 2


def test_json_keeps_the_folder_summary_off_stdout(tmp_path, captured, capsys) -> None:
    folder = _class_folder(tmp_path)
    run_command(run_module.command, [str(folder), "--json"])
    streams = capsys.readouterr()
    assert json.loads(streams.out)["ok"] is True
    assert "Class folder" in streams.err


# ---------------------------------------------------------------------------
# --yes-send-to-cloud / the interactive and non-interactive confirmation gate
# ---------------------------------------------------------------------------


def _cloud_preflight(tmp_path: Path):
    from opengrader.core.cloud_preflight import CloudPreflight

    return CloudPreflight(
        provider_label="Test Cloud",
        host="ai.example.edu",
        counts={"answer-image": 847},
        students=141,
        estimated_cost=3.40,
        cost_basis="measured from previous calls",
    )


def _cloud_refusal_report(tmp_path: Path, preflight=None) -> PipelineReport:
    preflight = preflight if preflight is not None else _cloud_preflight(tmp_path)
    outcome = StageOutcome(
        stage=STAGE_SCAN_CHECK,
        status=StageStatus.BLOCKED,
        message=(
            f"this run would send data to {preflight.host} — re-run with "
            "--yes-send-to-cloud to confirm, or turn off the AI feature that needs it"
        ),
        detail="cloud-preflight:\n" + preflight.statement(),
    )
    return PipelineReport(
        stages=(outcome,),
        submissions_dir=_cohort(tmp_path, 0),
        qa_file=None,
        cloud_preflight=preflight,
    )


class _FakeStdin:
    def isatty(self) -> bool:
        return True


def test_flag_confirmed_run_is_never_refused(tmp_path, monkeypatch) -> None:
    calls: list[bool] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request.send_to_cloud_confirmed)
        return _report(tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.RAN, "ok"))

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    report = run_command(run_module.command, _argv(tmp_path, "--yes-send-to-cloud"))
    assert calls == [True]
    assert report.ok


def test_tty_confirmation_yes_reruns_confirmed(tmp_path, monkeypatch, capsys) -> None:
    calls: list[bool] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request.send_to_cloud_confirmed)
        if not request.send_to_cloud_confirmed:
            return _cloud_refusal_report(tmp_path)
        return _report(tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.RAN, "ok"))

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())
    monkeypatch.setattr("builtins.input", lambda prompt="": "y")

    report = run_command(run_module.command, _argv(tmp_path))

    assert calls == [False, True]
    assert report.ok
    out = capsys.readouterr().out
    assert "ai.example.edu" in out


def test_tty_confirmation_no_stays_refused_and_exits_two(tmp_path, monkeypatch, capsys) -> None:
    def fake_run(request: PipelineRequest, *, progress=None):
        if not request.send_to_cloud_confirmed:
            return _cloud_refusal_report(tmp_path)
        raise AssertionError("should not have re-run without a yes")

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")

    assert _exit_status(_argv(tmp_path)) == 2
    assert "Not confirmed" in capsys.readouterr().out


def test_non_interactive_without_the_flag_prints_statement_and_exits_two(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run(request: PipelineRequest, *, progress=None):
        assert request.send_to_cloud_confirmed is False
        return _cloud_refusal_report(tmp_path)

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    # The default captured stdin under pytest already reports isatty() as
    # False; no monkeypatch needed to simulate a script/redirected run.

    assert _exit_status(_argv(tmp_path)) == 2
    out = capsys.readouterr().out
    assert "847" in out and "ai.example.edu" in out
    assert "--yes-send-to-cloud" in out


def test_json_output_carries_the_refusal_and_statement_by_field(
    tmp_path, monkeypatch, capsys
) -> None:
    def fake_run(request: PipelineRequest, *, progress=None):
        return _cloud_refusal_report(tmp_path)

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)

    assert _exit_status(_argv(tmp_path, "--json")) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["cloud_confirmation_needed"] is True
    assert payload["cloud_preflight"]["host"] == "ai.example.edu"
    assert payload["cloud_preflight"]["counts"]["answer-image"] == 847
    assert payload["cloud_preflight"]["sends_anything"] is True


# ---------------------------------------------------------------------------
# The egress summary in the human report and in --json
# ---------------------------------------------------------------------------


def _report_with_egress(tmp_path: Path) -> PipelineReport:
    from opengrader.core.egress import EgressSummary

    summary = EgressSummary(
        total_calls=3,
        calls_by_category={"answer-image": 3},
        calls_by_host={"ai.example.edu": 3},
        total_input_tokens=900,
        total_output_tokens=45,
        calls_with_tokens=3,
        first_at="2026-01-01T00:00:00Z",
        last_at="2026-01-01T00:00:05Z",
    )
    report = _report(tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.RAN, "ok"))
    return replace(
        report, egress_summary=summary, egress_cost=0.12, egress_cost_basis="measured"
    )


def test_the_summary_reports_what_this_run_sent(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_module, "run_pipeline", lambda request, **kw: _report_with_egress(tmp_path)
    )
    run_command(run_module.command, _argv(tmp_path))
    out = capsys.readouterr().out
    assert "Sent to the cloud" in out
    assert "answer-image" in out
    assert "ai.example.edu" in out
    assert "$0.12" in out


def test_a_local_only_run_prints_no_egress_section(tmp_path, captured, capsys) -> None:
    run_command(run_module.command, _argv(tmp_path))
    assert "Sent to the cloud" not in capsys.readouterr().out


def test_json_egress_field_carries_counts_and_cost(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        run_module, "run_pipeline", lambda request, **kw: _report_with_egress(tmp_path)
    )
    run_command(run_module.command, _argv(tmp_path, "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["egress"]["total_calls"] == 3
    assert payload["egress"]["calls_by_category"]["answer-image"] == 3
    assert payload["egress"]["estimated_cost"] == 0.12


def test_json_egress_field_is_null_for_a_local_only_run(tmp_path, captured, capsys) -> None:
    run_command(run_module.command, _argv(tmp_path, "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert payload["egress"] is None


def test_draft_rubrics_flag_lands_in_the_request(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path, "--draft-rubrics"))
    assert captured["request"].draft_rubrics is True
    run_command(run_module.command, _argv(tmp_path))
    assert captured["request"].draft_rubrics is False


def test_a_run_paused_for_rubric_review_publishes_nothing_and_exits_zero(
    tmp_path, captured, capsys
) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_EXTRACT, StageStatus.RAN, "2 submission(s) ready to grade"),
        _outcome(STAGE_DRAFT_RUBRICS, StageStatus.PAUSED, "drafted rubric items for q2"),
    )
    code = _exit_status(_argv(tmp_path, "--qa", str(tmp_path / "qa.md"), "--draft-rubrics"))

    assert code == 0
    # Publishing now would turn the coming review of the answer key into a
    # "changed input" that demands --fresh.
    assert "publication" not in captured
    out = capsys.readouterr().out
    assert "Paused for review" in out
    assert "run the same command again" in out
    assert "auto-grade" in out  # named under "not reached"


def test_a_paused_run_says_so_in_json(tmp_path, captured, capsys) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_DRAFT_RUBRICS, StageStatus.PAUSED, "drafted rubric items for q2"),
    )
    code = _exit_status(_argv(tmp_path, "--qa", str(tmp_path / "qa.md"), "--json"))
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["paused_for_review"] is True
    assert payload["stages"][-1]["status"] == "paused"


def _page_order_pause(tmp_path: Path) -> PipelineReport:
    paused = StageOutcome(
        stage=STAGE_EXTRACT,
        status=StageStatus.PAUSED,
        message="1 submission(s) look out of order — confirm their page order before extraction",
        detail="page-order:\n1. submission-0001.pdf  page numbers read as 1, 3, 2\nin split",
    )
    return _report(tmp_path, _outcome(STAGE_SPLIT, StageStatus.RAN), paused, submissions=0)


def test_the_page_order_answers_land_in_the_request(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path))
    assert (captured["request"].reorder_pages, captured["request"].keep_page_order) == (False, False)

    run_command(run_module.command, _argv(tmp_path, "--reorder"))
    assert captured["request"].reorder_pages is True

    run_command(run_module.command, _argv(tmp_path, "--keep-page-order"))
    assert captured["request"].keep_page_order is True


def test_a_run_paused_on_page_order_says_how_to_answer_when_nobody_can_be_asked(
    tmp_path, captured, capsys
) -> None:
    captured["report"] = _page_order_pause(tmp_path)

    code = _exit_status(_argv(tmp_path, "--qa", str(tmp_path / "qa.md")))

    out = capsys.readouterr().out
    assert code == 0 and "publication" not in captured
    assert "submission-0001.pdf" in out
    assert "--reorder" in out and "--keep-page-order" in out
    assert "Paused for review" not in out


def test_in_a_terminal_the_page_order_is_asked_and_the_run_carries_on(tmp_path, monkeypatch, capsys) -> None:
    from opengrader.core.pages import order_review

    calls: list[PipelineRequest] = []
    asked: list[Path] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request)
        if len(calls) == 1:
            return _page_order_pause(tmp_path)
        return _report(tmp_path, _outcome(STAGE_EXTRACT, StageStatus.RAN, "ok"))

    def review(split_dir, *, ask, write):
        asked.append(Path(split_dir))
        return True

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(order_review, "review_in_terminal", review)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())

    report = run_command(run_module.command, _argv(tmp_path))

    assert asked == [tmp_path / "cohort" / "split"]
    assert len(calls) == 2 and calls[0] == calls[1]
    assert not report.page_order_review_needed


def test_stopping_at_the_page_order_question_leaves_the_run_paused(tmp_path, monkeypatch, capsys) -> None:
    from opengrader.core.pages import order_review

    calls: list[PipelineRequest] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request)
        return _page_order_pause(tmp_path)

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(order_review, "review_in_terminal", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())

    report = run_command(run_module.command, _argv(tmp_path))

    assert len(calls) == 1 and report.page_order_review_needed


def _split_pause(tmp_path: Path) -> PipelineReport:
    paused = StageOutcome(
        stage=STAGE_EXTRACT,
        status=StageStatus.PAUSED,
        message="the split looks wrong in 1 place(s) — check it before extraction",
        detail=(
            "split-review:\n1. submission-0001: 5 page(s) where 6 are expected; "
            "submission-0002: 1 page(s) where 6 are expected\n"
            "   Merge submission-0001 and submission-0002 (5 + 1 pages) into one "
            "6-page submission.\nin split"
        ),
    )
    return _report(tmp_path, _outcome(STAGE_SPLIT, StageStatus.RAN), paused, submissions=0)


def test_the_split_answers_land_in_the_request(tmp_path, captured) -> None:
    run_command(run_module.command, _argv(tmp_path))
    assert (captured["request"].fix_split, captured["request"].keep_split) == (False, False)

    run_command(run_module.command, _argv(tmp_path, "--fix-split"))
    assert captured["request"].fix_split is True

    run_command(run_module.command, _argv(tmp_path, "--keep-split"))
    assert captured["request"].keep_split is True


def test_a_run_paused_on_the_split_says_how_to_answer_when_nobody_can_be_asked(
    tmp_path, captured, capsys
) -> None:
    captured["report"] = _split_pause(tmp_path)

    code = _exit_status(_argv(tmp_path, "--qa", str(tmp_path / "qa.md")))

    out = capsys.readouterr().out
    assert code == 0 and "publication" not in captured
    assert "Merge submission-0001 and submission-0002" in out
    assert "--fix-split" in out and "--keep-split" in out


def test_in_a_terminal_the_split_is_asked_and_the_run_carries_on(tmp_path, monkeypatch) -> None:
    from opengrader.core.pages import split_review

    calls: list[PipelineRequest] = []
    asked: list[Path] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request)
        if len(calls) == 1:
            return _split_pause(tmp_path)
        return _report(tmp_path, _outcome(STAGE_EXTRACT, StageStatus.RAN, "ok"))

    def review(split_dir, *, ask, write, answer):
        asked.append(Path(split_dir))
        return True

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(split_review, "review_split_in_terminal", review)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())

    report = run_command(run_module.command, _argv(tmp_path))

    assert asked == [tmp_path / "cohort" / "split"]
    assert len(calls) == 2
    assert not report.split_review_needed


def test_stopping_at_the_split_question_leaves_the_run_paused(tmp_path, monkeypatch) -> None:
    from opengrader.core.pages import split_review

    calls: list[PipelineRequest] = []

    def fake_run(request: PipelineRequest, *, progress=None):
        calls.append(request)
        return _split_pause(tmp_path)

    monkeypatch.setattr(run_module, "run_pipeline", fake_run)
    monkeypatch.setattr(split_review, "review_split_in_terminal", lambda *_a, **_k: False)
    monkeypatch.setattr(run_module.sys, "stdin", _FakeStdin())

    report = run_command(run_module.command, _argv(tmp_path))

    assert len(calls) == 1 and report.split_review_needed


def test_a_folder_of_section_scans_runs_them_one_per_section(tmp_path, captured, capsys) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    for name in ("section-01.pdf", "section-02.pdf"):
        (folder / name).write_bytes(b"%PDF-1.4\n")
    (folder / "roster-section-01.tsv").write_text("1\tAda\n", encoding="utf-8")
    (folder / "roster-section-02.tsv").write_text("2\tBo\n", encoding="utf-8")

    run_command(run_module.command, [str(folder)])

    request = captured["request"]
    assert request.scan == folder.resolve()
    assert request.section_scans == (
        f"{folder / 'section-01.pdf'}=Section 01",
        f"{folder / 'section-02.pdf'}=Section 02",
    )
    assert request.section_rosters == (
        f"{folder / 'roster-section-01.tsv'}=Section 01",
        f"{folder / 'roster-section-02.tsv'}=Section 02",
    )
    assert "one per section" in capsys.readouterr().out


def test_section_scans_can_be_named_by_hand(tmp_path, captured) -> None:
    one = tmp_path / "scans" / "mw.pdf"
    two = tmp_path / "scans" / "tth.pdf"
    one.parent.mkdir()
    one.write_bytes(b"%PDF-1.4\n")
    two.write_bytes(b"%PDF-1.4\n")

    run_command(
        run_module.command,
        [
            "--section-scan", f"{one}=MW",
            "--section-scan", f"{two}=TTh",
            "--out", str(tmp_path / "cohort"),
        ],
    )

    request = captured["request"]
    assert request.section_scans == (f"{one}=MW", f"{two}=TTh")
    assert request.scan == one.parent.resolve()


def test_one_section_named_twice_is_a_usage_error(tmp_path, captured, capsys) -> None:
    scan = tmp_path / "section-01.pdf"
    scan.write_bytes(b"%PDF-1.4\n")

    status = _exit_status(
        ["--section-scan", str(scan), "--section-scan", f"{scan}=Section 1",
         "--out", str(tmp_path / "cohort")]
    )

    assert status == 2
    assert "both section" in capsys.readouterr().err
    assert "request" not in captured


def test_scan_and_section_scans_together_are_refused(tmp_path, captured) -> None:
    assert _exit_status(_argv(tmp_path, "--section-scan", str(tmp_path / "x.pdf"))) == 2
    assert "request" not in captured

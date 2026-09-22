from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from opengrader.cli import automated_grading, run as run_module
from opengrader.cli import submission_extractor, submission_splitter
from opengrader.core import app_config
from opengrader.core.pipeline import (
    STAGE_AUTO_GRADE,
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


def test_the_required_pair_is_scan_and_out(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path))
    request = captured["request"]
    assert request.scan == tmp_path / "bundle.pdf"
    assert request.output_dir == tmp_path / "cohort"
    assert request.split_dir == tmp_path / "cohort" / "split"
    assert request.submissions_dir == tmp_path / "cohort" / "submissions"


def test_every_option_lands_in_the_request(tmp_path, captured) -> None:
    run_module.run_cli(
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
    run_module.run_cli(_argv(tmp_path, "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_llm_absent_follows_the_two_settings_independently(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=False)
    )
    run_module.run_cli(_argv(tmp_path))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is False


def test_llm_absent_follows_the_two_settings_the_other_way(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=False, grade_written=True)
    )
    run_module.run_cli(_argv(tmp_path))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is True


def test_llm_flag_is_a_blunt_override_for_both(tmp_path, captured) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=False, grade_written=False)
    )
    run_module.run_cli(_argv(tmp_path, "--llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is True


def test_no_llm_flag_turns_both_off_even_if_ai_setup_says_otherwise(
    tmp_path, captured
) -> None:
    app_config.save_settings(
        app_config.AISettings(read_handwriting=True, grade_written=True)
    )
    run_module.run_cli(_argv(tmp_path, "--no-llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is False


def test_auto_grade_defaults_on_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=True))
    run_module.run_cli(_argv(tmp_path))
    assert captured["request"].auto_grade is True


def test_auto_grade_defaults_off_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=False))
    run_module.run_cli(_argv(tmp_path))
    assert captured["request"].auto_grade is False


def test_no_auto_grade_still_works_with_ai_setup_on(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_grade=True))
    run_module.run_cli(_argv(tmp_path, "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_auto_ollama_defaults_from_ai_setup(tmp_path, captured) -> None:
    app_config.save_settings(app_config.AISettings(auto_start=False))
    run_module.run_cli(_argv(tmp_path))
    assert captured["request"].llm.auto_ollama is False

    app_config.save_settings(app_config.AISettings(auto_start=True))
    run_module.run_cli(_argv(tmp_path))
    assert captured["request"].llm.auto_ollama is True


def test_absent_paths_stay_none_rather_than_becoming_dot(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path))
    request = captured["request"]
    assert request.qa_file is None
    assert request.roster is None
    assert request.blank_template is None
    assert request.llm.min_confidence is None


def test_the_summary_names_every_stage_and_what_it_did(tmp_path, captured, capsys) -> None:
    run_module.run_cli(_argv(tmp_path))
    out = capsys.readouterr().out
    assert "Summary" in out
    for stage in (STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE):
        assert stage in out
    assert "ran" in out and "reused" in out and "skipped" in out


def test_the_reused_line_is_concrete_about_what_resume_saved(
    tmp_path, captured, capsys
) -> None:
    run_module.run_cli(_argv(tmp_path))
    assert "reused 42 split PDF(s) from the previous run" in capsys.readouterr().out


def test_a_finished_run_prints_how_to_open_it(tmp_path, captured, capsys) -> None:
    workspace = tmp_path / "grading workspace"
    run_module.run_cli(
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
    run_module.run_cli(_argv(tmp_path, "--qa", str(tmp_path / "qa.md")))
    assert "publication" not in captured


def test_run_without_answer_key_explains_how_to_publish(tmp_path, captured, capsys):
    run_module.run_cli(_argv(tmp_path))
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
    assert run_module.main(_argv(tmp_path, "--qa", str(report.qa_file), "--json")) == 1
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
    run_module.run_cli(_argv(tmp_path))
    out = capsys.readouterr().out
    assert "not reached" in out
    assert "split, extract, auto-grade" in out


def test_a_blocked_run_says_how_to_proceed(tmp_path, captured, capsys) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "2 high-severity finding(s)"),
    )
    run_module.run_cli(_argv(tmp_path))
    out = capsys.readouterr().out
    assert "--force" in out
    assert "before anything was graded" in out


def test_progress_callbacks_are_wired_up(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path))
    progress = captured["progress"]
    assert progress is not None
    assert progress.on_stage_start is not None
    assert progress.on_stage_finish is not None
    assert progress.on_status is not None


def test_a_clean_run_exits_zero(tmp_path, captured) -> None:
    assert run_module.main(_argv(tmp_path)) == 0


def test_a_failed_stage_exits_one(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SPLIT, StageStatus.FAILED, "exited with status 1")
    )
    assert run_module.main(_argv(tmp_path)) == 1


def test_a_blocked_run_exits_two(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "high-severity")
    )
    assert run_module.main(_argv(tmp_path)) == 2


def test_dry_run_prints_the_plan_and_touches_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    def explode(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("--dry-run ran the pipeline")

    monkeypatch.setattr(run_module, "run_pipeline", explode)
    monkeypatch.setattr(run_module, "_publish_results", explode)
    assert (
        run_module.main(_argv(tmp_path, "--dry-run", "--qa", str(tmp_path / "qa.md")))
        == 0
    )

    out = capsys.readouterr().out
    assert "Plan" in out
    assert "submission-splitter --input" in out
    assert "submission-extractor --input" in out
    assert not (tmp_path / "cohort").exists()


def test_dry_run_returns_no_report(tmp_path) -> None:
    assert run_module.run_cli(_argv(tmp_path, "--dry-run")) is None


def test_json_is_machine_readable(tmp_path, captured, capsys) -> None:
    run_module.run_cli(_argv(tmp_path, "--json"))
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
        code = run_module.main(
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

    run_module.main(["--scan", str(scans), "--out", str(tmp_path / "cohort"), "--json"])
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
        run_module.run_cli(["--scan", str(tmp_path / "bundle.pdf")])
    assert raised.value.code == 2
    assert "class folder" in capsys.readouterr().err
    assert "request" not in captured


def test_a_class_folder_fills_every_input_and_is_the_output(
    tmp_path, captured, capsys
) -> None:
    folder = _class_folder(tmp_path)
    run_module.run_cli([str(folder)])

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

    run_module.run_cli([str(folder)])

    assert captured["publication"][1] == course
    binding = workspace.assignment_inputs()[0]
    assert binding.folder == "cs146-quiz1"
    assert binding.scan == "scans.pdf"
    assert binding.qa == "qa.md"
    assert captured["publication"][2] == binding.id


def test_run_folder_creates_its_own_workspace_when_none_exists(tmp_path, captured) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)

    run_module.run_cli([str(folder)])

    assert captured["publication"][1] == folder
    assert Workspace(folder).assignment_inputs()[0].folder == "."


def test_bare_run_from_an_assignment_subfolder_reuses_saved_inputs(
    tmp_path, monkeypatch, captured
) -> None:
    folder = _class_folder(tmp_path)
    run_module.run_cli([str(folder)])
    incoming = folder / "incoming"
    incoming.mkdir()
    monkeypatch.chdir(incoming)

    run_module.run_cli([])

    assert captured["request"].scan == folder / "scans.pdf"
    assert captured["request"].qa_file == folder / "qa.md"
    assert captured["publication"][1] == folder


def test_changed_scan_requires_fresh_and_keeps_the_previous_assignment(
    tmp_path, captured, capsys
) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)
    run_module.run_cli([str(folder)])
    workspace = Workspace(folder)
    old_id = workspace.assignment_inputs()[0].id
    workspace.create_assignment(_QA_TEXT, assignment_id=old_id)
    (folder / "scans.pdf").write_bytes(b"%PDF-1.4\nchanged\n")

    with pytest.raises(SystemExit) as raised:
        run_module.run_cli([str(folder)])
    assert raised.value.code == 2
    assert "--fresh" in capsys.readouterr().err
    assert workspace.assignment_inputs()[0].id == old_id

    run_module.run_cli([str(folder), "--fresh"])

    new_id = workspace.assignment_inputs()[0].id
    assert new_id != old_id
    assert captured["publication"][2] == new_id
    assert workspace.assignments()[0].id == old_id


def test_failed_fresh_publication_keeps_the_previous_assignment_selected(
    tmp_path, captured, monkeypatch
) -> None:
    from opengrader.core.workspace.store import Workspace

    folder = _class_folder(tmp_path)
    run_module.run_cli([str(folder)])
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
    report = run_module.run_cli([str(folder), "--fresh"])

    assert report is not None and not report.ok
    assert workspace.assignment_inputs()[0].id == old_id


def test_a_flag_overrides_what_the_folder_offered(tmp_path, captured, capsys) -> None:
    folder = _class_folder(tmp_path)
    other_key = tmp_path / "other-key.md"
    run_module.run_cli(
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
    (folder / "period1.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "period2.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as raised:
        run_module.run_cli([str(folder)])
    assert raised.value.code == 2
    err = capsys.readouterr().err
    assert "--scan" in err and "period1.pdf" in err and "period2.pdf" in err
    assert "request" not in captured


def test_an_explicit_scan_settles_an_ambiguous_folder(tmp_path, captured) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "period1.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "period2.pdf").write_bytes(b"%PDF-1.4\n")

    run_module.run_cli([str(folder), "--scan", str(folder / "period2.pdf")])
    assert captured["request"].scan == folder / "period2.pdf"


def test_two_templates_are_left_out_with_a_warning_not_a_stop(
    tmp_path, captured, capsys
) -> None:
    folder = tmp_path / "class"
    folder.mkdir()
    (folder / "scan.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "template-vA.pdf").write_bytes(b"%PDF-1.4\n")
    (folder / "template-vB.pdf").write_bytes(b"%PDF-1.4\n")

    run_module.run_cli([str(folder)])
    assert captured["request"].blank_template is None
    assert "blank template not used — pass --blank-template" in capsys.readouterr().out


def test_a_folder_with_no_scan_says_so(tmp_path, captured, capsys) -> None:
    folder = tmp_path / "empty"
    folder.mkdir()

    with pytest.raises(SystemExit) as raised:
        run_module.run_cli([str(folder)])
    assert raised.value.code == 2
    assert "no scanned bundle" in capsys.readouterr().err


def test_a_class_folder_that_does_not_exist_is_a_usage_error(tmp_path, captured) -> None:
    with pytest.raises(SystemExit) as raised:
        run_module.run_cli([str(tmp_path / "nope")])
    assert raised.value.code == 2


def test_json_keeps_the_folder_summary_off_stdout(tmp_path, captured, capsys) -> None:
    folder = _class_folder(tmp_path)
    run_module.run_cli([str(folder), "--json"])
    streams = capsys.readouterr()
    assert json.loads(streams.out)["ok"] is True
    assert "Class folder" in streams.err

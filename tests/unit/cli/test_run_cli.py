from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.cli import automated_grading, run as run_module
from opengrader.cli import submission_extractor, submission_splitter
from opengrader.core import ai_config
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
    monkeypatch.setattr(run_module, "_open_tui", lambda report: seen.__setitem__("tui", report))
    return seen


def _argv(tmp_path, *extra: str) -> list[str]:
    return ["--scan", str(tmp_path / "bundle.pdf"), "--out", str(tmp_path / "cohort"), *extra]


def test_the_required_pair_is_scan_and_out(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    request = captured["request"]
    assert request.scan == tmp_path / "bundle.pdf"
    assert request.output_dir == tmp_path / "cohort"
    assert request.split_dir == tmp_path / "cohort" / "split"
    assert request.submissions_dir == tmp_path / "cohort" / "submissions"


def test_every_option_lands_in_the_request(tmp_path, captured) -> None:
    run_module.run_cli(
        _argv(
            tmp_path,
            "--no-tui",
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
    run_module.run_cli(_argv(tmp_path, "--no-tui", "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_llm_absent_follows_the_two_settings_independently(tmp_path, captured) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, grade_written=False)
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is False


def test_llm_absent_follows_the_two_settings_the_other_way(tmp_path, captured) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=False, grade_written=True)
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is True


def test_llm_flag_is_a_blunt_override_for_both(tmp_path, captured) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=False, grade_written=False)
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui", "--llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is True
    assert llm.grade_written is True


def test_no_llm_flag_turns_both_off_even_if_ai_setup_says_otherwise(
    tmp_path, captured
) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, grade_written=True)
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui", "--no-llm"))
    llm = captured["request"].llm
    assert llm.read_handwriting is False
    assert llm.grade_written is False


def test_auto_grade_defaults_on_from_ai_setup(tmp_path, captured) -> None:
    ai_config.save_settings(ai_config.AISettings(auto_grade=True))
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    assert captured["request"].auto_grade is True


def test_auto_grade_defaults_off_from_ai_setup(tmp_path, captured) -> None:
    ai_config.save_settings(ai_config.AISettings(auto_grade=False))
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    assert captured["request"].auto_grade is False


def test_no_auto_grade_still_works_with_ai_setup_on(tmp_path, captured) -> None:
    ai_config.save_settings(ai_config.AISettings(auto_grade=True))
    run_module.run_cli(_argv(tmp_path, "--no-tui", "--no-auto-grade"))
    assert captured["request"].auto_grade is False


def test_auto_ollama_defaults_from_ai_setup(tmp_path, captured) -> None:
    ai_config.save_settings(ai_config.AISettings(auto_start=False))
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    assert captured["request"].llm.auto_ollama is False

    ai_config.save_settings(ai_config.AISettings(auto_start=True))
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    assert captured["request"].llm.auto_ollama is True


def test_absent_paths_stay_none_rather_than_becoming_dot(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    request = captured["request"]
    assert request.qa_file is None
    assert request.roster is None
    assert request.blank_template is None
    assert request.llm.min_confidence is None


def test_the_summary_names_every_stage_and_what_it_did(tmp_path, captured, capsys) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    out = capsys.readouterr().out
    assert "Summary" in out
    for stage in (STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE):
        assert stage in out
    assert "ran" in out and "reused" in out and "skipped" in out


def test_the_reused_line_is_concrete_about_what_resume_saved(
    tmp_path, captured, capsys
) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    assert "reused 42 split PDF(s) from the previous run" in capsys.readouterr().out


def test_a_finished_run_prints_how_to_open_it(tmp_path, captured, capsys) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    out = capsys.readouterr().out
    assert "2 submission(s) ready" in out
    assert "uv run tui --qa-file" in out


def test_stages_the_run_never_reached_are_named_as_such(tmp_path, captured, capsys) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_SCAN_CHECK, StageStatus.FAILED, "no PDF found"),
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    out = capsys.readouterr().out
    assert "not reached" in out
    assert "split, extract, auto-grade" in out


def test_a_blocked_run_says_how_to_proceed(tmp_path, captured, capsys) -> None:
    captured["report"] = _report(
        tmp_path,
        _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "2 high-severity finding(s)"),
    )
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    out = capsys.readouterr().out
    assert "--force" in out
    assert "before anything was graded" in out


def test_progress_callbacks_are_wired_up(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path, "--no-tui"))
    progress = captured["progress"]
    assert progress is not None
    assert progress.on_stage_start is not None
    assert progress.on_stage_finish is not None
    assert progress.on_status is not None


def test_a_clean_run_exits_zero(tmp_path, captured) -> None:
    assert run_module.main(_argv(tmp_path, "--no-tui")) == 0


def test_a_failed_stage_exits_one(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SPLIT, StageStatus.FAILED, "exited with status 1")
    )
    assert run_module.main(_argv(tmp_path, "--no-tui")) == 1


def test_a_blocked_run_exits_two(tmp_path, captured) -> None:
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "high-severity")
    )
    assert run_module.main(_argv(tmp_path, "--no-tui")) == 2


def test_main_returns_an_int_and_never_the_report(tmp_path, captured) -> None:
    assert isinstance(run_module.main(_argv(tmp_path, "--no-tui")), int)
    assert isinstance(run_module.run_cli(_argv(tmp_path, "--no-tui")), PipelineReport)


def test_dry_run_prints_the_plan_and_touches_nothing(tmp_path, monkeypatch, capsys) -> None:
    def explode(*_args, **_kwargs):  # pragma: no cover
        raise AssertionError("--dry-run ran the pipeline")

    monkeypatch.setattr(run_module, "run_pipeline", explode)
    assert run_module.main(_argv(tmp_path, "--dry-run", "--qa", str(tmp_path / "qa.md"))) == 0

    out = capsys.readouterr().out
    assert "Plan" in out
    assert "submission-splitter --input" in out
    assert "submission-extractor --input" in out
    assert not (tmp_path / "cohort").exists()


def test_dry_run_returns_no_report(tmp_path) -> None:
    assert run_module.run_cli(_argv(tmp_path, "--dry-run")) is None


def test_json_is_machine_readable_and_never_opens_the_tui(
    tmp_path, captured, capsys
) -> None:
    run_module.run_cli(_argv(tmp_path, "--json", "--tui"))
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
    assert "tui" not in captured


def test_the_tui_opens_by_default_on_a_terminal(tmp_path, captured, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    run_module.run_cli(_argv(tmp_path))
    assert "tui" in captured


def test_the_tui_stays_shut_when_stdout_is_not_a_terminal(tmp_path, captured) -> None:
    run_module.run_cli(_argv(tmp_path))
    assert "tui" not in captured


def test_a_blocked_run_does_not_open_the_tui(tmp_path, captured, monkeypatch) -> None:
    monkeypatch.setattr("sys.stdout.isatty", lambda: True, raising=False)
    captured["report"] = _report(
        tmp_path, _outcome(STAGE_SCAN_CHECK, StageStatus.BLOCKED, "high-severity")
    )
    run_module.run_cli(_argv(tmp_path))
    assert "tui" not in captured


def test_the_tui_is_launched_through_its_own_entry_point(tmp_path, monkeypatch) -> None:
    from opengrader.tui import main as tui_main

    launched: list[list[str]] = []
    monkeypatch.setattr(tui_main, "run_app", lambda argv: launched.append(list(argv)))

    report = _report(
        tmp_path, _outcome(STAGE_EXTRACT, StageStatus.RAN, "2 submission(s) ready")
    )
    run_module._open_tui(report)
    assert launched == [
        [
            "--qa-file",
            str(tmp_path / "qa.md"),
            "--submissions",
            str(tmp_path / "cohort" / "submissions"),
        ]
    ]


def test_without_an_answer_key_the_tui_is_not_launched_on_nothing(tmp_path, monkeypatch) -> None:
    from opengrader.tui import main as tui_main

    launched: list[list[str]] = []
    monkeypatch.setattr(tui_main, "run_app", lambda argv: launched.append(list(argv)))

    report = PipelineReport(
        stages=(_outcome(STAGE_EXTRACT, StageStatus.RAN),),
        submissions_dir=_cohort(tmp_path),
        qa_file=None,
    )
    run_module._open_tui(report)
    assert launched == []


def test_the_cli_drives_the_real_engine(tmp_path, monkeypatch, capsys) -> None:
    import fitz

    from opengrader.core.submissions import scan_check as scan_check_module
    from opengrader.core.submissions.scan_check import PageScanFeatures, build_scan_report

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
        (submissions / "submission-0001.submission.md").write_text("# x\n", encoding="utf-8")

    monkeypatch.setattr(submission_extractor, "run_cli", fake_extract)
    monkeypatch.setattr(automated_grading, "run_cli", lambda *a, **k: None)

    code = run_module.main(
        ["--scan", str(scans), "--out", str(tmp_path / "cohort"), "--no-tui", "--json"]
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
    }
    assert (tmp_path / "cohort" / ".opengrader" / "run-ledger.jsonl").exists()


def test_json_stdout_is_not_polluted_by_the_inner_clis(tmp_path, monkeypatch, capsys) -> None:
    import fitz

    from opengrader.core.submissions import scan_check as scan_check_module
    from opengrader.core.submissions.scan_check import PageScanFeatures, build_scan_report

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

    run_module.main(
        ["--scan", str(scans), "--out", str(tmp_path / "cohort"), "--no-tui", "--json"]
    )
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is True
    assert "Found 2 submission PDF(s)" in captured.err

from __future__ import annotations

import shutil
from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from opengrader.cli import automated_grading, submission_extractor, submission_splitter
from opengrader.core.pipeline import (
    PIPELINE_STAGES,
    STAGE_AUTO_GRADE,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    LlmOptions,
    PipelineProgress,
    PipelineRequest,
    StageStatus,
    plan_pipeline,
    run_pipeline,
)
from opengrader.core.submissions import scan_check as scan_check_module
from opengrader.core.submissions.manifest import (
    RUN_LEDGER_FILENAME,
    read_manifest,
)
from opengrader.core.submissions.scan_check import (
    PageScanFeatures,
    ScanIssue,
    ScanIssueKind,
    build_scan_report,
)

_SAMPLES = Path(__file__).resolve().parents[3] / "samples"


def _pdf(path: Path, pages: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"page {index + 1}")
    doc.save(str(path))
    doc.close()
    return path


class _StageRecorder:
    def __init__(self, split_dir: Path, submissions_dir: Path) -> None:
        self.calls: list[tuple[str, list[str]]] = []
        self._split_dir = split_dir
        self._submissions_dir = submissions_dir
        self.split_count = 2
        self.fail_on: str | None = None
        self.failure: BaseException = RuntimeError("boom")

    def _maybe_fail(self, stage: str) -> None:
        if self.fail_on == stage:
            raise self.failure

    def split(self, argv, *, status_callback=None, **_kwargs) -> None:
        self.calls.append((STAGE_SPLIT, list(argv)))
        self._maybe_fail(STAGE_SPLIT)
        self._split_dir.mkdir(parents=True, exist_ok=True)
        for index in range(1, self.split_count + 1):
            (self._split_dir / f"submission-{index:04d}.pdf").write_bytes(b"%PDF-1.4\n")

    def extract(self, argv, *, progress=None, status_callback=None, **_kwargs) -> None:
        self.calls.append((STAGE_EXTRACT, list(argv)))
        self._maybe_fail(STAGE_EXTRACT)
        for index in range(1, self.split_count + 1):
            stem = f"submission-{index:04d}"
            folder = self._submissions_dir / stem
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{stem}.submission.md").write_text("# stub\n", encoding="utf-8")

    def grade(self, argv, *, progress=None, status_callback=None, **_kwargs) -> None:
        self.calls.append((STAGE_AUTO_GRADE, list(argv)))
        self._maybe_fail(STAGE_AUTO_GRADE)

    def stages(self) -> list[str]:
        return [stage for stage, _argv in self.calls]

    def argv_for(self, stage: str) -> list[str]:
        return next(argv for name, argv in self.calls if name == stage)


@pytest.fixture
def stages(monkeypatch, tmp_path) -> _StageRecorder:
    recorder = _StageRecorder(tmp_path / "cohort" / "split", tmp_path / "cohort" / "submissions")
    monkeypatch.setattr(submission_splitter, "run_cli", recorder.split)
    monkeypatch.setattr(submission_extractor, "run_cli", recorder.extract)
    monkeypatch.setattr(automated_grading, "run_cli", recorder.grade)
    return recorder


def _report(issues=()):
    features = [PageScanFeatures(page=1), PageScanFeatures(page=2)]
    report = build_scan_report(features, source="scan.pdf")
    return replace(report, issues=tuple(issues))


@pytest.fixture
def clean_scan(monkeypatch):
    monkeypatch.setattr(
        scan_check_module, "analyze_scan_pdf", lambda *_args, **_kwargs: _report()
    )


@pytest.fixture
def rotated_scan(monkeypatch):
    issue = ScanIssue(
        kind=ScanIssueKind.ROTATED_PAGE,
        severity="high",
        page=2,
        detail="rotated 180° from upright",
        fixable=True,
    )
    monkeypatch.setattr(
        scan_check_module,
        "analyze_scan_pdf",
        lambda *_args, **_kwargs: _report([issue]),
    )


def _request(tmp_path, **overrides) -> PipelineRequest:
    base = dict(
        scan=_pdf(tmp_path / "scans" / "bundle.pdf", pages=4),
        output_dir=tmp_path / "cohort",
        qa_file=None,
    )
    base.update(overrides)
    return PipelineRequest(**base)  # type: ignore[arg-type]


def test_plan_covers_every_stage_in_run_order(tmp_path) -> None:
    plans = plan_pipeline(_request(tmp_path))
    assert tuple(plan.stage for plan in plans) == PIPELINE_STAGES
    assert PIPELINE_STAGES == (STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE)


def test_scan_check_runs_in_process_and_so_builds_no_command(tmp_path) -> None:
    plan = plan_pipeline(_request(tmp_path))[0]
    assert plan.stage == STAGE_SCAN_CHECK
    assert plan.argv == ()
    assert plan.command == ""


def test_split_argv_always_fixes_rotation_and_order(tmp_path) -> None:
    request = _request(
        tmp_path,
        pages_per_submission=6,
        blank_template=_pdf(tmp_path / "blank.pdf"),
    )
    plan = plan_pipeline(request)[1]
    assert plan.argv == (
        "submission-splitter",
        "--input",
        str(request.scan),
        "--output",
        str(tmp_path / "cohort" / "split"),
        "--fix-rotation",
        "--fix-order",
        "--extra-work-pages",
        "--pages-per-submission",
        "6",
        "--blank-template",
        str(tmp_path / "blank.pdf"),
    )


def test_extract_argv_reads_the_split_dir_and_always_resumes(tmp_path) -> None:
    request = _request(tmp_path, roster=tmp_path / "roster.tsv", jobs=4)
    plan = plan_pipeline(request)[2]
    assert plan.argv == (
        "submission-extractor",
        "--input",
        str(tmp_path / "cohort" / "split"),
        "--output",
        str(tmp_path / "cohort" / "submissions"),
        "--resume",
        "--id-file",
        str(tmp_path / "roster.tsv"),
        "--jobs",
        "4",
    )


def test_extract_argv_reads_an_already_split_directory_directly(tmp_path) -> None:
    folder = tmp_path / "split-in"
    _pdf(folder / "submission-0001.pdf")
    plan = plan_pipeline(_request(tmp_path, scan=folder))[2]
    assert plan.argv[:3] == ("submission-extractor", "--input", str(folder))


def test_llm_escalates_transcription_rather_than_transcribing_everything(tmp_path) -> None:
    request = _request(
        tmp_path,
        llm=LlmOptions(
            read_handwriting=True,
            vision_model="qwen2.5vl:7b",
            host="http://box:11434",
            auto_ollama=True,
        ),
    )
    argv = plan_pipeline(request)[2].argv
    assert "--llm-escalate" in argv
    assert "--llm-responses" not in argv
    assert argv[-5:] == (
        "--ollama-model",
        "qwen2.5vl:7b",
        "--ollama-host",
        "http://box:11434",
        "--auto-ollama",
    )


def test_auto_grade_argv_carries_the_text_model_and_the_confidence_floor(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(
        tmp_path,
        qa_file=qa,
        llm=LlmOptions(grade_written=True, text_model="llama3.1", min_confidence=0.8),
    )
    plan = plan_pipeline(request)[3]
    assert plan.argv == (
        "automated-grading",
        "--solutions",
        str(qa),
        "--input",
        str(tmp_path / "cohort" / "submissions"),
        "--llm",
        "--llm-model",
        "llama3.1",
        "--llm-min-confidence",
        "0.8",
    )


def test_the_vision_model_never_reaches_the_text_grader(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(
        tmp_path,
        qa_file=qa,
        llm=LlmOptions(grade_written=True, vision_model="qwen2.5vl:7b"),
    )
    assert "qwen2.5vl:7b" not in plan_pipeline(request)[3].argv


def test_split_is_skipped_for_a_directory_of_already_split_pdfs(tmp_path) -> None:
    folder = tmp_path / "split-in"
    _pdf(folder / "submission-0001.pdf")
    plan = plan_pipeline(_request(tmp_path, scan=folder))[1]
    assert plan.will_run is False
    assert plan.status is StageStatus.SKIPPED
    assert "already a directory" in plan.reason


def test_auto_grade_is_skipped_without_an_answer_key(tmp_path) -> None:
    plan = plan_pipeline(_request(tmp_path))[3]
    assert plan.status is StageStatus.SKIPPED
    assert "no answer key" in plan.reason


def test_auto_grade_is_skipped_when_turned_off(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    plan = plan_pipeline(_request(tmp_path, qa_file=qa, auto_grade=False))[3]
    assert plan.status is StageStatus.SKIPPED


def test_stages_run_in_order_and_report_what_they_did(tmp_path, stages, clean_scan) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    report = run_pipeline(_request(tmp_path, qa_file=qa))

    assert stages.stages() == [STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE]
    assert [outcome.stage for outcome in report.stages] == list(PIPELINE_STAGES)
    assert all(outcome.status is StageStatus.RAN for outcome in report.stages)
    assert report.ok
    assert report.submission_count == 2
    assert report.ready_to_grade


def test_the_stage_runs_exactly_the_command_the_plan_advertised(
    tmp_path, stages, clean_scan
) -> None:
    request = _request(tmp_path, pages_per_submission=6)
    planned = {plan.stage: plan.argv for plan in plan_pipeline(request)}
    run_pipeline(request)
    for stage in (STAGE_SPLIT, STAGE_EXTRACT):
        assert stages.argv_for(stage) == list(planned[stage][1:])


def test_a_directory_input_skips_the_split_and_still_extracts(
    tmp_path, stages, clean_scan
) -> None:
    folder = tmp_path / "split-in"
    _pdf(folder / "submission-0001.pdf")
    report = run_pipeline(_request(tmp_path, scan=folder))

    assert report.stage(STAGE_SPLIT).status is StageStatus.SKIPPED
    assert STAGE_SPLIT not in stages.stages()
    assert stages.argv_for(STAGE_EXTRACT)[:2] == ["--input", str(folder)]


def test_a_high_severity_finding_blocks_before_anything_is_split(
    tmp_path, stages, rotated_scan
) -> None:
    report = run_pipeline(_request(tmp_path))

    scan = report.stage(STAGE_SCAN_CHECK)
    assert scan.status is StageStatus.BLOCKED
    assert "--force" in scan.message
    assert "rotated 180°" in scan.detail
    assert stages.calls == []
    assert report.stage(STAGE_SPLIT) is None
    assert report.ok is False


def test_force_proceeds_past_a_blocking_finding_but_still_reports_it(
    tmp_path, stages, rotated_scan
) -> None:
    report = run_pipeline(_request(tmp_path, force=True))

    scan = report.stage(STAGE_SCAN_CHECK)
    assert scan.status is StageStatus.RAN
    assert "--force" in scan.message
    assert "rotated 180°" in scan.detail
    assert stages.stages() == [STAGE_SPLIT, STAGE_EXTRACT]


def test_a_blocking_finding_is_never_written_to_the_ledger(
    tmp_path, stages, rotated_scan
) -> None:
    run_pipeline(_request(tmp_path, force=True))
    ledger = read_manifest(tmp_path / "cohort", filename=RUN_LEDGER_FILENAME)
    assert STAGE_SCAN_CHECK not in ledger


def test_a_failing_stage_returns_the_partial_report(tmp_path, stages, clean_scan) -> None:
    stages.fail_on = STAGE_EXTRACT
    stages.failure = SystemExit(1)
    report = run_pipeline(_request(tmp_path))

    assert report.ok is False
    assert report.stage(STAGE_SCAN_CHECK).status is StageStatus.RAN
    assert report.stage(STAGE_SPLIT).status is StageStatus.RAN
    extract = report.stage(STAGE_EXTRACT)
    assert extract.status is StageStatus.FAILED
    assert "exited with status 1" in extract.message
    assert "submission-extractor" in extract.detail
    assert report.stage(STAGE_AUTO_GRADE) is None


def test_an_exception_is_reported_with_its_type(tmp_path, stages, clean_scan) -> None:
    stages.fail_on = STAGE_SPLIT
    stages.failure = FileNotFoundError("input PDF not found: bundle.pdf")
    report = run_pipeline(_request(tmp_path))
    assert report.stage(STAGE_SPLIT).message == (
        "FileNotFoundError: input PDF not found: bundle.pdf"
    )


def test_a_keyboard_interrupt_is_not_swallowed(tmp_path, stages, clean_scan) -> None:
    stages.fail_on = STAGE_SPLIT
    stages.failure = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        run_pipeline(_request(tmp_path))


def test_a_second_run_on_an_unchanged_input_reuses_instead_of_repeating(
    tmp_path, stages, clean_scan
) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(tmp_path, qa_file=qa)

    run_pipeline(request)
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_SCAN_CHECK).status is StageStatus.REUSED
    assert second.stage(STAGE_SPLIT).status is StageStatus.REUSED
    assert second.stage(STAGE_AUTO_GRADE).status is StageStatus.REUSED
    assert stages.stages() == [STAGE_EXTRACT]
    assert "reused 2 split PDF(s)" in second.stage(STAGE_SPLIT).message


def test_extraction_always_runs_because_its_resume_is_one_level_down(
    tmp_path, stages, clean_scan
) -> None:
    request = _request(tmp_path)
    run_pipeline(request)
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_EXTRACT).status is StageStatus.RAN
    assert "--resume" in stages.argv_for(STAGE_EXTRACT)


def test_a_changed_scan_invalidates_the_split(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path)
    run_pipeline(request)
    _pdf(request.scan, pages=8)
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_SPLIT).status is StageStatus.RAN
    assert STAGE_SPLIT in stages.stages()


def test_a_changed_answer_key_invalidates_only_the_grading(
    tmp_path, stages, clean_scan
) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(tmp_path, qa_file=qa)
    run_pipeline(request)

    qa.write_text("# key, revised\n", encoding="utf-8")
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_SPLIT).status is StageStatus.REUSED
    assert second.stage(STAGE_AUTO_GRADE).status is StageStatus.RAN


def test_a_grown_cohort_invalidates_the_grading_pass(tmp_path, stages, clean_scan) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(tmp_path, qa_file=qa)
    run_pipeline(request)

    stages.split_count = 3
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_AUTO_GRADE).status is StageStatus.RAN
    assert STAGE_AUTO_GRADE in stages.stages()


def test_grading_a_reused_cohort_is_not_re_run_just_because_grades_changed(
    tmp_path, stages, clean_scan
) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(tmp_path, qa_file=qa)
    run_pipeline(request)

    graded = request.submissions_dir / "submission-0001" / "submission-0001.submission.md"
    graded.write_text("# stub\n- Points: 10\n", encoding="utf-8")
    stages.calls.clear()
    second = run_pipeline(request)

    assert second.stage(STAGE_AUTO_GRADE).status is StageStatus.REUSED


def test_fresh_ignores_the_ledger_entirely(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path)
    run_pipeline(request)
    stages.calls.clear()
    second = run_pipeline(replace(request, fresh=True))

    assert second.stage(STAGE_SCAN_CHECK).status is StageStatus.RAN
    assert second.stage(STAGE_SPLIT).status is StageStatus.RAN
    assert stages.stages() == [STAGE_SPLIT, STAGE_EXTRACT]


def test_jobs_is_not_part_of_any_settings_fingerprint(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path)
    run_pipeline(request)
    stages.calls.clear()
    second = run_pipeline(replace(request, jobs=8))
    assert second.stage(STAGE_SPLIT).status is StageStatus.REUSED
    assert STAGE_SPLIT not in stages.stages()


def test_a_reused_stage_reports_usable_paths(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path)
    run_pipeline(request)
    second = run_pipeline(request)
    outputs = second.stage(STAGE_SPLIT).outputs
    assert outputs
    assert all(Path(item).is_absolute() and Path(item).exists() for item in outputs)


def test_progress_reports_every_stage_start_and_finish(tmp_path, stages, clean_scan) -> None:
    started: list[str] = []
    finished: list[tuple[str, str]] = []
    run_pipeline(
        _request(tmp_path),
        progress=PipelineProgress(
            on_stage_start=started.append,
            on_stage_finish=lambda outcome: finished.append(
                (outcome.stage, outcome.status.value)
            ),
        ),
    )
    assert started == list(PIPELINE_STAGES)
    assert [stage for stage, _status in finished] == list(PIPELINE_STAGES)
    assert dict(finished)[STAGE_AUTO_GRADE] == "skipped"


def test_status_lines_are_forwarded_from_the_inner_clis(
    tmp_path, monkeypatch, stages, clean_scan
) -> None:
    def talkative_split(argv, *, status_callback=None, **_kwargs):
        status_callback("Wrote 2 split PDFs")
        stages.split(argv)

    monkeypatch.setattr(submission_splitter, "run_cli", talkative_split)
    seen: list[str] = []
    run_pipeline(_request(tmp_path), progress=PipelineProgress(on_status=seen.append))
    assert "Wrote 2 split PDFs" in seen


@pytest.mark.skipif(not _SAMPLES.is_dir(), reason="sample data not present")
def test_end_to_end_on_the_cs146_sample(tmp_path, monkeypatch) -> None:
    scans = tmp_path / "scans"
    shutil.copytree(_SAMPLES / "cs146-quiz1" / "split-submissions", scans)
    cohort = tmp_path / "cohort"
    shutil.copytree(_SAMPLES / "cs146-quiz1" / "student-submissions", cohort / "submissions")
    qa = tmp_path / "qa-multi.md"
    shutil.copy(_SAMPLES / "qa-multi.md", qa)

    extract_calls: list[list[str]] = []
    monkeypatch.setattr(
        submission_extractor,
        "run_cli",
        lambda argv, **_kwargs: extract_calls.append(list(argv)),
    )

    request = PipelineRequest(scan=scans, output_dir=cohort, qa_file=qa)
    first = run_pipeline(request)

    assert first.ok, [outcome.message for outcome in first.stages]
    assert first.stage(STAGE_SCAN_CHECK).status is StageStatus.RAN
    assert first.stage(STAGE_SPLIT).status is StageStatus.SKIPPED
    assert first.stage(STAGE_EXTRACT).status is StageStatus.RAN
    assert first.stage(STAGE_AUTO_GRADE).status is StageStatus.RAN
    assert first.submission_count == 2
    assert first.ready_to_grade
    assert extract_calls[0][:2] == ["--input", str(scans)]
    assert not (cohort / "split").exists()

    graded = (
        cohort / "submissions" / "submission-0001" / "submission-0001.submission.md"
    ).read_text(encoding="utf-8")
    assert "- Analysis: Correct" in graded

    extract_calls.clear()
    second = run_pipeline(request)
    assert second.stage(STAGE_SCAN_CHECK).status is StageStatus.REUSED
    assert second.stage(STAGE_SPLIT).status is StageStatus.SKIPPED
    assert second.stage(STAGE_AUTO_GRADE).status is StageStatus.REUSED
    assert len(extract_calls) == 1

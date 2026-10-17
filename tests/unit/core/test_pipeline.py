from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import fitz
import pytest

from opengrader.cli import automated_grading, submission_extractor, submission_splitter
from opengrader.core.pipeline import (
    PIPELINE_STAGES,
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    LlmOptions,
    PipelineProgress,
    PipelineRequest,
    StageStatus,
    auto_grade_pending,
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


def _stage_plan(request: PipelineRequest, stage: str):
    return next(plan for plan in plan_pipeline(request) if plan.stage == stage)


def test_plan_covers_every_stage_in_run_order(tmp_path) -> None:
    plans = plan_pipeline(_request(tmp_path))
    assert tuple(plan.stage for plan in plans) == PIPELINE_STAGES
    assert PIPELINE_STAGES == (
        STAGE_SCAN_CHECK,
        STAGE_SPLIT,
        STAGE_EXTRACT,
        STAGE_DRAFT_RUBRICS,
        STAGE_AUTO_GRADE,
    )


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


def test_split_never_sorts_pages_unless_asked(tmp_path) -> None:
    assert "--reorder" not in plan_pipeline(_request(tmp_path))[1].argv
    assert plan_pipeline(_request(tmp_path, reorder_pages=True))[1].argv[-1] == "--reorder"


def test_turning_reordering_on_redoes_the_split_but_leaving_it_off_does_not(tmp_path) -> None:
    from opengrader.core.pipeline import _stage_ledger_key

    request = _request(tmp_path)
    off = _stage_ledger_key(request, STAGE_SPLIT)

    assert _stage_ledger_key(replace(request, reorder_pages=True), STAGE_SPLIT) != off
    assert _stage_ledger_key(replace(request, reorder_pages=False), STAGE_SPLIT) == off


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


def test_a_region_map_reaches_the_extractor_and_only_the_extractor(tmp_path) -> None:
    region_map = tmp_path / "midterm.regions.json"
    request = _request(tmp_path, region_map=region_map)

    split, extract = plan_pipeline(request)[1:3]
    assert extract.argv[extract.argv.index("--region-map") + 1] == str(region_map)
    assert "--blank-template" not in extract.argv
    assert "--region-map" not in split.argv


def test_a_region_map_and_a_blank_template_together_are_refused(tmp_path) -> None:
    with pytest.raises(ValueError, match="one or the other"):
        _request(
            tmp_path,
            region_map=tmp_path / "midterm.regions.json",
            blank_template=tmp_path / "template.pdf",
        )


def test_section_rosters_replace_the_single_roster_one_id_file_each(tmp_path) -> None:
    request = _request(
        tmp_path,
        roster=tmp_path / "roster.tsv",
        section_rosters=("/rosters/mw.tsv=MW", "/rosters/tth.tsv"),
    )
    argv = _stage_plan(request, STAGE_EXTRACT).argv
    id_files = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--id-file"]
    assert id_files == ["/rosters/mw.tsv=MW", "/rosters/tth.tsv"]


def test_new_extract_options_change_the_ledger_key_only_when_used(tmp_path) -> None:
    from opengrader.core.pipeline import _stage_ledger_key

    mw = tmp_path / "mw.tsv"
    mw.write_text("1\tAda\n", encoding="utf-8")
    region_map = tmp_path / "midterm.regions.json"
    region_map.write_text("{}", encoding="utf-8")

    request = _request(tmp_path)
    plain = _stage_ledger_key(request, STAGE_EXTRACT)
    # Unused, they leave the key alone: a ledger from before they existed still matches.
    assert _stage_ledger_key(replace(request, section_rosters=()), STAGE_EXTRACT) == plain

    with_map = _stage_ledger_key(replace(request, region_map=region_map), STAGE_EXTRACT)
    as_mw = _stage_ledger_key(replace(request, section_rosters=(f"{mw}=MW",)), STAGE_EXTRACT)
    as_tth = _stage_ledger_key(replace(request, section_rosters=(f"{mw}=TTh",)), STAGE_EXTRACT)
    assert len({plain, with_map, as_mw, as_tth}) == 4


def test_refine_argv_is_the_extract_command_re_reading_instead_of_resuming(tmp_path) -> None:
    from opengrader.core.pipeline import refine_argv

    request = _request(tmp_path, roster=tmp_path / "roster.tsv")
    extract = _stage_plan(request, STAGE_EXTRACT).argv

    assert refine_argv(request) == (
        *[arg for arg in extract if arg != "--resume"],
        "--refine",
    )
    assert refine_argv(request, dpi=450)[-3:] == ("--refine", "--refine-dpi", "450")


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
        ),
    )
    argv = plan_pipeline(request)[2].argv
    assert "--llm-escalate" in argv
    assert "--llm-responses" not in argv
    assert argv[-4:] == (
        "--ollama-model",
        "qwen2.5vl:7b",
        "--ollama-host",
        "http://box:11434",
    )
    assert "--auto-ollama" not in argv


def test_auto_grade_argv_carries_the_text_model_and_the_confidence_floor(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(
        tmp_path,
        qa_file=qa,
        llm=LlmOptions(grade_written=True, text_model="llama3.1", min_confidence=0.8),
    )
    plan = _stage_plan(request, STAGE_AUTO_GRADE)
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
    assert "qwen2.5vl:7b" not in _stage_plan(request, STAGE_AUTO_GRADE).argv


def test_split_is_skipped_for_a_directory_of_already_split_pdfs(tmp_path) -> None:
    folder = tmp_path / "split-in"
    _pdf(folder / "submission-0001.pdf")
    plan = plan_pipeline(_request(tmp_path, scan=folder))[1]
    assert plan.will_run is False
    assert plan.status is StageStatus.SKIPPED
    assert "already a directory" in plan.reason


def test_auto_grade_is_skipped_without_an_answer_key(tmp_path) -> None:
    plan = _stage_plan(_request(tmp_path), STAGE_AUTO_GRADE)
    assert plan.status is StageStatus.SKIPPED
    assert "no answer key" in plan.reason


def test_auto_grade_is_skipped_when_turned_off(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    plan = _stage_plan(_request(tmp_path, qa_file=qa, auto_grade=False), STAGE_AUTO_GRADE)
    assert plan.status is StageStatus.SKIPPED


def test_auto_grading_is_pending_only_after_a_run_that_read_scans_without_it(
    tmp_path, stages, clean_scan
) -> None:
    cohort = tmp_path / "cohort"
    # Never run here: nothing to catch up on.
    assert auto_grade_pending(cohort) is False

    run_pipeline(_request(tmp_path))
    assert auto_grade_pending(cohort) is True

    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    run_pipeline(_request(tmp_path, qa_file=qa))
    assert auto_grade_pending(cohort) is False


def test_stages_run_in_order_and_report_what_they_did(tmp_path, stages, clean_scan) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    report = run_pipeline(_request(tmp_path, qa_file=qa))

    assert stages.stages() == [STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE]
    assert [outcome.stage for outcome in report.stages] == list(PIPELINE_STAGES)
    assert all(
        outcome.status is StageStatus.RAN
        for outcome in report.stages
        if outcome.stage != STAGE_DRAFT_RUBRICS
    )
    assert report.stage(STAGE_DRAFT_RUBRICS).status is StageStatus.SKIPPED
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


# ---------------------------------------------------------------------------
# There is no identity override left in the core: student names and IDs are
# never sent to the cloud, and there is no flag that changes that.
# ---------------------------------------------------------------------------


def test_pipeline_request_has_no_identity_override_field() -> None:
    assert "send_identity_confirmed" not in PipelineRequest.__dataclass_fields__
    with pytest.raises(TypeError):
        PipelineRequest(  # type: ignore[call-arg]
            scan=Path("scan.pdf"),
            output_dir=Path("out"),
            send_identity_confirmed=True,
        )


def test_split_and_extract_argv_never_carry_the_identity_flag(tmp_path) -> None:
    request = _request(
        tmp_path,
        send_to_cloud_confirmed=True,
        llm=LlmOptions(read_handwriting=True),
    )
    split_argv = plan_pipeline(request)[1].argv
    extract_argv = plan_pipeline(request)[2].argv
    # The splitter's own vision calls are whole-page reads (page numbers,
    # Name/ID headers) that GuardedVisionReader always keeps local, so a
    # split run never sends anything to the cloud — the pipeline doesn't
    # pass --yes-send-to-cloud to it, and the splitter's parser doesn't
    # accept it at all (see cli/submission_splitter.py).
    assert "--yes-send-to-cloud" not in split_argv
    assert "--yes-send-identity-to-cloud" not in split_argv
    assert "--yes-send-to-cloud" in extract_argv
    assert "--yes-send-identity-to-cloud" not in extract_argv


# ---------------------------------------------------------------------------
# The single cloud confirmation gate in run_pipeline, and the CloudPreflight
# it now exposes on the report for a caller (the TUI in particular) to read
# directly instead of parsing a BLOCKED outcome's detail text.
# ---------------------------------------------------------------------------

_CLOUD_HOST = "https://api.pipeline-cloud.example/v1"


@pytest.fixture
def cloud_spec():
    from opengrader.core import app_config

    original = app_config.registered_providers()
    spec = app_config.ProviderSpec(
        name="pipeline-cloud",
        label="Pipeline Cloud",
        summary="Test-only cloud provider.",
        default_host=_CLOUD_HOST,
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="PIPELINE_CLOUD_KEY",
        supports_model_listing=False,
        dialect="openai-chat",
    )
    app_config.register_provider(spec)
    try:
        yield spec
    finally:
        app_config._REGISTRY.clear()
        for spec_ in original:
            app_config.register_provider(spec_)


def _consent_to_send(spec) -> None:
    from opengrader.core import app_config
    from opengrader.core.app_config import consent

    settings = app_config.AISettings(provider=spec.name, read_handwriting=True)
    resolved = app_config.resolve(settings, environ={})
    settings = consent.grant_cloud_consent(settings, resolved)
    app_config.save_settings(settings)


def test_cloud_run_without_confirmation_is_blocked_and_carries_the_preflight(
    tmp_path, stages, cloud_spec
) -> None:
    _consent_to_send(cloud_spec)
    split_dir = tmp_path / "already-split"
    _pdf(split_dir / "submission-0001.pdf")
    request = _request(tmp_path, scan=split_dir, llm=LlmOptions(read_handwriting=True))

    report = run_pipeline(request)

    assert report.cloud_confirmation_needed is True
    assert report.cloud_preflight is not None
    assert report.cloud_preflight.host == _CLOUD_HOST
    assert report.cloud_preflight.sends_anything is True
    assert stages.calls == []


def test_a_confirmed_cloud_run_actually_runs_and_still_carries_the_preflight(
    tmp_path, stages, clean_scan, cloud_spec
) -> None:
    _consent_to_send(cloud_spec)
    split_dir = tmp_path / "already-split"
    _pdf(split_dir / "submission-0001.pdf")
    request = _request(
        tmp_path,
        scan=split_dir,
        llm=LlmOptions(read_handwriting=True),
        send_to_cloud_confirmed=True,
    )

    report = run_pipeline(request)

    assert report.cloud_confirmation_needed is False
    assert report.cloud_preflight is not None
    assert report.cloud_preflight.sends_anything is True
    assert stages.calls  # the extract stage actually ran


def _drafts(*, written: bool, failed: tuple[str, ...] = ()):
    from opengrader.cli.draft_rubric import DraftResult
    from opengrader.core.qa_file.markdown import QARubricFlatOption

    options = (
        QARubricFlatOption("Correct", 0.0, "correct", "exclusive"),
        QARubricFlatOption("Partially Correct - Missing base case", -3.0, "partial", "checklist"),
        QARubricFlatOption("Partially Correct - Off by one", -2.0, "partial", "checklist"),
        QARubricFlatOption("Incorrect", -5.0, "incorrect", "exclusive"),
    )
    results = [
        DraftResult("q2", "", "drafted", options=options, written=written),
    ]
    results += [DraftResult(qid, "", "failed", reason="no usable rubric") for qid in failed]
    return results


def _draft_stub(monkeypatch, results):
    from opengrader.cli import draft_rubric

    calls: list[list[str]] = []

    def run_cli(argv, **_kwargs):
        calls.append(list(argv))
        return results

    monkeypatch.setattr(draft_rubric, "run_cli", run_cli)
    return calls


def test_draft_rubrics_is_skipped_unless_requested(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    plan = _stage_plan(_request(tmp_path, qa_file=qa), STAGE_DRAFT_RUBRICS)
    assert plan.status is StageStatus.SKIPPED
    assert "not requested" in plan.reason

    plan = _stage_plan(_request(tmp_path, draft_rubrics=True), STAGE_DRAFT_RUBRICS)
    assert plan.status is StageStatus.SKIPPED
    assert "no answer key" in plan.reason


def test_draft_rubrics_argv_writes_and_carries_model_host_and_cloud_confirmation(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(
        tmp_path,
        qa_file=qa,
        draft_rubrics=True,
        send_to_cloud_confirmed=True,
        llm=LlmOptions(text_model="llama3.1", host="http://box:11434", vision_model="vl"),
    )
    plan = _stage_plan(request, STAGE_DRAFT_RUBRICS)
    assert plan.will_run is True
    assert plan.argv == (
        "draft-rubric",
        "--solutions",
        str(qa),
        "--input",
        str(tmp_path / "cohort" / "submissions"),
        "--write",
        "--llm-model",
        "llama3.1",
        "--llm-host",
        "http://box:11434",
        "--yes-send-to-cloud",
    )


def test_written_drafts_pause_before_auto_grading(
    tmp_path, stages, clean_scan, monkeypatch
) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    calls = _draft_stub(monkeypatch, _drafts(written=True, failed=("q4",)))

    report = run_pipeline(_request(tmp_path, qa_file=qa, draft_rubrics=True))

    assert len(calls) == 1
    assert STAGE_AUTO_GRADE not in stages.stages()
    outcome = report.stage(STAGE_DRAFT_RUBRICS)
    assert outcome.status is StageStatus.PAUSED
    assert "q2" in outcome.message and "run again" in outcome.message
    assert "Missing base case (-3)" in outcome.detail
    assert "could not draft: q4" in outcome.detail
    assert report.stage(STAGE_AUTO_GRADE) is None
    # Paused on purpose: not a failure, but flagged so nothing is published.
    assert report.ok
    assert report.paused_for_review


def test_nothing_left_to_draft_goes_straight_on_to_grading(
    tmp_path, stages, clean_scan, monkeypatch
) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    _draft_stub(monkeypatch, [])

    report = run_pipeline(_request(tmp_path, qa_file=qa, draft_rubrics=True))

    assert report.stage(STAGE_DRAFT_RUBRICS).status is StageStatus.RAN
    assert "already has partial-credit items" in report.stage(STAGE_DRAFT_RUBRICS).message
    assert stages.stages()[-1] == STAGE_AUTO_GRADE
    assert not report.paused_for_review


def test_failed_drafts_alone_do_not_pause(tmp_path, stages, clean_scan, monkeypatch) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    from opengrader.cli.draft_rubric import DraftResult

    _draft_stub(monkeypatch, [DraftResult("q2", "", "failed", reason="garbage")])

    report = run_pipeline(_request(tmp_path, qa_file=qa, draft_rubrics=True))

    outcome = report.stage(STAGE_DRAFT_RUBRICS)
    assert outcome.status is StageStatus.RAN
    assert "could not draft rubric items for q2" in outcome.message
    assert stages.stages()[-1] == STAGE_AUTO_GRADE


def _out_of_order(split_dir: Path, name: str = "submission-0001") -> Path:
    from opengrader.core.pages.order_review import OrderFinding, write_order_review

    pdf = _pdf(split_dir / f"{name}.pdf", pages=3)
    write_order_review(split_dir, [OrderFinding(name=name, keys=(1, 3, 2), warning="out of order")])
    return pdf


def _numbers(pdf: Path) -> list[str]:
    with fitz.open(str(pdf)) as doc:
        return [page.get_text().strip() for page in doc]


def test_a_submission_that_looks_out_of_order_pauses_the_run_before_extraction(
    tmp_path, stages, clean_scan
) -> None:
    request = _request(tmp_path)
    stages.split_count = 0
    pdf = _out_of_order(request.split_dir)

    report = run_pipeline(request)

    assert report.page_order_review_needed and report.paused_for_review and report.ok
    assert STAGE_EXTRACT not in stages.stages()
    assert "submission-0001.pdf" in report.stage(STAGE_EXTRACT).detail
    assert _numbers(pdf) == ["page 1", "page 2", "page 3"]


def test_once_the_order_is_answered_the_same_run_carries_on(tmp_path, stages, clean_scan) -> None:
    from opengrader.core.pages.order_review import keep_pages

    request = _request(tmp_path)
    stages.split_count = 0
    _out_of_order(request.split_dir)
    assert run_pipeline(request).page_order_review_needed

    keep_pages(request.split_dir, ["submission-0001"])
    report = run_pipeline(request)

    assert not report.page_order_review_needed
    assert stages.stages().count(STAGE_SPLIT) == 1
    assert STAGE_EXTRACT in stages.stages()


def test_keep_page_order_answers_up_front_and_moves_nothing(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path, keep_page_order=True)
    stages.split_count = 0
    pdf = _out_of_order(request.split_dir)

    report = run_pipeline(request)

    assert not report.paused_for_review and STAGE_EXTRACT in stages.stages()
    assert _numbers(pdf) == ["page 1", "page 2", "page 3"]


def test_reorder_pages_sorts_what_is_still_unanswered_instead_of_pausing(tmp_path, stages, clean_scan) -> None:
    request = _request(tmp_path, reorder_pages=True)
    stages.split_count = 0
    pdf = _out_of_order(request.split_dir)

    report = run_pipeline(request)

    assert not report.paused_for_review and STAGE_EXTRACT in stages.stages()
    assert _numbers(pdf) == ["page 1", "page 3", "page 2"]


def _split_five_plus_one(split_dir: Path) -> None:
    """What the splitter leaves for the tester's scan: a six-page assignment
    split 5 + 1, then a correct one, with its findings recorded."""
    from opengrader.core.pages.split_review import read_part, record_split_review

    for name, pages in (("submission-0001", 5), ("submission-0002", 1), ("submission-0003", 6)):
        _pdf(split_dir / f"{name}.pdf", pages=pages)
    names = ("submission-0001", "submission-0002", "submission-0003")
    record_split_review(split_dir, [read_part(split_dir, name) for name in names], 6)


@pytest.fixture
def five_plus_one_split(monkeypatch, stages):
    def split(argv, *, status_callback=None, **_kwargs) -> None:
        stages.calls.append((STAGE_SPLIT, list(argv)))
        _split_five_plus_one(Path(argv[argv.index("--output") + 1]))

    monkeypatch.setattr(submission_splitter, "run_cli", split)
    stages.split_count = 2
    return stages


def test_a_split_of_the_wrong_length_pauses_the_run_before_extraction(
    tmp_path, five_plus_one_split, clean_scan
) -> None:
    request = _request(tmp_path, pages_per_submission=6)

    report = run_pipeline(request)

    assert report.split_review_needed and report.paused_for_review and report.ok
    assert STAGE_EXTRACT not in five_plus_one_split.stages()
    detail = report.stage(STAGE_EXTRACT).detail
    assert "submission-0001: 5 page(s) where 6 are expected" in detail
    assert "Merge submission-0001 and submission-0002" in detail
    assert (request.split_dir / "submission-0002.pdf").exists()


def test_a_fix_applied_between_runs_is_not_undone_by_splitting_again(
    tmp_path, five_plus_one_split, clean_scan
) -> None:
    from opengrader.core.pipeline import answer_split_review

    request = _request(tmp_path, pages_per_submission=6)
    assert run_pipeline(request).split_review_needed

    assert answer_split_review(
        request, request.split_dir, fix=["submission-0001"], keep=["submission-0001"]
    ) == ["submission-0001"]
    report = run_pipeline(request)

    assert not report.paused_for_review
    # The split is reused: the ledger was told about the merge.
    assert five_plus_one_split.stages().count(STAGE_SPLIT) == 1
    assert STAGE_EXTRACT in five_plus_one_split.stages()
    assert not (request.split_dir / "submission-0002.pdf").exists()
    assert len(_numbers(request.split_dir / "submission-0001.pdf")) == 6


def test_fix_split_up_front_applies_the_proposed_fix_and_carries_on(
    tmp_path, five_plus_one_split, clean_scan
) -> None:
    request = _request(tmp_path, pages_per_submission=6, fix_split=True)

    report = run_pipeline(request)

    assert not report.paused_for_review
    assert STAGE_EXTRACT in five_plus_one_split.stages()
    assert not (request.split_dir / "submission-0002.pdf").exists()


def test_keep_split_up_front_changes_nothing_and_carries_on(
    tmp_path, five_plus_one_split, clean_scan
) -> None:
    request = _request(tmp_path, pages_per_submission=6, keep_split=True)

    report = run_pipeline(request)

    assert not report.paused_for_review
    assert (request.split_dir / "submission-0002.pdf").exists()


def test_fix_split_still_waits_for_a_problem_it_has_no_fix_for(
    tmp_path, stages, clean_scan
) -> None:
    from opengrader.core.pages.split_review import read_part, record_split_review

    request = _request(tmp_path, pages_per_submission=6, fix_split=True)
    stages.split_count = 0
    _pdf(request.split_dir / "submission-0001.pdf", pages=6)
    _pdf(request.split_dir / "submission-0002.pdf", pages=4)
    record_split_review(
        request.split_dir,
        [read_part(request.split_dir, f"submission-000{n}") for n in (1, 2)],
        6,
    )

    report = run_pipeline(request)

    assert report.split_review_needed
    assert "No fix can be proposed" in report.stage(STAGE_EXTRACT).detail


def test_a_template_checks_a_bundle_by_submission_length_not_its_whole_page_count(
    tmp_path, stages, monkeypatch
) -> None:
    seen: dict[str, object] = {}

    def analyze(_path, **kwargs):
        seen.update(kwargs)
        return _report()

    monkeypatch.setattr(scan_check_module, "analyze_scan_pdf", analyze)

    run_pipeline(_request(tmp_path, blank_template=_pdf(tmp_path / "blank.pdf", pages=2)))

    assert seen["pages_per_submission"] == 2 and seen["expected_page_count"] is None


# --- scans that need an answer key to find their questions -------------------


def _split_catalog(tmp_path, lines) -> None:
    """The page catalog a split by page matching leaves beside the split PDFs."""
    import numpy as np

    from opengrader.core.pages.catalog import (
        CATALOG_DIRNAME,
        PageCatalog,
        PageGroup,
        PageMatch,
        ScanPage,
        save_catalog,
    )
    from opengrader.core.pages.layout import page_layout
    from opengrader.core.pages.printed import TextLine

    page = ScanPage(tmp_path / "scans" / "bundle.pdf", 0)
    printed = [TextLine(text, (0.1, y, 0.6, y + 0.012)) for text, y in lines]
    group = PageGroup(id="g01", reference=page, reference_turns=0,
                      layout=page_layout(printed, printed_only=True), page_number=1, copies_read=2)
    save_catalog(
        PageCatalog(groups={"g01": group}, matches=[PageMatch(page, "g01", np.eye(3), 100)]),
        tmp_path / "cohort" / "split" / CATALOG_DIRNAME,
    )


_OWN_PAGES = [("1) What is the symbol for gold?", 0.2), ("2) Why does ice float?", 0.5)]
_PRINTED_PAGES = [("1. (5 points) What is the symbol for gold?", 0.2), ("2. (5 points) Why does ice float?", 0.5)]


def test_scans_of_your_own_assignment_wait_for_an_answer_key_after_the_split(
    tmp_path, stages, clean_scan
) -> None:
    _split_catalog(tmp_path, _OWN_PAGES)

    report = run_pipeline(_request(tmp_path))

    # Checked and split, so the wait costs only the reading.
    assert stages.stages() == [STAGE_SPLIT]
    assert report.answer_key_needed and report.ok and not report.ready_to_grade
    paused = report.stage(STAGE_EXTRACT)
    assert paused.status is StageStatus.PAUSED and "answer key" in paused.message
    assert not report.page_order_review_needed
    # Nothing recorded: the next run reads them.
    assert STAGE_EXTRACT not in read_manifest(tmp_path / "cohort", filename=RUN_LEDGER_FILENAME)


def test_with_the_answer_key_they_are_read_and_the_key_is_handed_to_the_reader(
    tmp_path, stages, clean_scan
) -> None:
    _split_catalog(tmp_path, _OWN_PAGES)
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")

    report = run_pipeline(_request(tmp_path, qa_file=qa))

    assert not report.answer_key_needed
    argv = stages.argv_for(STAGE_EXTRACT)
    assert argv[argv.index("--qa-file") + 1] == str(qa)


def test_a_blank_template_a_region_map_or_a_say_so_reads_them_without_a_key(
    tmp_path, stages, clean_scan
) -> None:
    _split_catalog(tmp_path, _OWN_PAGES)
    template = _pdf(tmp_path / "blank.pdf")
    region_map = tmp_path / "quiz.regions.json"
    region_map.write_text("{}", encoding="utf-8")

    for overrides in (
        {"blank_template": template},
        {"region_map": region_map},
        {"read_without_key": True},
    ):
        report = run_pipeline(_request(tmp_path, fresh=True, **overrides))
        assert not report.answer_key_needed, overrides
        assert report.stage(STAGE_EXTRACT).status is StageStatus.RAN, overrides


def test_scans_opengrader_printed_never_wait_for_a_key(tmp_path, stages, clean_scan) -> None:
    _split_catalog(tmp_path, _PRINTED_PAGES)

    report = run_pipeline(_request(tmp_path))

    assert not report.answer_key_needed
    assert report.stage(STAGE_EXTRACT).status is StageStatus.RAN


def test_the_answer_key_is_not_handed_over_where_something_else_finds_the_questions(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    template = _pdf(tmp_path / "blank.pdf")

    plain = _stage_plan(_request(tmp_path, qa_file=qa), STAGE_EXTRACT)
    with_template = _stage_plan(_request(tmp_path, qa_file=qa, blank_template=template), STAGE_EXTRACT)

    assert "--qa-file" in plain.argv
    assert "--qa-file" not in with_template.argv

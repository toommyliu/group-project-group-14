"""Preview in plain words first, the tools' plan second (core/pipeline_preview.py)."""

from __future__ import annotations

from pathlib import Path

from opengrader.core.pipeline import (
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    PipelineRequest,
    StagePlan,
    StageStatus,
)
from opengrader.core.pipeline_preview import describe_plan, technical_lines


def _request(tmp_path: Path, **overrides) -> PipelineRequest:
    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4\n")
    base = dict(scan=scan, output_dir=tmp_path, qa_file=tmp_path / "qa.md")
    base.update(overrides)
    return PipelineRequest(**base)  # type: ignore[arg-type]


def _plan(stage: str, *, run: bool = True, status=StageStatus.RAN, reason="no record", section=""):
    return StagePlan(
        stage=stage,
        will_run=run,
        reason=reason,
        argv=(f"{stage}-tool", "--input", "x") if stage != STAGE_SCAN_CHECK else (),
        status=status,
        section=section,
    )


FIRST_RUN = (
    _plan(STAGE_SCAN_CHECK),
    _plan(STAGE_SPLIT),
    _plan(STAGE_EXTRACT, reason="always runs; --resume skips"),
    _plan(STAGE_DRAFT_RUBRICS, run=False, status=StageStatus.SKIPPED),
    _plan(STAGE_AUTO_GRADE),
)


def test_a_first_run_is_one_sentence_then_a_checklist(tmp_path) -> None:
    preview = describe_plan(_request(tmp_path, pages_per_submission=4), FIRST_RUN)
    lines = preview.lines()

    assert lines[0] == (
        "Run Pipeline will check the scan for upside-down, blank or missing pages, "
        "split it into one PDF per student, read every answer and grade every "
        "answer the answer key settles."
    )
    assert "nothing has been run or changed" in lines[1]
    assert "▸ Read every answer — for the first time here" in lines
    assert "· Draft missing rubric items with AI — not asked for (optional, in Settings)" in lines
    assert preview.fixes == ()
    # No command, flag or stage id in the plain part.
    assert not any("--" in line or "auto-grade" in line for line in lines)


def test_what_holds_a_run_back_comes_with_the_easy_fix(tmp_path) -> None:
    plans = FIRST_RUN[:-1] + (
        _plan(STAGE_AUTO_GRADE, run=False, status=StageStatus.SKIPPED),
    )
    preview = describe_plan(_request(tmp_path, qa_file=None), plans)

    assert "· Grade what the answer key settles — waiting for an answer key" in preview.lines()
    assert any("Create answer key" in fix for fix in preview.fixes)
    assert any("Pages per submission" in fix for fix in preview.fixes)


def test_grading_turned_off_says_where_to_turn_it_on(tmp_path) -> None:
    plans = FIRST_RUN[:-1] + (
        _plan(STAGE_AUTO_GRADE, run=False, status=StageStatus.SKIPPED),
    )
    preview = describe_plan(
        _request(tmp_path, auto_grade=False, pages_per_submission=4), plans
    )

    assert any("AI setup" in fix for fix in preview.fixes)


def test_a_finished_folder_says_it_is_up_to_date(tmp_path) -> None:
    done = StageStatus.REUSED
    plans = (
        _plan(STAGE_SCAN_CHECK, run=False, status=done),
        _plan(STAGE_SPLIT, run=False, status=done),
        _plan(STAGE_EXTRACT, reason="always runs"),
        _plan(STAGE_DRAFT_RUBRICS, run=False, status=StageStatus.SKIPPED),
        _plan(STAGE_AUTO_GRADE, run=False, status=done),
    )
    lines = describe_plan(_request(tmp_path, pages_per_submission=4), plans).lines()

    assert lines[0].startswith("Everything is up to date.")
    assert "✓ Split it into one PDF per student — already done; kept from last time" in lines
    assert any(line.startswith("▸ Read every answer — only PDFs that are new") for line in lines)


def test_sections_are_named_and_grading_is_once_for_the_class(tmp_path) -> None:
    plans = tuple(
        _plan(stage, section=section)
        for section in ("Section 01", "Section 02")
        for stage in (STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT)
    ) + FIRST_RUN[3:]
    request = _request(tmp_path, scan=tmp_path, section_scans=("a.pdf", "b.pdf"))
    lines = describe_plan(request, plans).lines()

    assert lines[0].startswith("For each of the 2 sections, Run Pipeline will check the scan")
    assert "Then it will grade every answer the answer key settles, for the whole class at once." in lines[0]
    assert "▸ Section 02 · Split it into one PDF per student — for the first time here" in lines


def test_the_technical_details_keep_every_command(tmp_path) -> None:
    lines = technical_lines(FIRST_RUN, _request(tmp_path))

    assert any(line.strip() == "$ split-tool --input x" for line in lines)
    assert lines[-1] == f"class folder: {tmp_path}"

"""One scan per class section: each is checked, split and read on its own,
into one submissions folder graded together (`core/pipeline.section_requests`)."""

from __future__ import annotations

from pathlib import Path

import fitz
import pytest

from opengrader.cli import automated_grading, submission_extractor, submission_splitter
from opengrader.core.pipeline import (
    STAGE_AUTO_GRADE,
    STAGE_DRAFT_RUBRICS,
    STAGE_EXTRACT,
    STAGE_SCAN_CHECK,
    STAGE_SPLIT,
    PipelineRequest,
    StageStatus,
    auto_grade_pending,
    plan_pipeline,
    run_pipeline,
    section_requests,
)
from opengrader.core.submissions import scan_check as scan_check_module
from opengrader.core.submissions.manifest import RUN_LEDGER_FILENAME, read_manifest
from opengrader.core.submissions.scan_check import PageScanFeatures, build_scan_report


def _pdf(path: Path, pages: int = 2) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"page {index + 1}")
    doc.save(str(path))
    doc.close()
    return path


def _request(tmp_path: Path, **overrides) -> PipelineRequest:
    scans = tmp_path / "class" / "scans"
    base = dict(
        scan=scans,
        output_dir=tmp_path / "class",
        qa_file=None,
        section_scans=(
            str(_pdf(scans / "section-01.pdf")),
            str(_pdf(scans / "section-02.pdf")),
        ),
    )
    base.update(overrides)
    return PipelineRequest(**base)  # type: ignore[arg-type]


def _value(argv, flag: str) -> str | None:
    argv = list(argv)
    return argv[argv.index(flag) + 1] if flag in argv else None


class _Recorder:
    """Stage stand-ins that write where their arguments say, as the tools do."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str]]] = []

    def split(self, argv, **_kwargs) -> None:
        self.calls.append((STAGE_SPLIT, list(argv)))
        output = Path(_value(argv, "--output"))
        prefix = _value(argv, "--filename-prefix") or "submission"
        output.mkdir(parents=True, exist_ok=True)
        (output / f"{prefix}-0001.pdf").write_bytes(b"%PDF-1.4\n")

    def extract(self, argv, **_kwargs) -> None:
        self.calls.append((STAGE_EXTRACT, list(argv)))
        source = Path(_value(argv, "--input"))
        output = Path(_value(argv, "--output"))
        for pdf in source.glob("*.pdf"):
            folder = output / pdf.stem
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{pdf.stem}.submission.md").write_text("# stub\n", encoding="utf-8")

    def grade(self, argv, **_kwargs) -> None:
        self.calls.append((STAGE_AUTO_GRADE, list(argv)))


@pytest.fixture
def stages(monkeypatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(submission_splitter, "run_cli", recorder.split)
    monkeypatch.setattr(submission_extractor, "run_cli", recorder.extract)
    monkeypatch.setattr(automated_grading, "run_cli", recorder.grade)
    report = build_scan_report([PageScanFeatures(page=1)], source="scan.pdf")
    monkeypatch.setattr(
        scan_check_module, "analyze_scan_pdf", lambda *_args, **_kwargs: report
    )
    return recorder


def test_each_section_gets_its_own_request_named_after_its_file(tmp_path) -> None:
    sections = section_requests(_request(tmp_path))

    assert [item.section for item in sections] == ["Section 01", "Section 02"]
    assert [item.scan.name for item in sections] == ["section-01.pdf", "section-02.pdf"]
    assert all(not item.section_scans for item in sections)
    # Each section's split stays apart; the submissions are shared.
    assert sections[0].split_dir == tmp_path / "class" / "split" / "section-01"
    assert {item.submissions_dir for item in sections} == {
        tmp_path / "class" / "submissions"
    }


def test_a_named_section_wins_over_the_file_name(tmp_path) -> None:
    scan = _pdf(tmp_path / "morning.pdf")
    request = _request(tmp_path, section_scans=(f"{scan}=Lab A", f"{scan}=Lab B"))

    assert [item.section for item in section_requests(request)] == ["Lab A", "Lab B"]


def test_a_sections_roster_is_the_one_naming_the_same_section(tmp_path) -> None:
    one = tmp_path / "roster-sec-1.tsv"
    two = tmp_path / "anything.tsv"
    whole = tmp_path / "class.tsv"
    for path in (one, two, whole):
        path.write_text("1\tA\n", encoding="utf-8")
    request = _request(
        tmp_path,
        roster=whole,
        # "Section 1" by its file's name, and one named outright.
        section_rosters=(str(one), f"{two}=section 2"),
    )

    first, second = section_requests(request)
    assert first.roster == one
    assert second.roster == two
    assert first.section_rosters == () and second.section_rosters == ()


def test_a_section_without_its_own_roster_uses_the_class_roster(tmp_path) -> None:
    whole = tmp_path / "class.tsv"
    whole.write_text("1\tA\n", encoding="utf-8")

    assert {item.roster for item in section_requests(_request(tmp_path, roster=whole))} == {
        whole
    }


def test_two_scans_of_one_section_are_refused(tmp_path) -> None:
    scan = _pdf(tmp_path / "section-01.pdf")
    other = _pdf(tmp_path / "copy" / "Section 1.pdf")

    with pytest.raises(ValueError, match="both section"):
        section_requests(_request(tmp_path, section_scans=(str(scan), str(other))))


def test_split_folders_holding_the_same_file_names_are_refused(tmp_path) -> None:
    for name in ("section-01", "section-02"):
        _pdf(tmp_path / name / "student-1.pdf")
    request = _request(
        tmp_path,
        section_scans=(str(tmp_path / "section-01"), str(tmp_path / "section-02")),
    )

    with pytest.raises(ValueError, match="overwrite each other"):
        section_requests(request)


def test_the_plan_reads_each_section_then_grades_the_class_once(tmp_path) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    plans = plan_pipeline(_request(tmp_path, qa_file=qa))

    assert [plan.stage for plan in plans] == [
        STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT,
        STAGE_SCAN_CHECK, STAGE_SPLIT, STAGE_EXTRACT,
        STAGE_DRAFT_RUBRICS, STAGE_AUTO_GRADE,
    ]
    assert plans[0].reason.startswith("Section 01: ")
    assert plans[3].reason.startswith("Section 02: ")
    split = plans[1].argv
    assert _value(split, "--output") == str(tmp_path / "class" / "split" / "section-01")
    assert _value(split, "--filename-prefix") == "section-01"
    extract = plans[5].argv
    assert _value(extract, "--input") == str(tmp_path / "class" / "split" / "section-02")
    assert _value(extract, "--output") == str(tmp_path / "class" / "submissions")
    assert _value(extract, "--section") == "Section 02"
    assert _value(plans[-1].argv, "--input") == str(tmp_path / "class" / "submissions")


def test_a_run_reads_every_section_into_one_folder_and_resumes(tmp_path, stages) -> None:
    qa = tmp_path / "qa.md"
    qa.write_text("# key\n", encoding="utf-8")
    request = _request(tmp_path, qa_file=qa)

    report = run_pipeline(request)

    assert report.ok, report.stages
    assert [stage for stage, _argv in stages.calls] == [
        STAGE_SPLIT, STAGE_EXTRACT, STAGE_SPLIT, STAGE_EXTRACT, STAGE_AUTO_GRADE,
    ]
    assert report.submission_count == 2
    extracts = [o for o in report.stages if o.stage == STAGE_EXTRACT]
    # Each section counts its own students, not the shared folder's.
    assert [o.message for o in extracts] == [
        "Section 01: 1 submission(s) ready to grade",
        "Section 02: 1 submission(s) ready to grade",
    ]
    ledger = read_manifest(request.output_dir, filename=RUN_LEDGER_FILENAME)
    assert {"split:section-01", "split:section-02", "extract:section-01"} <= set(ledger)

    stages.calls.clear()
    again = run_pipeline(request)
    assert [o.status for o in again.stages if o.stage == STAGE_SPLIT] == [
        StageStatus.REUSED,
        StageStatus.REUSED,
    ]
    assert again.stage(STAGE_AUTO_GRADE).status is StageStatus.REUSED


def test_a_bad_section_list_fails_the_run_before_anything_is_written(tmp_path, stages) -> None:
    scan = _pdf(tmp_path / "section-01.pdf")
    request = _request(tmp_path, section_scans=(str(scan), str(scan)))

    report = run_pipeline(request)

    assert not report.ok
    assert report.stages[0].status is StageStatus.FAILED
    assert stages.calls == []
    assert not (tmp_path / "class" / "split").exists()


def test_auto_grading_is_pending_after_sections_were_read_without_it(
    tmp_path, stages
) -> None:
    request = _request(tmp_path)  # no answer key: nothing to grade against
    run_pipeline(request)

    assert auto_grade_pending(request.output_dir)


def test_review_dirs_are_each_sections_split_folder(tmp_path) -> None:
    request = _request(tmp_path)

    assert request.review_dirs == (
        tmp_path / "class" / "split" / "section-01",
        tmp_path / "class" / "split" / "section-02",
    )

import subprocess
import sys
from pathlib import Path

import fitz
import pytest

from opengrader.core.submissions.batch import (
    AUTO_WORKER_CAP,
    VLM_AUTO_WORKER_CAP,
    ExtractionJob,
    document_outputs,
    resolve_worker_count,
    run_extraction_batch,
)
from opengrader.core.submissions.processor import ExtractionProgress
from opengrader.core.submissions.student_file import (
    StudentSubmissionDocument,
    StudentSubmissionQuestion,
)


def _job(tmp_path: Path, name: str) -> ExtractionJob:
    return ExtractionJob(
        pdf_path=tmp_path / "scans" / f"{name}.pdf",
        output_dir=tmp_path / "out",
        source_sha256=f"sha-{name}",
    )


def _document(student_id: str = "111", name: str = "Ann Lee") -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name=name,
        student_id=student_id,
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="x.pdf",
        questions=[],
    )


def _builder(tmp_path: Path, *, fails: set[str] = frozenset()):
    seen: list[str] = []

    def build(**kwargs):
        stem = Path(kwargs["pdf_path"]).stem
        seen.append(stem)
        if stem in fails:
            raise ValueError(f"bad scan: {stem}")
        return _document(), Path(kwargs["output_dir"]) / stem / f"{stem}.submission.md"

    build.seen = seen  # type: ignore[attr-defined]
    return build


def test_single_job_never_starts_a_pool() -> None:
    assert resolve_worker_count(None, 1, vlm_active=False) == 1
    assert resolve_worker_count(16, 1, vlm_active=False) == 1


def test_auto_never_exceeds_the_job_count() -> None:
    assert resolve_worker_count(None, 3, vlm_active=False) == 3


def test_auto_respects_the_plain_cap(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 64)
    assert resolve_worker_count(None, 100, vlm_active=False) == AUTO_WORKER_CAP


def test_auto_uses_the_lower_cap_when_the_vision_model_is_active(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 64)
    assert resolve_worker_count(None, 100, vlm_active=True) == VLM_AUTO_WORKER_CAP


def test_auto_never_exceeds_the_cpu_count(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert resolve_worker_count(None, 100, vlm_active=False) == 2


def test_explicit_request_overrides_the_cap(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: 2)
    assert resolve_worker_count(32, 100, vlm_active=False) == 32


def test_unknown_cpu_count_falls_back_to_one(monkeypatch) -> None:
    monkeypatch.setattr("os.cpu_count", lambda: None)
    assert resolve_worker_count(None, 100, vlm_active=False) == 1


def test_empty_batch_reports_nothing(tmp_path) -> None:
    report = run_extraction_batch([], workers=4)
    assert report.outcomes == [] and report.workers == 0


def test_every_job_is_extracted_once(tmp_path) -> None:
    build = _builder(tmp_path)
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 4)]
    report = run_extraction_batch(jobs, workers=1, build=build)
    assert build.seen == ["submission-0001", "submission-0002", "submission-0003"]
    assert [o.status for o in report.outcomes] == ["extracted"] * 3


def test_outcome_carries_identity_and_source_hash(tmp_path) -> None:
    build = _builder(tmp_path)
    report = run_extraction_batch([_job(tmp_path, "submission-0001")], build=build)
    outcome = report.outcomes[0]
    assert outcome.student_id == "111"
    assert outcome.identity == "Ann Lee (111)"
    assert outcome.source_sha256 == "sha-submission-0001"


def test_unidentified_submission_says_so(tmp_path) -> None:
    def build(**kwargs):
        stem = Path(kwargs["pdf_path"]).stem
        return _document(student_id="", name=""), Path(kwargs["output_dir"]) / f"{stem}.md"

    report = run_extraction_batch([_job(tmp_path, "s1")], build=build)
    assert report.outcomes[0].identity == "Unidentified"


def test_a_failing_scan_becomes_a_failed_outcome_not_an_exception(tmp_path) -> None:
    build = _builder(tmp_path, fails={"submission-0002"})
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 4)]
    report = run_extraction_batch(jobs, workers=1, build=build)
    assert [o.status for o in report.outcomes] == ["extracted", "failed", "extracted"]
    assert "bad scan: submission-0002" in report.failed[0].error
    assert len(report.extracted) == 2


def test_fail_fast_stops_after_the_first_failure(tmp_path) -> None:
    build = _builder(tmp_path, fails={"submission-0001"})
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 4)]
    report = run_extraction_batch(jobs, workers=1, build=build, fail_fast=True)
    assert build.seen == ["submission-0001"]
    assert len(report.outcomes) == 1


def test_on_outcome_fires_per_submission(tmp_path) -> None:
    build = _builder(tmp_path)
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 4)]
    seen: list[str] = []
    run_extraction_batch(jobs, workers=1, build=build, on_outcome=lambda o: seen.append(o.key))
    assert seen == ["submission-0001", "submission-0002", "submission-0003"]


def test_a_custom_builder_forces_single_process(tmp_path) -> None:
    build = _builder(tmp_path)
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 5)]
    report = run_extraction_batch(jobs, workers=4, build=build)
    assert report.workers == 1


def test_the_interactive_resolver_forces_single_process(tmp_path) -> None:
    build = _builder(tmp_path)
    jobs = [_job(tmp_path, f"submission-{n:04d}") for n in range(1, 5)]
    report = run_extraction_batch(
        jobs, workers=4, build=build, resolve_ambiguous=lambda *_a: None
    )
    assert report.workers == 1


def test_the_resolver_reaches_the_builder(tmp_path) -> None:
    captured: dict = {}

    def build(**kwargs):
        captured.update(kwargs)
        return _document(), Path(kwargs["output_dir"]) / "x.md"

    resolver = lambda *_a: None  # noqa: E731
    run_extraction_batch([_job(tmp_path, "s1")], build=build, resolve_ambiguous=resolver)
    assert captured["resolve_ambiguous"] is resolver


def test_progress_and_status_reach_the_caller_in_process(tmp_path) -> None:
    ticks: list[tuple[str, str]] = []
    statuses: list[str] = []

    def build(**kwargs):
        kwargs["progress"].file_start("s1.pdf", 2)
        kwargs["progress"].response("s1.pdf", "q1")
        kwargs["progress_callback"]("Extracting from text layer...")
        return _document(), Path(kwargs["output_dir"]) / "x.md"

    run_extraction_batch(
        [_job(tmp_path, "s1")],
        build=build,
        progress=ExtractionProgress(on_response=lambda *a: ticks.append(a)),
        status_callback=statuses.append,
    )
    assert ticks == [("s1.pdf", "q1")]
    assert statuses == ["Extracting from text layer..."]


def test_document_outputs_lists_markdown_and_every_crop(tmp_path) -> None:
    document = StudentSubmissionDocument(
        student_name="Ann",
        student_id="1",
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="x.pdf",
        name_image_path="identifiers/name.png",
        id_image_path="identifiers/id.png",
        questions=[
            StudentSubmissionQuestion(
                question_id="q1",
                prompt="p",
                possible_points=1.0,
                response_image_path="responses/q1.png",
                response_text="",
            ),
            StudentSubmissionQuestion(
                question_id="q2",
                prompt="p",
                possible_points=1.0,
                response_image_path=None,
                response_text="typed",
            ),
        ],
    )
    markdown = tmp_path / "sub" / "sub.submission.md"
    assert document_outputs(tmp_path, document, markdown) == (
        "sub/sub.submission.md",
        "sub/identifiers/name.png",
        "sub/identifiers/id.png",
        "sub/responses/q1.png",
    )


def _text_layer_pdf(path: Path, student_id: str, name: str) -> None:
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "CS146 Quiz 1")
    page.insert_text((72, 96), f"Name: {name}")
    page.insert_text((72, 112), f"ID: {student_id}")
    page.insert_text((72, 160), "1. (10 points) What is a stack?")
    page.insert_text((72, 180), "A LIFO data structure.")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def spawned_batch(tmp_path):
    scans = tmp_path / "scans"
    out = tmp_path / "out"
    scans.mkdir()
    people = [("111111111", "Ann Lee"), ("222222222", "Bob Kim"), ("333333333", "Cara Diaz")]
    for index, (student_id, name) in enumerate(people, start=1):
        _text_layer_pdf(scans / f"submission-{index:04d}.pdf", student_id, name)

    jobs = [
        ExtractionJob(pdf_path=pdf, output_dir=out, source_sha256=f"sha-{pdf.stem}")
        for pdf in sorted(scans.glob("*.pdf"))
    ]
    return people, out, run_extraction_batch(jobs, workers=2)


def test_workers_extract_every_submission_correctly(spawned_batch) -> None:
    people, out, report = spawned_batch
    assert report.workers == 2
    assert [o.status for o in report.outcomes] == ["extracted"] * 3
    for (student_id, name), outcome in zip(people, report.outcomes):
        assert outcome.student_id == student_id
        assert outcome.student_name == name
        text = Path(outcome.markdown_path).read_text()
        assert "A LIFO data structure." in text


def test_outcomes_come_back_in_input_order(spawned_batch) -> None:
    _people, _out, report = spawned_batch
    assert [o.key for o in report.outcomes] == [
        "submission-0001",
        "submission-0002",
        "submission-0003",
    ]


def test_worker_outputs_are_recorded_relative_to_the_output_dir(spawned_batch) -> None:
    _people, _out, report = spawned_batch
    assert report.outcomes[0].outputs == (
        "submission-0001/submission-0001.submission.md",
    )


def test_status_messages_cross_the_process_boundary(tmp_path) -> None:
    scans = tmp_path / "scans"
    scans.mkdir()
    for index in range(1, 3):
        _text_layer_pdf(scans / f"submission-{index:04d}.pdf", f"{index}" * 9, f"S{index}")

    statuses: list[str] = []
    jobs = [
        ExtractionJob(pdf_path=pdf, output_dir=tmp_path / "out")
        for pdf in sorted(scans.glob("*.pdf"))
    ]
    run_extraction_batch(jobs, workers=2, status_callback=statuses.append)
    assert any("text layer" in message for message in statuses)


def test_a_broken_pdf_fails_only_its_own_submission(tmp_path) -> None:
    scans = tmp_path / "scans"
    scans.mkdir()
    _text_layer_pdf(scans / "submission-0001.pdf", "111111111", "Ann Lee")
    (scans / "submission-0002.pdf").write_bytes(b"not a pdf at all")

    jobs = [
        ExtractionJob(pdf_path=pdf, output_dir=tmp_path / "out")
        for pdf in sorted(scans.glob("*.pdf"))
    ]
    report = run_extraction_batch(jobs, workers=2)
    assert [o.status for o in report.outcomes] == ["extracted", "failed"]
    assert report.failed[0].error


_STDERR_LIKE_TEXTUAL = r"""
import sys
from concurrent.futures import ProcessPoolExecutor
import multiprocessing

from opengrader.core.submissions.batch import _ensure_resource_tracker


class PrintCapture:  # what Textual puts in sys.stderr while an app runs
    def write(self, text):
        return len(text)

    def flush(self):
        pass

    def fileno(self):
        return -1


if __name__ == "__main__":  # spawn re-imports this script in the worker
    capture = PrintCapture()
    sys.stderr = capture
    try:
        _ensure_resource_tracker()
        assert sys.stderr is capture
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(1, mp_context=context) as pool:
            print(pool.submit(abs, -7).result())
    except BaseException:
        import traceback

        traceback.print_exc(file=sys.__stderr__)
        raise
"""


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX resource tracker only")
def test_spawn_pool_starts_while_stderr_has_no_file_descriptor(tmp_path):
    # 2026-11-05: Launch Grading in classic failed at extract with
    # "ValueError: bad value(s) in fds_to_keep" — Textual's stderr has
    # fileno() == -1. A fresh interpreter, so no tracker is running yet.
    script = tmp_path / "spawn_check.py"
    script.write_text(_STDERR_LIKE_TEXTUAL)
    result = subprocess.run(
        [sys.executable, str(script)], capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "7"

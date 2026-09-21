import builtins
from argparse import ArgumentTypeError
from dataclasses import replace
from pathlib import Path

import pytest

import opengrader.cli.submission_extractor as extractor_cli
import opengrader.core.submissions.batch as batch_module
from opengrader.core import ai_config
from opengrader.cli.submission_extractor import (
    _parse_jobs,
    _parse_roster_arg,
    _prompt_for_duplicate_name,
    run_cli,
)
from opengrader.core.submissions import ExtractionProgress
from opengrader.core.submissions.manifest import read_manifest
from opengrader.core.submissions.processor import _RosterEntry
from opengrader.core.submissions.student_file import StudentSubmissionDocument

CANDIDATES = (
    _RosterEntry(student_id="333333333", name="John Smith"),
    _RosterEntry(student_id="444444444", name="John Smith"),
)


def _doc() -> StudentSubmissionDocument:
    return StudentSubmissionDocument(
        student_name="John Smith",
        student_id="",
        course_code=None,
        assignment_title="Quiz",
        assignment_version=None,
        source_pdf="submission-0003.pdf",
        questions=[],
    )


def test_prompt_returns_selected_candidate(monkeypatch) -> None:
    monkeypatch.setattr(builtins, "input", lambda _p="": "2")
    chosen = _prompt_for_duplicate_name(_doc(), CANDIDATES)
    assert chosen is not None and chosen.student_id == "444444444"


def test_prompt_skip_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(builtins, "input", lambda _p="": "s")
    assert _prompt_for_duplicate_name(_doc(), CANDIDATES) is None


def test_prompt_reprompts_on_invalid_then_accepts(monkeypatch) -> None:
    answers = iter(["9", "abc", "1"])
    monkeypatch.setattr(builtins, "input", lambda _p="": next(answers))
    chosen = _prompt_for_duplicate_name(_doc(), CANDIDATES)
    assert chosen is not None and chosen.student_id == "333333333"


def test_prompt_eof_returns_none(monkeypatch) -> None:
    def _raise(_p=""):
        raise EOFError

    monkeypatch.setattr(builtins, "input", _raise)
    assert _prompt_for_duplicate_name(_doc(), CANDIDATES) is None


def _stub_build(monkeypatch, captured: dict) -> None:
    def fake_build(**kwargs):
        captured.update(kwargs)
        return _doc(), Path(kwargs["output_dir"]) / "x.submission.md"

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(extractor_cli, "_load_blank_assignment_template", lambda _p: object())
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)


def _scan_dir(tmp_path: Path) -> Path:
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    return scans


def test_llm_responses_flag_enables_vlm_in_template_mode(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
        "--llm-responses",
    ])

    assert captured["llm_responses"] is True
    assert captured["vision_reader"] is not None
    assert isinstance(captured["progress"], ExtractionProgress)


def test_template_mode_without_flag_has_no_ollama(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
    ])

    assert captured["llm_responses"] is False
    assert captured["vision_reader"] is None


def test_injected_progress_is_passed_through(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")

    sentinel = ExtractionProgress()
    statuses: list[str] = []
    run_cli(
        ["--input", str(tmp_path), "--output", str(tmp_path / "out"), "--no-ollama-qa"],
        progress=sentinel,
        status_callback=statuses.append,
    )
    assert captured["progress"] is sentinel


def _write_roster(path: Path, rows: list[tuple[str, str]]) -> None:
    path.write_text("".join(f"{sid}\t{name}\n" for sid, name in rows))


def test_parse_roster_arg_plain_path(tmp_path) -> None:
    roster = tmp_path / "roster.tsv"
    roster.write_text("111\tAnn\n")
    path, name = _parse_roster_arg(str(roster))
    assert path == roster and name is None


def test_parse_roster_arg_named_section() -> None:
    path, name = _parse_roster_arg("rosters/mw.tsv=SectionA")
    assert path == Path("rosters/mw.tsv") and name == "SectionA"


def test_parse_roster_arg_keeps_existing_path_with_equals(tmp_path) -> None:
    weird = tmp_path / "a=b.tsv"
    weird.write_text("111\tAnn\n")
    path, name = _parse_roster_arg(str(weird))
    assert path == weird and name is None


def test_single_named_roster_sets_default_section(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    roster = tmp_path / "roster.tsv"
    _write_roster(roster, [("111111111", "Ann Lee")])

    run_cli([
        "--input", str(tmp_path),
        "--output", str(tmp_path / "out"),
        "--id-file", f"{roster}=SectionA",
        "--no-ollama-qa",
    ])

    assert captured["default_section"] == "SectionA"
    assert captured["section_by_id"] is None


def test_single_bare_roster_has_no_section(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    roster = tmp_path / "roster.tsv"
    _write_roster(roster, [("111111111", "Ann Lee")])

    run_cli([
        "--input", str(tmp_path),
        "--output", str(tmp_path / "out"),
        "--id-file", str(roster),
        "--no-ollama-qa",
    ])

    assert captured["default_section"] is None
    assert captured["section_by_id"] is None


def test_explicit_section_flag_sets_default_section(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(tmp_path),
        "--output", str(tmp_path / "out"),
        "--section", "SectionB",
        "--no-ollama-qa",
    ])

    assert captured["default_section"] == "SectionB"
    assert captured["section_by_id"] is None


def test_multiple_rosters_build_section_map(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    mw = tmp_path / "mw.tsv"
    tth = tmp_path / "tth.tsv"
    _write_roster(mw, [("111111111", "Ann Lee")])
    _write_roster(tth, [("222222222", "Bob Kim")])

    run_cli([
        "--input", str(tmp_path),
        "--output", str(tmp_path / "out"),
        "--id-file", str(mw),
        "--id-file", f"{tth}=Thursday",
        "--no-ollama-qa",
    ])

    assert captured["default_section"] is None
    assert captured["section_by_id"] == {
        "111111111": "mw",
        "222222222": "Thursday",
    }


def test_jobs_accepts_auto_and_positive_integers() -> None:
    assert _parse_jobs("auto") is None
    assert _parse_jobs("0") is None
    assert _parse_jobs(" 4 ") == 4


def test_jobs_rejects_nonsense() -> None:
    for bad in ("-1", "half", ""):
        with pytest.raises(ArgumentTypeError):
            _parse_jobs(bad)


def test_interactive_roster_forces_one_job(monkeypatch, tmp_path, capsys) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    monkeypatch.setattr(extractor_cli.sys.stdin, "isatty", lambda: True)
    scans = _scan_dir(tmp_path)
    roster = tmp_path / "roster.tsv"
    _write_roster(roster, [("111111111", "Ann Lee")])

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--id-file", str(roster),
        "--interactive-roster",
        "--jobs", "8",
        "--no-ollama-qa",
    ])

    assert "runs with --jobs 1" in capsys.readouterr().err


def _stub_writing_build(monkeypatch, calls: list[str]) -> None:
    def fake_build(**kwargs):
        stem = Path(kwargs["pdf_path"]).stem
        calls.append(stem)
        submission_dir = Path(kwargs["output_dir"]) / stem
        submission_dir.mkdir(parents=True, exist_ok=True)
        markdown = submission_dir / f"{stem}.submission.md"
        markdown.write_text("stub")
        return _doc(), markdown

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)


def _extract(scans: Path, out: Path, *extra: str) -> None:
    run_cli(["--input", str(scans), "--output", str(out), "--no-ollama-qa", *extra])


def test_manifest_is_written_even_without_resume(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)

    assert set(read_manifest(out)) == {"submission-0001"}


def test_resume_skips_an_unchanged_submission(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    _extract(scans, out, "--resume")

    assert calls == ["submission-0001"]


def test_without_resume_everything_is_re_extracted(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    _extract(scans, out)

    assert calls == ["submission-0001", "submission-0001"]


def test_resume_re_extracts_when_the_scan_changes(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\nrescanned\n")
    _extract(scans, out, "--resume")

    assert len(calls) == 2


def test_resume_re_extracts_when_a_setting_changes(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    _extract(scans, out, "--resume", "--dpi", "150")

    assert len(calls) == 2


def test_resume_re_extracts_when_an_output_was_deleted(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    (out / "submission-0001" / "submission-0001.submission.md").unlink()
    _extract(scans, out, "--resume")

    assert len(calls) == 2


def test_resume_keeps_a_graded_submission(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out)
    graded = out / "submission-0001" / "submission-0001.submission.md"
    graded.write_text("Points Awarded: 8")
    _extract(scans, out, "--resume")

    assert calls == ["submission-0001"]
    assert graded.read_text() == "Points Awarded: 8"


def test_a_failed_scan_exits_nonzero_but_lets_the_others_finish(
    monkeypatch, tmp_path, capsys
) -> None:
    calls: list[str] = []

    def fake_build(**kwargs):
        stem = Path(kwargs["pdf_path"]).stem
        calls.append(stem)
        if stem == "submission-0001":
            raise ValueError("unreadable scan")
        return _doc(), Path(kwargs["output_dir"]) / f"{stem}.submission.md"

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)
    scans = _scan_dir(tmp_path)
    (scans / "submission-0002.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans),
            "--output", str(tmp_path / "out"),
            "--no-ollama-qa",
            "--jobs", "1",
        ])

    assert exit_info.value.code == 1
    assert calls == ["submission-0001", "submission-0002"]
    stderr = capsys.readouterr().err
    assert "1 of 2 submission(s) failed" in stderr
    assert "--resume" in stderr


def test_section_flag_with_multiple_rosters_errors(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    (tmp_path / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    mw = tmp_path / "mw.tsv"
    tth = tmp_path / "tth.tsv"
    _write_roster(mw, [("111111111", "Ann Lee")])
    _write_roster(tth, [("222222222", "Bob Kim")])

    with pytest.raises(SystemExit):
        run_cli([
            "--input", str(tmp_path),
            "--output", str(tmp_path / "out"),
            "--section", "SectionA",
            "--id-file", str(mw),
            "--id-file", str(tth),
            "--no-ollama-qa",
        ])


_SAMPLES = Path(__file__).resolve().parents[3] / "samples"
_MC_QA = _SAMPLES / "cmpe195a-quiz2-multiple-choice/qa.md"


def _fill_bubble_sheet(source_pdf: Path, layout, marks: dict, dest: Path) -> None:  # noqa: ANN001
    import fitz

    doc = fitz.open(str(source_pdf))
    page = doc[0]
    width, height = page.rect.width, page.rect.height
    for row in layout.rows:
        wanted = marks.get(row.question_id)
        if not wanted:
            continue
        wanted = (wanted,) if isinstance(wanted, str) else wanted
        for bubble in row.bubbles:
            if bubble.choice_label in wanted:
                page.draw_circle(
                    fitz.Point(bubble.cx * width, bubble.cy * height),
                    bubble.radius * width * 0.92,
                    color=(0, 0, 0), fill=(0, 0, 0), width=0,
                )
    doc.save(str(dest))
    doc.close()


def test_bubble_sheet_mode_extracts_marks_without_ocr_or_a_vision_model(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from opengrader.core.bubble_sheet import BubbleQuestion
    from opengrader.core.pdf.templates.bubble_sheet import generate_bubble_sheet_pdf
    from opengrader.core.qa_file import load_qa_markdown
    from opengrader.core.qa_file.markdown import qa_to_bubble_questions
    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    questions = qa_to_bubble_questions(load_qa_markdown(_MC_QA))
    blank = tmp_path / "blank.pdf"
    layout = generate_bubble_sheet_pdf(blank, questions, title="Quiz", version="A")

    scans = tmp_path / "scans"
    scans.mkdir()
    _fill_bubble_sheet(blank, layout, {"q1": "B", "q2a": "B", "q3": "C"},
                       scans / "submission-0001.pdf")

    out_dir = tmp_path / "out"
    run_cli([
        "--input", str(scans),
        "--output", str(out_dir),
        "--bubble-sheet", str(_MC_QA),
        "--jobs", "1",
    ])

    document = load_student_submission_markdown(
        out_dir / "submission-0001" / "submission-0001.submission.md"
    )
    answers = {q.question_id: q.response_text for q in document.questions}
    assert answers["q1"] == "B"
    assert answers["q2a"] == "B"
    assert answers["q3"] == "C"
    assert answers["q2b"] == ""
    assert [q.question_id for q in document.questions] == ["q1", "q2a", "q2b", "q3"]

    captured = capsys.readouterr()
    assert "Ollama" not in captured.out


def test_bubble_sheet_mode_carries_points_and_title_from_the_qa_file(
    tmp_path: Path,
) -> None:
    from opengrader.core.pdf.templates.bubble_sheet import generate_bubble_sheet_pdf
    from opengrader.core.qa_file import load_qa_markdown
    from opengrader.core.qa_file.markdown import qa_to_bubble_questions
    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    questions = qa_to_bubble_questions(load_qa_markdown(_MC_QA))
    blank = tmp_path / "blank.pdf"
    layout = generate_bubble_sheet_pdf(blank, questions, title="Quiz", version="A")
    scans = tmp_path / "scans"
    scans.mkdir()
    _fill_bubble_sheet(blank, layout, {"q1": "A"}, scans / "s1.pdf")

    out_dir = tmp_path / "out"
    run_cli([
        "--input", str(scans), "--output", str(out_dir),
        "--bubble-sheet", str(_MC_QA), "--jobs", "1",
    ])

    document = load_student_submission_markdown(
        out_dir / "s1" / "s1.submission.md"
    )
    assert document.assignment_title.startswith("CMPE195A")
    points = {q.question_id: q.possible_points for q in document.questions}
    assert points == {"q1": 10.0, "q2a": 8.0, "q2b": 12.0, "q3": 10.0}


def test_bubble_sheet_mode_rejects_a_qa_file_with_no_multiple_choice(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "s1.pdf").write_bytes(b"%PDF-1.4\n")
    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans), "--output", str(tmp_path / "out"),
            "--bubble-sheet", str(_SAMPLES / "cmpe195a-quiz1/qa.md"),
        ])
    assert exit_info.value.code == 1
    assert "no multiple-choice questions" in capsys.readouterr().err


def test_the_layout_not_just_the_flag_invalidates_a_resumed_run(tmp_path: Path) -> None:
    from opengrader.cli.submission_extractor import _bubble_layout_fingerprint
    from opengrader.core.bubble_sheet import BubbleQuestion, build_bubble_sheet_layout

    three = build_bubble_sheet_layout(
        [BubbleQuestion(question_id=f"q{i}") for i in range(1, 4)]
    )
    four = build_bubble_sheet_layout(
        [BubbleQuestion(question_id=f"q{i}") for i in range(1, 5)]
    )
    assert _bubble_layout_fingerprint(three) != _bubble_layout_fingerprint(four)
    assert _bubble_layout_fingerprint(three) == _bubble_layout_fingerprint(
        build_bubble_sheet_layout(
            [BubbleQuestion(question_id=f"q{i}") for i in range(1, 4)]
        )
    )
    assert _bubble_layout_fingerprint(None) == ""


def _scrambled_batch(tmp_path: Path, students: int = 3):
    import fitz

    from opengrader.core.pdf.templates.bubble_sheet import generate_bubble_sheet_batch
    from opengrader.core.qa_file import load_qa_markdown
    from opengrader.core.qa_file.markdown import qa_to_bubble_questions
    from opengrader.core.roster import RosterStudent

    questions = qa_to_bubble_questions(load_qa_markdown(_MC_QA))
    roster = [
        RosterStudent(student_id=f"10000{i}", name=f"Student {i}")
        for i in range(1, students + 1)
    ]
    batch = tmp_path / "batch.pdf"
    layout, forms = generate_bubble_sheet_batch(
        batch, questions, title="Quiz", students=roster, shuffle=True, version="A"
    )

    from opengrader.core.bubble_sheet import build_bubble_sheet_layout, shuffle_questions

    key = {"q1": "B", "q2a": "B", "q3": "C"}
    scans = tmp_path / "scans"
    scans.mkdir()
    doc = fitz.open(str(batch))
    for index, form in enumerate(forms):
        page = doc[index + 1]
        w, h = page.rect.width, page.rect.height
        sheet = build_bubble_sheet_layout(shuffle_questions(questions, form.form_key))
        for row in sheet.rows:
            for bubble in row.bubbles:
                if bubble.choice_label == key[row.question_id]:
                    page.draw_circle(
                        fitz.Point(bubble.cx * w, bubble.cy * h),
                        bubble.radius * w * 0.9,
                        color=(0, 0, 0), fill=(0, 0, 0), width=0,
                    )
        out = fitz.open()
        out.insert_pdf(doc, from_page=index + 1, to_page=index + 1)
        out.save(str(scans / f"submission-{index + 1:04d}.pdf"))
        out.close()
    doc.close()
    return scans, forms, key


def test_a_scrambled_batch_grades_identically_for_every_form(tmp_path: Path) -> None:
    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    scans, forms, key = _scrambled_batch(tmp_path)
    assert len({form.question_order for form in forms}) > 1, "forms must differ"

    out_dir = tmp_path / "out"
    run_cli([
        "--input", str(scans), "--output", str(out_dir),
        "--bubble-sheet", str(_MC_QA), "--jobs", "1",
    ])

    for md in sorted(out_dir.glob("*/*.submission.md")):
        document = load_student_submission_markdown(md)
        answers = {
            q.question_id: q.response_text for q in document.questions if q.response_text
        }
        assert answers == key, md.parent.name


def test_a_scrambled_batch_is_detected_without_being_told(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    scans, _forms, _key = _scrambled_batch(tmp_path)
    run_cli([
        "--input", str(scans), "--output", str(tmp_path / "out"),
        "--bubble-sheet", str(_MC_QA), "--jobs", "1",
    ])
    assert "printed with --shuffle" in capsys.readouterr().out


def test_a_scrambled_sheet_with_an_unreadable_form_key_is_refused(
    tmp_path: Path,
) -> None:
    import fitz

    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    scans, _forms, _key = _scrambled_batch(tmp_path)
    damaged = sorted(scans.glob("*.pdf"))[0]
    doc = fitz.open(str(damaged))
    page = doc[0]
    page.draw_rect(
        fitz.Rect(page.rect.width - 110, 40, page.rect.width - 34, 115),
        color=(1, 1, 1), fill=(1, 1, 1), width=0,
    )
    doc.saveIncr()
    doc.close()

    out_dir = tmp_path / "out"
    run_cli([
        "--input", str(scans), "--output", str(out_dir),
        "--bubble-sheet", str(_MC_QA), "--jobs", "1",
    ])

    document = load_student_submission_markdown(
        out_dir / damaged.stem / f"{damaged.stem}.submission.md"
    )
    assert [q.question_id for q in document.questions] == ["q1", "q2a", "q2b", "q3"]
    assert all(q.response_text == "" for q in document.questions)
    assert all(q.response_confidence is None for q in document.questions)

    other = sorted(scans.glob("*.pdf"))[1]
    neighbour = load_student_submission_markdown(
        out_dir / other.stem / f"{other.stem}.submission.md"
    )
    assert any(q.response_text for q in neighbour.questions)


def test_a_form_key_read_off_the_paper_rescues_the_sheet(tmp_path: Path) -> None:
    import fitz

    from opengrader.core.submissions.student_file import (
        load_student_submission_markdown,
    )

    scans, forms, key = _scrambled_batch(tmp_path)
    damaged = sorted(scans.glob("*.pdf"))[0]
    doc = fitz.open(str(damaged))
    page = doc[0]
    page.draw_rect(
        fitz.Rect(page.rect.width - 110, 40, page.rect.width - 34, 115),
        color=(1, 1, 1), fill=(1, 1, 1), width=0,
    )
    doc.saveIncr()
    doc.close()

    out_dir = tmp_path / "out"
    run_cli([
        "--input", str(damaged), "--output", str(out_dir),
        "--bubble-sheet", str(_MC_QA), "--bubble-shuffled",
        "--bubble-form", forms[0].form_key, "--jobs", "1",
    ])

    document = load_student_submission_markdown(
        out_dir / damaged.stem / f"{damaged.stem}.submission.md"
    )
    answers = {
        q.question_id: q.response_text for q in document.questions if q.response_text
    }
    assert answers == key


def _refine_submission(
    out: Path,
    stem: str,
    *,
    confidences: dict[str, float | None],
    graded: str | None = None,
) -> Path:
    directory = out / stem
    directory.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        'student_name: "Tommy Liu"',
        'student_id: "123456789"',
        'course_code: "CS146"',
        'assignment_title: "Quiz 1"',
        'assignment_version: "A"',
        f'source_pdf: "{stem}.pdf"',
        'student_score: ""',
        "---",
        "",
    ]
    for question_id, confidence in confidences.items():
        lines += [
            f"- {question_id}:",
            "  - Question type: Free Response",
            "  - Prompt: ",
            f"  - Response image: ![{question_id} response](responses/{question_id}.png)",
        ]
        if confidence is not None:
            lines.append(f"  - Confidence: {confidence:g}")
        lines += [
            f"  - Response text: original {question_id}",
            "  - Possible points: 10",
            f"  - Points: {8 if question_id == graded else ''}",
            "  - Rubric:",
            f"  - Analysis: {'solid reasoning' if question_id == graded else ''}",
            "",
        ]
    path = directory / f"{stem}.submission.md"
    path.write_text("\n".join(lines))
    return path


def _stub_refine_build(monkeypatch, *, readings: dict[str, tuple[str, float | None]], calls: list[str]):
    from opengrader.core.submissions.student_file import (
        StudentSubmissionQuestion,
        render_student_submission_markdown,
    )

    def fake_build(**kwargs):
        stem = Path(kwargs["pdf_path"]).stem
        calls.append(stem)
        submission_dir = Path(kwargs["output_dir"]) / stem
        submission_dir.mkdir(parents=True, exist_ok=True)
        markdown = submission_dir / f"{stem}.submission.md"
        document = StudentSubmissionDocument(
            student_name="Tommy Liu",
            student_id="123456789",
            course_code="CS146",
            assignment_title="Quiz 1",
            assignment_version="A",
            source_pdf=f"{stem}.pdf",
            questions=tuple(
                StudentSubmissionQuestion(
                    question_id=question_id,
                    prompt="",
                    possible_points=10,
                    response_image_path=f"responses/{question_id}.png",
                    response_text=text,
                    points_awarded=None,
                    rubric_labels=(),
                    question_type="free_response",
                    response_confidence=confidence,
                )
                for question_id, (text, confidence) in readings.items()
            ),
        )
        markdown.write_text(render_student_submission_markdown(document))
        return document, markdown

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)


def test_refine_rewrites_only_the_low_confidence_answer(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    path = _refine_submission(
        out, "submission-0001", confidences={"q1": 21.0, "q2": 90.0}, graded="q1"
    )
    before = path.read_text()
    _stub_refine_build(
        monkeypatch,
        readings={"q1": ("a clearer reading", 75.0), "q2": ("TOTALLY DIFFERENT", 99.0)},
        calls=calls,
    )

    _extract(scans, out, "--refine")

    after = path.read_text()
    assert "a clearer reading" in after
    assert "TOTALLY DIFFERENT" not in after
    assert "original q2" in after
    assert "  - Points: 8" in after
    assert "solid reasoning" in after
    assert before.count("- q") == after.count("- q")


def test_refine_skips_submissions_with_nothing_below_the_floor(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    _refine_submission(out, "submission-0001", confidences={"q1": 92.0})
    _stub_refine_build(monkeypatch, readings={"q1": ("x", 99.0)}, calls=calls)

    _extract(scans, out, "--refine")

    assert calls == []


def test_refine_keeps_a_worse_reading_out(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    path = _refine_submission(out, "submission-0001", confidences={"q1": 45.0})
    _stub_refine_build(monkeypatch, readings={"q1": ("worse", 30.0)}, calls=calls)

    _extract(scans, out, "--refine")

    assert calls == ["submission-0001"]
    assert "original q1" in path.read_text()


def test_refine_dry_run_writes_nothing(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    path = _refine_submission(out, "submission-0001", confidences={"q1": 21.0})
    before = path.read_text()
    _stub_refine_build(monkeypatch, readings={"q1": ("better", 80.0)}, calls=calls)

    _extract(scans, out, "--refine", "--refine-dry-run")

    assert calls == ["submission-0001"]
    assert path.read_text() == before
    assert not (out / ".opengrader" / "history").exists()


def test_refine_never_touches_the_resume_ledger(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    _refine_submission(out, "submission-0001", confidences={"q1": 21.0})
    _stub_refine_build(monkeypatch, readings={"q1": ("better", 80.0)}, calls=calls)

    _extract(scans, out, "--refine")

    assert read_manifest(out) == {}


def test_refine_journals_under_its_own_actor(monkeypatch, tmp_path) -> None:
    from opengrader.core.history import read_journal, resolve_history_dir

    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    _refine_submission(out, "submission-0001", confidences={"q1": 21.0})
    _stub_refine_build(monkeypatch, readings={"q1": ("better", 80.0)}, calls=calls)

    _extract(scans, out, "--refine")

    entries = read_journal(resolve_history_dir(out))
    assert [entry.actor for entry in entries] == [extractor_cli.JOURNAL_ACTOR_REFINE]
    assert entries[0].kind == "transcription"
    assert entries[0].old["response_text"] == "original q1"
    assert entries[0].new["response_text"] == "better"


def test_refine_dpi_reaches_the_extraction(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    _refine_submission(out, "submission-0001", confidences={"q1": 21.0})

    from opengrader.core.submissions.student_file import (
        StudentSubmissionQuestion,
        render_student_submission_markdown,
    )

    def fake_build(**kwargs):
        captured.update(kwargs)
        stem = Path(kwargs["pdf_path"]).stem
        directory = Path(kwargs["output_dir"]) / stem
        directory.mkdir(parents=True, exist_ok=True)
        markdown = directory / f"{stem}.submission.md"
        document = StudentSubmissionDocument(
            student_name="", student_id="", course_code=None,
            assignment_title="", assignment_version=None, source_pdf="",
            questions=(
                StudentSubmissionQuestion(
                    question_id="q1", prompt="", possible_points=10,
                    response_image_path=None, response_text="sharper",
                    points_awarded=None, rubric_labels=(),
                    question_type="free_response", response_confidence=88.0,
                ),
            ),
        )
        markdown.write_text(render_student_submission_markdown(document))
        return document, markdown

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)

    _extract(scans, out, "--refine", "--refine-dpi", "450")

    assert captured["dpi"] == 450
    assert Path(captured["output_dir"]) != out


def test_refine_leaves_no_scratch_directory_behind(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    staged: list[Path] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    _refine_submission(out, "submission-0001", confidences={"q1": 21.0})

    _stub_refine_build(monkeypatch, readings={"q1": ("better", 80.0)}, calls=calls)
    original_mkdtemp = extractor_cli.tempfile.mkdtemp

    def record_mkdtemp(*args, **kwargs):
        path = Path(original_mkdtemp(*args, **kwargs))
        staged.append(path)
        return str(path)

    monkeypatch.setattr(extractor_cli.tempfile, "mkdtemp", record_mkdtemp)

    _extract(scans, out, "--refine")

    assert staged and not staged[0].exists()


def test_refine_model_adopts_a_model_reading_and_clears_the_confidence(
    monkeypatch, tmp_path
) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    path = _refine_submission(out, "submission-0001", confidences={"q1": 21.0})
    _stub_refine_build(
        monkeypatch, readings={"q1": ("a vision model's words", None)}, calls=calls
    )

    _extract(scans, out, "--refine", "--refine-model", "qwen2.5vl:32b")

    after = path.read_text()
    assert "a vision model's words" in after
    assert "Confidence:" not in after


def test_a_model_reading_is_refused_without_the_escalation(monkeypatch, tmp_path) -> None:
    calls: list[str] = []
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    path = _refine_submission(out, "submission-0001", confidences={"q1": 21.0})
    _stub_refine_build(monkeypatch, readings={"q1": ("unmeasured words", None)}, calls=calls)

    _extract(scans, out, "--refine")

    after = path.read_text()
    assert "original q1" in after
    assert "Confidence: 21" in after


def test_refine_refuses_to_combine_with_resume(tmp_path, capsys) -> None:
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["--input", str(scans), "--output", str(out), "--refine", "--resume"])

    assert exit_info.value.code == 1
    assert "--refine cannot be combined with --resume" in capsys.readouterr().err


def test_refine_knobs_warn_without_refine(monkeypatch, tmp_path, capsys) -> None:
    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"

    _extract(scans, out, "--refine-dpi", "400")

    assert "--refine-dpi does nothing without --refine" in capsys.readouterr().err


def test_llm_escalate_reaches_the_builder_and_implies_transcription(
    monkeypatch, tmp_path
) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
        "--llm-escalate",
    ])

    assert captured["escalate_floor"] == 60.0
    assert captured["llm_responses"] is True
    assert captured["vision_reader"] is not None


def test_escalate_confidence_overrides_the_floor(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--llm-escalate",
        "--escalate-confidence", "75",
    ])

    assert captured["escalate_floor"] == 75.0


def test_no_escalation_by_default(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)

    run_cli(["--input", str(scans), "--output", str(tmp_path / "out")])

    assert captured["escalate_floor"] is None


def test_an_out_of_range_floor_is_refused(monkeypatch, tmp_path, capsys) -> None:
    _stub_build(monkeypatch, {})
    scans = _scan_dir(tmp_path)

    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans),
            "--output", str(tmp_path / "out"),
            "--llm-escalate",
            "--escalate-confidence", "120",
        ])

    assert exit_info.value.code == 1
    assert "--escalate-confidence must be between 0 and 100" in capsys.readouterr().err


def test_escalate_confidence_alone_warns(monkeypatch, tmp_path, capsys) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--escalate-confidence", "75",
    ])

    assert captured["escalate_floor"] is None
    assert "does nothing without --llm-escalate" in capsys.readouterr().err


def test_the_floor_is_in_the_resume_fingerprint(monkeypatch, tmp_path) -> None:
    from opengrader.core.submissions.manifest import read_manifest

    scans = _scan_dir(tmp_path)
    output = tmp_path / "out"

    captured: dict = {}
    _stub_build(monkeypatch, captured)
    run_cli(["--input", str(scans), "--output", str(output)])
    plain_id = next(iter(read_manifest(output).values())).settings_id

    run_cli([
        "--input", str(scans),
        "--output", str(output),
        "--llm-escalate",
    ])
    escalated_id = next(iter(read_manifest(output).values())).settings_id

    assert plain_id != escalated_id


def test_the_run_reports_which_readers_produced_the_answers(
    monkeypatch, tmp_path, capsys
) -> None:
    from opengrader.core.submissions.student_file import StudentSubmissionQuestion

    def fake_build(**kwargs):
        document = _doc()
        document.questions.extend(
            [
                StudentSubmissionQuestion(
                    question_id="q1",
                    prompt="",
                    possible_points=None,
                    response_image_path="responses/q1.png",
                    response_text="read by ocr",
                    response_source="tesseract",
                ),
                StudentSubmissionQuestion(
                    question_id="q2",
                    prompt="",
                    possible_points=None,
                    response_image_path="responses/q2.png",
                    response_text="read by the model",
                    response_source="vision-model",
                ),
            ]
        )
        return document, Path(kwargs["output_dir"]) / "x.submission.md"

    monkeypatch.setattr(
        batch_module, "build_student_submission_document_unified", fake_build
    )
    monkeypatch.setattr(ai_config, "ensure_ready", lambda *_a, **_k: None)
    scans = _scan_dir(tmp_path)

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--llm-escalate",
    ])

    out = capsys.readouterr().out
    assert "Readings: Tesseract OCR 1, vision model 1" in out
    assert "Escalated 1 of 2 answers to a model (below 60% OCR confidence)." in out


def test_llm_responses_defaults_on_from_ai_setup(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
    ])

    assert captured["llm_responses"] is True
    assert captured["vision_reader"] is not None


def test_llm_responses_defaults_off_when_ai_setup_leaves_it_off(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(ai_config.AISettings())
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
    ])

    assert captured["llm_responses"] is False
    assert captured["vision_reader"] is None


def test_explicit_no_llm_responses_beats_ai_setup(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(ai_config.AISettings(read_handwriting=True))
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
        "--no-llm-responses",
    ])

    assert captured["llm_responses"] is False
    assert captured["vision_reader"] is None


def test_llm_escalate_defaults_on_from_ai_setup(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, escalate_only=True)
    )
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)

    run_cli(["--input", str(scans), "--output", str(tmp_path / "out")])

    assert captured["escalate_floor"] == 60.0
    assert captured["llm_responses"] is True


def test_llm_math_defaults_on_from_ai_setup(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, transcribe_math=True)
    )
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
    ])

    assert captured["llm_math"] is True


def test_explicit_no_llm_math_beats_ai_setup(monkeypatch, tmp_path) -> None:
    ai_config.save_settings(
        ai_config.AISettings(read_handwriting=True, transcribe_math=True)
    )
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--blank-template", str(blank),
        "--no-llm-math",
    ])

    assert captured["llm_math"] is False


def _region_map_exam(tmp_path: Path, *, pages: int = 1) -> Path:
    import fitz
    from PIL import Image

    exam = tmp_path / "exam.pdf"
    doc = fitz.open()
    for index in range(pages):
        raster = tmp_path / f"exam-{index}.png"
        Image.new("RGB", (400, 520), (250 - index, 250, 250)).save(raster)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, filename=str(raster))
    doc.save(str(exam))
    doc.close()
    return exam


def _region_map_file(exam: Path, *, points: float = 10.0) -> Path:
    from opengrader.core.region_map import (
        MappedQuestion,
        RegionBox,
        blank_region_map,
        default_region_map_path,
        save_region_map,
    )

    region_map = blank_region_map(exam, assignment_title="Paper Exam")
    region_map = replace(
        region_map,
        name_field=RegionBox(1, (0.55, 0.03, 0.4, 0.06)),
        student_id_field=RegionBox(1, (0.08, 0.03, 0.4, 0.06)),
        questions=(
            MappedQuestion("q1", 1, 1, (0.08, 0.30, 0.84, 0.20), possible_points=points),
        ),
    )
    return save_region_map(region_map, default_region_map_path(exam))


def test_region_map_and_blank_template_together_are_refused(tmp_path, capsys) -> None:
    scans = _scan_dir(tmp_path)
    exam = _region_map_exam(tmp_path)
    blank = tmp_path / "blank.pdf"
    blank.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans),
            "--output", str(tmp_path / "out"),
            "--blank-template", str(blank),
            "--region-map", str(_region_map_file(exam)),
        ])

    assert exit_info.value.code == 1
    stderr = capsys.readouterr().err
    assert "--blank-template and --region-map" in stderr
    assert "only one may be given" in stderr


def test_the_region_map_becomes_the_structural_template(monkeypatch, tmp_path) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    exam = _region_map_exam(tmp_path)

    run_cli([
        "--input", str(scans),
        "--output", str(tmp_path / "out"),
        "--region-map", str(_region_map_file(exam)),
        "--no-ollama-qa",
    ])

    template = captured["blank_assignment_template"]
    assert template is not None
    assert template.exact_regions is True
    assert template.assignment_title == "Paper Exam"
    assert template.questions[0].source_segments[0].bbox_norm == (0.08, 0.30, 0.84, 0.20)


def test_a_region_map_with_an_error_finding_is_refused(monkeypatch, tmp_path, capsys) -> None:
    import json

    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    exam = _region_map_exam(tmp_path)
    map_path = _region_map_file(exam)

    data = json.loads(map_path.read_text())
    data["questions"][0]["bbox"] = [0.5, 0.5, 0.0, 0.0]
    map_path.write_text(json.dumps(data))

    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans),
            "--output", str(tmp_path / "out"),
            "--region-map", str(map_path),
        ])

    assert exit_info.value.code == 1
    assert "region-map validate" in capsys.readouterr().err


def test_a_region_map_for_another_exam_is_refused(monkeypatch, tmp_path, capsys) -> None:
    captured: dict = {}
    _stub_build(monkeypatch, captured)
    scans = _scan_dir(tmp_path)
    exam = _region_map_exam(tmp_path)
    map_path = _region_map_file(exam)

    other_dir = tmp_path / "other"
    other_dir.mkdir()
    other = _region_map_exam(other_dir, pages=2)

    with pytest.raises(SystemExit) as exit_info:
        run_cli([
            "--input", str(scans),
            "--output", str(tmp_path / "out"),
            "--region-map", str(map_path),
            "--region-map-input", str(other),
        ])

    assert exit_info.value.code == 1
    assert "region map" in capsys.readouterr().err


def test_the_region_map_contents_are_in_the_resume_fingerprint(monkeypatch, tmp_path) -> None:
    from opengrader.core.submissions.manifest import read_manifest

    calls: list[str] = []
    _stub_writing_build(monkeypatch, calls)
    scans, out = _scan_dir(tmp_path), tmp_path / "out"
    exam = _region_map_exam(tmp_path)
    map_path = _region_map_file(exam, points=10.0)

    _extract(scans, out, "--region-map", str(map_path))
    first_id = next(iter(read_manifest(out).values())).settings_id

    _region_map_file(exam, points=25.0)
    _extract(scans, out, "--region-map", str(map_path), "--resume")
    second_id = next(iter(read_manifest(out).values())).settings_id

    assert first_id != second_id
    assert calls == ["submission-0001", "submission-0001"]

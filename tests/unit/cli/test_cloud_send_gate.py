"""`--yes-send-to-cloud` and the no-consent refusal, across every child CLI
the pipeline can invoke with a cloud AI provider selected.

Two things are checked for each CLI that can actually send to the cloud:

1. Its argparse parser accepts `--yes-send-to-cloud` (a pure parsing check —
   this is what would have caught the pipeline appending a flag no parser
   declared). The one exception is the splitter: every vision call it makes
   is a whole-page read that GuardedVisionReader always keeps local, so it
   never sends anything and its parser does not accept the flag at all —
   see `test_submission_splitter_parser_does_not_accept_the_flag` and
   `test_splitter_sends_nothing_to_the_cloud_without_the_flag` below.
2. A cloud provider with no consent recorded raises CloudConsentRequired /
   CloudDisabledError from app_config.vision_reader()/text_generate()/
   ensure_ready(), and the CLI turns that into one clear line plus exit 2 —
   never a bare traceback.

No network anywhere: every case below monkeypatches the app_config call that
would reach a provider, so nothing is actually sent.
"""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from opengrader.cli import (
    answer_key,
    automated_grading,
    llm_eval,
    submission_extractor,
    submission_splitter,
)
from opengrader.core import app_config
from opengrader.core.cloud_preflight import CloudConfirmationNeeded

SOLUTIONS_MD = """\
---
title: |
  Test Exam
instructions: Answer all questions.
questions_start_page: 1
---

- (10 points) What is 2 + 2?
  - **Correct**
    - 4
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
"""

SUBMISSION_MD = """\
---
student_name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Test Exam"
source_pdf: "student.pdf"
student_score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: What is 2 + 2?
  - Response text: 4
  - Possible points:
  - Points:
  - Rubric:
  - Analysis:
"""

# llm-eval only evaluates open-ended answers a human has already graded (a
# non-empty Rubric label) that a rule can't already settle by itself — a
# stack-vs-queue essay question rather than the numeric one above.
EVAL_SOLUTIONS_MD = """\
---
title: Quiz
---

- (4 points) Explain the difference between a stack and a queue.
  - **Correct**
    - LIFO vs FIFO
  - **Rubric**
    - Correct (+0)
    - Partial (-2)
    - Incorrect (-4)
"""

EVALUATED_SUBMISSION_MD = """\
---
name: "Test Student"
student_id: "123456789"
course_code: "CS146"
assignment_title: "Quiz"
assignment_version: null
source_pdf: "student.pdf"
score: ""
---


- q1:
  - Question type: Free Response
  - Prompt: p
  - Response text: stacks are FIFO and queues are LIFO
  - Possible points: 4
  - Points: 4
  - Rubric: Correct
  - Analysis:
"""


def _raise_consent_required(*_args, **_kwargs):
    raise app_config.CloudConsentRequired("not consented for this test")


# ---------------------------------------------------------------------------
# 1. every child CLI's parser accepts --yes-send-to-cloud
# ---------------------------------------------------------------------------


def test_submission_splitter_parser_does_not_accept_the_flag() -> None:
    # The splitter's only vision calls are whole-page reads (page numbers,
    # Name/ID headers), which GuardedVisionReader always keeps local — a
    # split run never sends anything to the cloud, so unlike every other
    # child CLI here it does not accept --yes-send-to-cloud at all.
    parser = submission_splitter._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            ["--input", "in.pdf", "--output", "out", "--yes-send-to-cloud"]
        )


def test_submission_extractor_parser_accepts_the_flag(tmp_path: Path) -> None:
    # submission_extractor builds its parser inline in run_cli rather than
    # exposing a _build_parser(). If the flag were unrecognized, argparse
    # itself would exit(2) before the input path is even looked at; here it
    # gets past parsing and fails on the (deliberately) missing input
    # instead, which proves the flag was accepted.
    with pytest.raises(FileNotFoundError):
        submission_extractor.run_cli(
            [
                "--input", str(tmp_path / "does-not-exist"),
                "--output", str(tmp_path / "out"),
                "--yes-send-to-cloud",
            ]
        )


def test_automated_grading_parser_accepts_the_flag() -> None:
    parser = automated_grading.setup_args()
    args = parser.parse_args(
        ["--solutions", "s.md", "--input", "i.md", "--yes-send-to-cloud"]
    )
    assert args.yes_send_to_cloud is True


def test_answer_key_parser_accepts_the_flag() -> None:
    parser = answer_key._build_parser()
    args = parser.parse_args(
        ["--input", "in.pdf", "--output", "out.md", "--yes-send-to-cloud"]
    )
    assert args.yes_send_to_cloud is True


def test_llm_eval_parser_accepts_the_flag() -> None:
    parser = llm_eval._build_parser()
    args = parser.parse_args(
        [
            "--solutions", "s.md",
            "--input", "i/",
            "--yes-send-to-cloud",
        ]
    )
    assert args.yes_send_to_cloud is True


# ---------------------------------------------------------------------------
# 2. missing cloud consent refuses (exit 2, one clear line) instead of a
#    traceback
# ---------------------------------------------------------------------------


def test_splitter_vision_fallback_exits_2_on_missing_consent(monkeypatch, capsys) -> None:
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)
    args = Namespace(
        ollama_host="https://cloud.example/v1",
        ollama_model="a-vision-model",
        ollama_timeout=5.0,
    )
    with pytest.raises(SystemExit) as excinfo:
        submission_splitter._verified_vision_reader(args, lambda _m: None)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "opengrader providers consent" in err


def test_answer_key_pdf_path_exits_2_on_missing_consent(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)
    # Kept local-kind (the default Ollama host) so the flow reaches
    # app_config.vision_reader() directly rather than first tripping the
    # separate --yes-send-to-cloud pre-flight gate (a different code path,
    # covered by its own tests) — --ollama-model differing from the default
    # is what makes _qa_from_pdf build a vision reader at all.
    ai = app_config.resolve()
    args = Namespace(
        llm_read_scans=True,
        auto_ollama=False,
        ollama_model="a-different-model",
        ollama_host=ai.host,
        ollama_timeout=5.0,
        title=None,
        instructions=None,
        blank_template=None,
        yes_send_to_cloud=False,
    )
    messages: list[str] = []
    with pytest.raises(SystemExit) as excinfo:
        answer_key._qa_from_pdf(tmp_path / "key.pdf", args, messages.append, None)
    assert excinfo.value.code == 2
    assert any("opengrader providers consent" in message for message in messages)


def test_extractor_exits_2_on_missing_consent(tmp_path: Path, monkeypatch, capsys) -> None:
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")
    monkeypatch.setattr(app_config, "vision_reader", _raise_consent_required)

    with pytest.raises(SystemExit) as excinfo:
        submission_extractor.run_cli(
            ["--input", str(scans), "--output", str(tmp_path / "out")]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "opengrader providers consent" in err


def test_automated_grading_exits_2_on_missing_consent(tmp_path: Path, monkeypatch, capsys) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)
    monkeypatch.setattr(app_config, "ensure_ready", _raise_consent_required)

    with pytest.raises(SystemExit) as excinfo:
        automated_grading.run_cli(
            [
                "--solutions", str(solutions_path),
                "--input", str(submission_path),
                "--llm",
            ]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "opengrader providers consent" in err


def test_llm_eval_exits_2_on_missing_consent(tmp_path: Path, monkeypatch, capsys) -> None:
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(EVAL_SOLUTIONS_MD)
    submissions = tmp_path / "subs"
    submissions.mkdir()
    (submissions / "s1.submission.md").write_text(EVALUATED_SUBMISSION_MD)
    monkeypatch.setattr(app_config, "ensure_ready", _raise_consent_required)

    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(
            ["--solutions", str(solutions_path), "--input", str(submissions)]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert "opengrader providers consent" in err


# ---------------------------------------------------------------------------
# 3. the standalone --yes-send-to-cloud pre-flight itself, exercised against
#    a real (network-free) cloud provider registration rather than a
#    monkeypatched exception — this is what would have caught
#    cli_confirm_cloud_send's signature changing out from under these calls.
# ---------------------------------------------------------------------------

_GATE_CLOUD_HOST = "https://api.gate-test.example/v1"


@pytest.fixture
def gate_cloud_spec():
    original = app_config.registered_providers()
    spec = app_config.ProviderSpec(
        name="gate-test-cloud",
        label="Gate Test Cloud",
        summary="Test-only cloud provider.",
        default_host=_GATE_CLOUD_HOST,
        default_vision_model="v",
        default_text_model="t",
        setup_hint="",
        api_key_env="GATE_TEST_KEY",
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


def _consented(spec) -> None:
    from opengrader.core.app_config import consent

    settings = app_config.AISettings(provider=spec.name, grade_written=True)
    resolved = app_config.resolve(settings, environ={})
    settings = consent.grant_cloud_consent(settings, resolved)
    app_config.save_settings(settings)


def test_automated_grading_refuses_a_real_cloud_send_without_the_flag(
    tmp_path: Path, gate_cloud_spec, capsys
) -> None:
    # Consented (so ensure_ready/text_generate would not themselves refuse)
    # but --yes-send-to-cloud is absent and stdin is not a TTY under pytest —
    # cli_confirm_cloud_send must refuse this run on its own, before any
    # network call, and never crash on its own keyword arguments.
    _consented(gate_cloud_spec)
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(SOLUTIONS_MD)
    submission_path = tmp_path / "student.submission.md"
    submission_path.write_text(SUBMISSION_MD)

    with pytest.raises(SystemExit) as excinfo:
        automated_grading.run_cli(
            [
                "--solutions", str(solutions_path),
                "--input", str(submission_path),
                "--llm",
                "--llm-host", _GATE_CLOUD_HOST,
            ]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert _GATE_CLOUD_HOST in err
    assert "--yes-send-to-cloud" in err


def test_extractor_refuses_a_real_cloud_send_without_the_flag(
    tmp_path: Path, gate_cloud_spec, capsys
) -> None:
    _consented(gate_cloud_spec)
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as excinfo:
        submission_extractor.run_cli(
            [
                "--input", str(scans),
                "--output", str(tmp_path / "out"),
                "--ollama-host", _GATE_CLOUD_HOST,
                "--llm-responses",
            ]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert _GATE_CLOUD_HOST in err
    assert "--yes-send-to-cloud" in err
    # The number of answers is not known before the scans are read; the
    # statement must not claim one image per PDF.
    assert "1 answer image " not in err
    assert "one answer image per answer found in 1 submission pdf" in err.lower()


def test_extractor_sends_no_answers_to_the_cloud_with_handwriting_reading_off(
    tmp_path: Path, gate_cloud_spec, capsys, monkeypatch
) -> None:
    """Without a blank template the extractor used to hand every answer crop
    to the vision model even with handwriting reading off. AI setup, the
    data-flow report and the pipeline's pre-flight all say no answer images
    are sent then, so a cloud provider must not get any."""
    _consented(gate_cloud_spec)
    assert app_config.resolve().read_handwriting is False
    assert not app_config.data_flow_report().items[0].leaves_machine
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")

    def _no_cloud_reader(*_args, **_kwargs):
        raise AssertionError("a cloud vision reader was built with handwriting reading off")

    monkeypatch.setattr(app_config, "vision_reader", _no_cloud_reader)
    monkeypatch.setattr("urllib.request.urlopen", _no_cloud_reader)

    try:
        submission_extractor.run_cli(
            ["--input", str(scans), "--output", str(tmp_path / "out")]
        )
    except SystemExit as exc:
        # The fake PDF may fail extraction; what matters is that it never
        # got as far as a cloud reader or a confirmation prompt.
        assert exc.code != 2
    err = capsys.readouterr().err
    assert "--yes-send-to-cloud" not in err
    assert "no answer images are sent" in err


def test_splitter_sends_nothing_to_the_cloud_without_the_flag(
    tmp_path: Path, gate_cloud_spec, capsys, monkeypatch
) -> None:
    """Unlike every other child CLI above, the splitter must NOT refuse here.

    Every vision call the splitter makes (page-number footers, Name/ID
    headers) is a whole-page read, and GuardedVisionReader always redirects
    those to a local reader — never to the cloud host — so a splitter run
    sends nothing to the cloud and must not ask for confirmation, even with
    a consented cloud provider selected and no --yes-send-to-cloud.

    The local Ollama route is stubbed to fail (no real server needed for
    this test); the splitter's documented fallback is then the text
    layer/Tesseract. `urlopen` is stubbed to blow up loudly if anything in
    this run ever tries to reach the network, cloud or otherwise.
    """
    import fitz

    _consented(gate_cloud_spec)
    bundle = tmp_path / "scans.pdf"
    doc = fitz.open()
    for _ in range(2):
        doc.new_page(width=612, height=792)
    doc.save(str(bundle))
    doc.close()

    def _no_network(*_args, **_kwargs):
        raise AssertionError("the splitter must never open a network connection")

    monkeypatch.setattr("urllib.request.urlopen", _no_network)
    monkeypatch.setattr(
        app_config,
        "vision_reader",
        lambda *_a, **_k: (_ for _ in ()).throw(ConnectionError("no local Ollama for this test")),
    )

    submission_splitter.run_cli(
        [
            "--input", str(bundle),
            "--output", str(tmp_path / "split"),
            "--llm-page-numbers",
            "--ollama-host", _GATE_CLOUD_HOST,
        ]
    )

    err = capsys.readouterr().err
    assert _GATE_CLOUD_HOST not in err
    assert "--yes-send-to-cloud" not in err


def test_answer_key_refuses_a_real_cloud_send_without_the_flag(
    tmp_path: Path, gate_cloud_spec, capsys
) -> None:
    _consented(gate_cloud_spec)
    key_pdf = tmp_path / "key.pdf"
    key_pdf.write_bytes(b"%PDF-1.4\n")

    with pytest.raises(SystemExit) as excinfo:
        answer_key.run_cli(
            [
                "--input", str(key_pdf),
                "--output", str(tmp_path / "qa.md"),
                "--llm-read-scans",
                "--ollama-model", "a-different-model",
            ]
        )
    assert excinfo.value.code == 2
    # answer_key's own emit() prints to stdout by default (no status_callback).
    out = capsys.readouterr().out
    assert _GATE_CLOUD_HOST in out
    assert "--yes-send-to-cloud" in out


def _no_input(*_args, **_kwargs):
    raise AssertionError("input() was called; inside the TUI this blocks forever")


def test_extractor_never_prompts_when_its_caller_owns_the_terminal(
    tmp_path: Path, gate_cloud_spec, capsys, monkeypatch
) -> None:
    # The TUI runs the extractor in-process: stdin is still a TTY there, and an
    # input() prompt on a worker thread would hang the app.
    _consented(gate_cloud_spec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _no_input)
    scans = tmp_path / "scans"
    scans.mkdir()
    (scans / "submission-0001.pdf").write_bytes(b"%PDF-1.4\n")

    with pytest.raises(CloudConfirmationNeeded) as excinfo:
        submission_extractor.run_cli(
            [
                "--input", str(scans),
                "--output", str(tmp_path / "out"),
                "--ollama-host", _GATE_CLOUD_HOST,
                "--llm-responses",
            ],
            prompt_cloud_send=False,
        )
    assert excinfo.value.code == 2
    assert excinfo.value.preflight.host == _GATE_CLOUD_HOST
    assert "one answer image per answer found in 1 submission pdf" in (
        excinfo.value.statement().lower()
    )
    assert "--yes-send-to-cloud" not in capsys.readouterr().err


def test_answer_key_never_prompts_when_its_caller_owns_the_terminal(
    tmp_path: Path, gate_cloud_spec, monkeypatch
) -> None:
    _consented(gate_cloud_spec)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _no_input)
    key_pdf = tmp_path / "key.pdf"
    key_pdf.write_bytes(b"%PDF-1.4\n")
    messages: list[str] = []

    with pytest.raises(CloudConfirmationNeeded) as excinfo:
        answer_key.run_cli(
            [
                "--input", str(key_pdf),
                "--output", str(tmp_path / "qa.md"),
                "--llm-read-scans",
                "--ollama-model", "a-different-model",
            ],
            status_callback=messages.append,
            prompt_cloud_send=False,
        )
    assert excinfo.value.code == 2
    assert _GATE_CLOUD_HOST in excinfo.value.statement()
    assert not (tmp_path / "qa.md").exists()
    assert not any("--yes-send-to-cloud" in m for m in messages)


def test_llm_eval_refuses_a_real_cloud_send_without_the_flag(
    tmp_path: Path, gate_cloud_spec, capsys
) -> None:
    _consented(gate_cloud_spec)
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(EVAL_SOLUTIONS_MD)
    submissions = tmp_path / "subs"
    submissions.mkdir()
    (submissions / "s1.submission.md").write_text(EVALUATED_SUBMISSION_MD)

    with pytest.raises(SystemExit) as excinfo:
        llm_eval.run_cli(
            ["--solutions", str(solutions_path), "--input", str(submissions)]
        )
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert _GATE_CLOUD_HOST in err
    assert "--yes-send-to-cloud" in err


def test_answer_key_confirmation_does_not_claim_a_single_image(
    tmp_path: Path, gate_cloud_spec
) -> None:
    """A multi-question key sends up to one image per question. The
    statement used to say "1 answer image will be sent" whatever the key."""
    import fitz

    _consented(gate_cloud_spec)
    key = tmp_path / "key.pdf"
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=612, height=792)
    doc.save(str(key))
    doc.close()
    args = Namespace(
        llm_read_scans=True,
        auto_ollama=False,
        ollama_model="a-different-model",
        ollama_host=_GATE_CLOUD_HOST,
        ollama_timeout=5.0,
        title=None,
        instructions=None,
        blank_template=None,
        yes_send_to_cloud=False,
    )
    messages: list[str] = []

    with pytest.raises(SystemExit) as excinfo:
        answer_key._qa_from_pdf(key, args, messages.append, None)

    assert excinfo.value.code == 2
    text = " ".join(messages)
    assert "1 answer image " not in text
    assert "up to one image per question found in the 3-page answer key" in text.lower()


def test_auto_grade_confirmation_counts_calls_per_answer_not_per_student(
    tmp_path: Path, gate_cloud_spec, capsys
) -> None:
    _consented(gate_cloud_spec)
    solutions_path = tmp_path / "solutions.md"
    solutions_path.write_text(
        SOLUTIONS_MD
        + "\n- (10 points) Explain why.\n  - **Correct**\n    - because\n"
        "  - **Rubric**\n    - Correct (+0)\n    - Partially Correct (-5)\n"
        "    - Incorrect (-10)\n"
    )
    submissions = tmp_path / "subs"
    submissions.mkdir()
    for index in (1, 2):
        (submissions / f"s{index}.submission.md").write_text(SUBMISSION_MD)

    with pytest.raises(SystemExit):
        automated_grading.run_cli(
            [
                "--solutions", str(solutions_path),
                "--input", str(submissions),
                "--llm",
                "--llm-host", _GATE_CLOUD_HOST,
            ]
        )

    err = capsys.readouterr().err
    # 2 submissions x 2 questions: four calls at most, not "2 graded-text calls".
    assert "up to 4 graded-text calls" in err.lower()

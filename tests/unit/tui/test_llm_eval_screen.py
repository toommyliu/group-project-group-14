"""Tests for the AI-grading calibration modal (LlmEvalScreen).

The modal shells out to the `llm-eval` CLI in a thread worker, so these tests
patch that one seam and drive the real screen: what has to be right is the
argv it builds, the source switch, and — the point of the whole thing — that a
recommended threshold comes back out and only when the grader asks for it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from textual.app import App
from textual.widgets import Button, Input, RichLog, Select, TextArea

from opengrader.core.grading.evaluation import (
    LlmEvalReport,
    ModelEvaluation,
    SkipTally,
    ThresholdRecommendation,
    ThresholdRow,
    summarize,
)
from opengrader.tui.ui.screens import llm_eval_screen as screen_module
from opengrader.tui.ui.screens.llm_eval_screen import (
    SOURCE_OTHER,
    SOURCE_THIS,
    LlmEvalScreen,
)


def _report(threshold: float | None, *, model: str = "llama3.1") -> LlmEvalReport:
    """A minimal report whose best model recommends ``threshold``."""
    row = ThresholdRow(
        threshold=threshold or 0.0,
        applied=10,
        deferred=0,
        coverage=1.0,
        agreements=10,
        agreement_rate=1.0,
        generous=0,
        harsh=0,
        mean_absolute_error=0.0,
    )
    return LlmEvalReport(
        models=(
            ModelEvaluation(
                model=model,
                overall=summarize([]),
                by_question_type=(),
                by_confidence=(),
                thresholds=(row,),
                recommendation=ThresholdRecommendation(
                    threshold=threshold,
                    row=row if threshold is not None else None,
                    target_agreement=0.9,
                    min_sample=5,
                    reason="because",
                ),
                worst_disagreements=(),
            ),
        ),
        cases_per_model=10,
        skipped=SkipTally(),
        points_tolerance=0.0,
        bucket_size=0.1,
        target_agreement=0.9,
        min_sample=5,
    )


class _Host(App):
    """Hosts the modal and records what it dismissed with."""

    def __init__(self, screen: LlmEvalScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: float | None = None
        self.dismissed = False

    def on_mount(self) -> None:
        def _record(value: float | None) -> None:
            self.result = value
            self.dismissed = True

        self.push_screen(self._screen, _record)


def _patch_cli(monkeypatch, report, *, captured: list[list[str]] | None = None):
    """Replace the CLI call with a stub, recording the argv it was handed."""

    def _fake(argv, **_kwargs):
        if captured is not None:
            captured.append(list(argv))
        print("(stub report)")
        return report

    monkeypatch.setattr(screen_module, "run_llm_eval_cli", _fake)


async def _settle(pilot) -> None:
    """Let the thread worker finish and its result marshal back onto the UI."""
    for _ in range(40):
        await pilot.pause()
        await asyncio.sleep(0.01)


# ── argv construction ────────────────────────────────────────────────────────


def test_running_against_this_assignment_uses_the_session_paths(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.85), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md",
                submissions_path="/exam/subs",
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    assert captured, "the CLI was never called"
    argv = captured[0]
    assert argv[:4] == ["--solutions", "/exam/solutions.md", "--input", "/exam/subs"]
    # Defaults: 90% agreement target, and boot/pull Ollama like the other steps.
    assert "--target-agreement" in argv
    assert argv[argv.index("--target-agreement") + 1] == "0.9"
    assert "--auto-ollama" in argv
    assert "--limit" not in argv


def test_another_assignment_uses_the_typed_paths(monkeypatch) -> None:  # noqa: ANN001
    """The point of the source switch: calibrate from a past, fully-marked exam."""
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#llm-eval-source", Select).value = SOURCE_OTHER
            await pilot.pause()
            screen.query_one("#llm-eval-solutions", Input).value = "/old/mid1.md"
            screen.query_one("#llm-eval-submissions", Input).value = "/old/subs"
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    argv = captured[0]
    assert argv[:4] == ["--solutions", "/old/mid1.md", "--input", "/old/subs"]


def test_the_other_source_fields_are_hidden_until_chosen() -> None:
    async def run() -> None:
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            assert screen.query_one("#llm-eval-other-source").has_class("hidden")
            screen.query_one("#llm-eval-source", Select).value = SOURCE_OTHER
            await pilot.pause()
            assert not screen.query_one("#llm-eval-other-source").has_class("hidden")

    asyncio.run(run())


def test_models_and_limit_become_repeated_flags(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.75), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#llm-eval-models", Input).value = "llama3.1, mistral"
            screen.query_one("#llm-eval-limit", Input).value = "25"
            screen.query_one("#llm-eval-target", Input).value = "95"
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    argv = captured[0]
    assert argv.count("--llm-model") == 2
    assert "llama3.1" in argv and "mistral" in argv
    assert argv[argv.index("--limit") + 1] == "25"
    # Instructors type a percentage; the CLI takes 0-1.
    assert argv[argv.index("--target-agreement") + 1] == "0.95"


def test_a_nonsense_target_is_refused_without_calling_the_model(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            app.screen.query_one("#llm-eval-target", Input).value = "ninety"
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    assert captured == []


def test_running_with_no_assignment_and_no_paths_is_refused(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(LlmEvalScreen())
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    assert captured == []


# ── the recommendation coming back out ───────────────────────────────────────


def test_the_recommendation_is_returned_only_when_applied(monkeypatch) -> None:  # noqa: ANN001
    """Closing must not quietly rewrite a setting the grader didn't accept."""

    async def run() -> tuple[float | None, float | None]:
        _patch_cli(monkeypatch, _report(0.85))

        # (a) Run, then Close: nothing comes back.
        closed = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md",
                submissions_path="/exam/subs",
                apply_label="Use in Auto-grade",
            )
        )
        async with closed.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)
            await pilot.click("#llm-eval-close")
            await pilot.pause()

        # (b) Run, then the apply button: the threshold comes back.
        applied = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md",
                submissions_path="/exam/subs",
                apply_label="Use in Auto-grade",
            )
        )
        async with applied.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)
            await pilot.click("#llm-eval-apply")
            await pilot.pause()

        return closed.result, applied.result

    closed_result, applied_result = asyncio.run(run())
    assert closed_result is None
    assert applied_result == 0.85


def test_the_apply_button_stays_disabled_until_a_threshold_exists(monkeypatch) -> None:  # noqa: ANN001
    """A model nothing can be trusted from must not offer a number to apply."""

    async def run() -> tuple[bool, bool]:
        _patch_cli(monkeypatch, _report(None))
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md",
                submissions_path="/exam/subs",
                apply_label="Use in Auto-grade",
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            before = app.screen.query_one("#llm-eval-apply", Button).disabled
            await pilot.click("#llm-eval-run")
            await _settle(pilot)
            after = app.screen.query_one("#llm-eval-apply", Button).disabled
        return before, after

    before, after = asyncio.run(run())
    assert before is True
    assert after is True


def test_a_caller_with_nowhere_to_put_the_number_gets_no_apply_button() -> None:
    """The workspace (shift+A) has no Auto-grade field, so no button."""

    async def run() -> int:
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            return len(app.screen.query("#llm-eval-apply"))

    assert asyncio.run(run()) == 0


def test_the_report_is_written_into_the_log(monkeypatch) -> None:  # noqa: ANN001
    async def run() -> int:
        _patch_cli(monkeypatch, _report(0.8))
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)
            return len(app.screen.query_one("#llm-eval-log", RichLog).lines)

    assert asyncio.run(run()) > 0


def test_a_crashing_harness_is_reported_not_raised(monkeypatch) -> None:  # noqa: ANN001
    """A slow opt-in operation must not take the app down with it."""

    async def run() -> bool:
        def _boom(_argv, **_kwargs):
            raise RuntimeError("ollama exploded")

        monkeypatch.setattr(screen_module, "run_llm_eval_cli", _boom)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)
            # Still usable: the Run button is re-enabled for another attempt.
            return app.screen.query_one("#llm-eval-run", Button).disabled

    assert asyncio.run(run()) is False


def test_paths_are_relayed_verbatim_from_a_real_provider_shape(tmp_path: Path) -> None:
    """The modal takes plain strings, so a provider's Path values must stringify."""
    screen = LlmEvalScreen(
        solutions_path=str(tmp_path / "qa.md"),
        submissions_path=str(tmp_path / "subs"),
    )
    assert screen._solutions_path.endswith("qa.md")
    assert screen._submissions_path.endswith("subs")


# ── pooling several classes ──────────────────────────────────────────────────


def test_extra_cohorts_become_repeated_input_flags(monkeypatch) -> None:  # noqa: ANN001
    """Same answer key, several terms' submissions — one bigger sample."""
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md",
                submissions_path="/exam/2026-spring",
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            app.screen.query_one("#llm-eval-classes", TextArea).text = (
                "/exam/2025-fall\n/exam/2025-spring\n"
            )
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    argv = captured[0]
    inputs = [argv[i + 1] for i, token in enumerate(argv) if token == "--input"]
    assert inputs == ["/exam/2026-spring", "/exam/2025-fall", "/exam/2025-spring"]


def test_blank_lines_in_the_cohort_list_are_ignored(monkeypatch) -> None:  # noqa: ANN001
    """A trailing newline from the Browse-and-append button is normal."""
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            app.screen.query_one("#llm-eval-classes", TextArea).text = (
                "\n  /exam/old  \n\n"
            )
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    argv = captured[0]
    inputs = [argv[i + 1] for i, token in enumerate(argv) if token == "--input"]
    assert inputs == ["/exam/subs", "/exam/old"]


def test_no_extra_cohorts_means_a_single_input(monkeypatch) -> None:  # noqa: ANN001
    captured: list[list[str]] = []

    async def run() -> None:
        _patch_cli(monkeypatch, _report(0.8), captured=captured)
        app = _Host(
            LlmEvalScreen(
                solutions_path="/exam/solutions.md", submissions_path="/exam/subs"
            )
        )
        async with app.run_test(size=(140, 48)) as pilot:
            await pilot.pause()
            await pilot.click("#llm-eval-run")
            await _settle(pilot)

    asyncio.run(run())
    assert captured[0].count("--input") == 1

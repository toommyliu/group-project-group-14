from __future__ import annotations

import json

import pytest

from opengrader.cli import doctor
from opengrader.core.environment import EnvironmentCheck, EnvironmentReport


def _report(*checks: EnvironmentCheck) -> EnvironmentReport:
    return EnvironmentReport(checks=checks)


@pytest.fixture
def stub_report(monkeypatch: pytest.MonkeyPatch):
    def install(report: EnvironmentReport) -> dict:
        recorded: dict = {}

        def fake_build(**kwargs) -> EnvironmentReport:
            recorded.update(kwargs)
            return report

        monkeypatch.setattr(doctor, "build_environment_report", fake_build)
        return recorded

    return install


def test_clean_environment_exits_zero(stub_report, capsys: pytest.CaptureFixture) -> None:
    stub_report(_report(EnvironmentCheck(name="Python", status="ready", detail="3.12.4")))

    assert doctor.run_cli([]) == 0
    out = capsys.readouterr().out
    assert "Python: 3.12.4" in out
    assert "Everything Opengrader needs" in out


def test_findings_print_their_impact_and_fix(
    stub_report, capsys: pytest.CaptureFixture
) -> None:
    stub_report(
        _report(
            EnvironmentCheck(
                name="Tesseract OCR",
                status="broken",
                detail="not installed",
                impact="Scanned exams extract to empty answers.",
                fix="brew install tesseract",
            )
        )
    )

    doctor.run_cli([])
    out = capsys.readouterr().out
    assert "Scanned exams extract to empty answers." in out
    assert "Fix: brew install tesseract" in out


def test_findings_are_repeated_as_a_todo_list_worst_first(
    stub_report, capsys: pytest.CaptureFixture
) -> None:
    stub_report(
        _report(
            EnvironmentCheck(name="Python", status="ready", detail="3.12.4"),
            EnvironmentCheck(name="pix2tex", status="degraded", detail="", fix="uv sync --extra math"),
            EnvironmentCheck(name="Tesseract OCR", status="broken", detail="", fix="brew install tesseract"),
        )
    )

    doctor.run_cli([])
    out = capsys.readouterr().out
    todo = out.split("To do, most important first:")[1]
    assert todo.index("Tesseract OCR") < todo.index("pix2tex")
    assert "Python" not in todo


def test_fail_on_problem_exits_two_only_for_broken_checks(stub_report) -> None:
    stub_report(_report(EnvironmentCheck(name="Tesseract OCR", status="broken", detail="")))
    assert doctor.run_cli(["--fail-on-problem"]) == 2


def test_fail_on_problem_ignores_optional_gaps(stub_report) -> None:
    stub_report(_report(EnvironmentCheck(name="Ollama server", status="degraded", detail="")))
    assert doctor.run_cli(["--fail-on-problem"]) == 0


def test_json_output_carries_every_field(stub_report, capsys: pytest.CaptureFixture) -> None:
    stub_report(
        _report(
            EnvironmentCheck(
                name="Ollama server",
                status="degraded",
                detail="not reachable",
                impact="Optional.",
                fix="ollama serve",
            )
        )
    )

    doctor.run_cli(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "degraded"
    assert payload["checks"] == [
        {
            "name": "Ollama server",
            "status": "degraded",
            "detail": "not reachable",
            "impact": "Optional.",
            "fix": "ollama serve",
        }
    ]


def test_flags_reach_the_engine(stub_report) -> None:
    recorded = stub_report(_report())
    doctor.run_cli(
        [
            "--ollama-model",
            "llama3.2-vision:11b",
            "--llm-model",
            "llama3.1:8b",
            "--ollama-host",
            "http://box:11434",
            "--smoke",
            "--no-graphics",
        ]
    )
    assert recorded["vision_model"] == "llama3.2-vision:11b"
    assert recorded["text_model"] == "llama3.1:8b"
    assert recorded["ollama_host"] == "http://box:11434"
    assert recorded["check_graphics"] is False
    assert recorded["smoke_test"] is True
    assert "ai" in recorded
    assert recorded["time_ai"] is False


def test_main_returns_the_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "run_cli", lambda argv=None: 0)
    assert doctor.main() == 0


def test_time_ai_flag_reaches_the_engine(stub_report) -> None:
    recorded = stub_report(_report())
    doctor.run_cli(["--time-ai"])
    assert recorded.get("time_ai") is True
    assert "ai" in recorded


def test_ai_features_and_latency_in_json_output(stub_report, capsys: pytest.CaptureFixture) -> None:
    stub_report(
        _report(
            EnvironmentCheck(name="Python", status="ready", detail="3.12.4"),
            EnvironmentCheck(
                name="AI features", status="ready", detail="reading handwriting"
            ),
        )
    )

    doctor.run_cli(["--json"])
    payload = json.loads(capsys.readouterr().out)
    check_names = [check["name"] for check in payload["checks"]]
    assert "AI features" in check_names

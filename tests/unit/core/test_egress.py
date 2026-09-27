"""Tests for egress logging: recording, reading, and summarizing outbound data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opengrader.core import egress


class TestReadEntries:
    def test_read_valid_entries(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1}\n'
            '{"at":"2026-09-25T10:01:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2}\n'
        )
        entries = egress.read_entries(log_file)
        assert len(entries) == 2
        assert entries[0]["provider"] == "ollama"
        assert entries[1]["count"] == 2

    def test_read_entries_with_tokens(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"openai-chat","host":"api.openai.com","category":"answer-image","count":1,"input_tokens":100,"output_tokens":50}\n'
        )
        entries = egress.read_entries(log_file)
        assert len(entries) == 1
        assert entries[0]["input_tokens"] == 100
        assert entries[0]["output_tokens"] == 50

    def test_read_entries_skip_blank_lines(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1}\n'
            "\n"
            '{"at":"2026-09-25T10:01:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2}\n'
        )
        entries = egress.read_entries(log_file)
        assert len(entries) == 2

    def test_read_entries_skip_malformed_lines(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1}\n'
            "not valid json\n"
            '{"at":"2026-09-25T10:01:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2}\n'
        )
        entries = egress.read_entries(log_file)
        assert len(entries) == 2
        assert entries[0]["provider"] == "ollama"
        assert entries[1]["provider"] == "anthropic"

    def test_read_entries_missing_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "nonexistent.jsonl"
        entries = egress.read_entries(log_file)
        assert entries == []


class TestEgressSummary:
    def test_to_dict(self) -> None:
        summary = egress.EgressSummary(
            total_calls=5,
            calls_by_category={"answer-image": 3, "graded-text": 2},
            calls_by_host={"localhost": 5},
            total_input_tokens=1000,
            total_output_tokens=500,
            calls_with_tokens=5,
            first_at="2026-09-25T10:00:00+00:00",
            last_at="2026-09-25T10:05:00+00:00",
        )
        result = summary.to_dict()
        assert result["total_calls"] == 5
        assert result["total_input_tokens"] == 1000
        assert result["calls_by_category"]["answer-image"] == 3


class TestSummarize:
    def test_summarize_direct_file(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1,"input_tokens":100,"output_tokens":50}\n'
            '{"at":"2026-09-25T10:01:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2,"input_tokens":200,"output_tokens":100}\n'
        )
        summary = egress.summarize(log_file)
        assert summary.total_calls == 2
        assert summary.calls_by_category["answer-image"] == 1
        assert summary.calls_by_category["graded-text"] == 1
        assert summary.calls_by_host["localhost"] == 1
        assert summary.calls_by_host["api.anthropic.com"] == 1
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150
        assert summary.calls_with_tokens == 2

    def test_summarize_folder(self, tmp_path: Path) -> None:
        # Create .opengrader directory structure
        egress_dir = tmp_path / ".opengrader"
        egress_dir.mkdir()
        log_file = egress_dir / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1}\n'
        )
        summary = egress.summarize(tmp_path)
        assert summary.total_calls == 1
        assert summary.calls_by_category["answer-image"] == 1

    def test_summarize_with_since_filter(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1}\n'
            '{"at":"2026-09-25T10:05:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2}\n'
            '{"at":"2026-09-25T11:00:00+00:00","provider":"openai-chat","host":"api.openai.com","category":"answer-image","count":3}\n'
        )
        summary = egress.summarize(log_file, since="2026-09-25T10:30:00+00:00")
        assert summary.total_calls == 1
        assert summary.calls_by_category["answer-image"] == 1
        assert summary.calls_by_host["api.openai.com"] == 1

    def test_summarize_empty_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text("")
        summary = egress.summarize(log_file)
        assert summary.total_calls == 0
        assert summary.total_input_tokens == 0
        assert summary.total_output_tokens == 0
        assert summary.first_at is None
        assert summary.last_at is None

    def test_summarize_missing_log(self, tmp_path: Path) -> None:
        log_file = tmp_path / "nonexistent.jsonl"
        summary = egress.summarize(log_file)
        assert summary.total_calls == 0
        assert summary.total_input_tokens == 0

    def test_summarize_partial_tokens(self, tmp_path: Path) -> None:
        log_file = tmp_path / "cloud-egress.jsonl"
        log_file.write_text(
            '{"at":"2026-09-25T10:00:00+00:00","provider":"ollama","host":"localhost","category":"answer-image","count":1,"input_tokens":100}\n'
            '{"at":"2026-09-25T10:01:00+00:00","provider":"anthropic","host":"api.anthropic.com","category":"graded-text","count":2}\n'
            '{"at":"2026-09-25T10:02:00+00:00","provider":"openai-chat","host":"api.openai.com","category":"answer-image","count":1,"output_tokens":75}\n'
        )
        summary = egress.summarize(log_file)
        assert summary.total_calls == 3
        assert summary.total_input_tokens == 100
        assert summary.total_output_tokens == 75
        assert summary.calls_with_tokens == 2


class TestAverageTokens:
    def test_average_tokens_from_summary(self) -> None:
        summary = egress.EgressSummary(
            total_calls=4,
            calls_by_category={},
            calls_by_host={},
            total_input_tokens=400,
            total_output_tokens=200,
            calls_with_tokens=4,
            first_at=None,
            last_at=None,
        )
        result = egress.average_tokens(summary)
        assert result == (100.0, 50.0)

    def test_average_tokens_from_entries(self) -> None:
        entries = [
            {"input_tokens": 100, "output_tokens": 50},
            {"input_tokens": 200, "output_tokens": 100},
        ]
        result = egress.average_tokens(entries)
        assert result == (150.0, 75.0)

    def test_average_tokens_from_entries_with_category(self) -> None:
        entries = [
            {"category": "answer-image", "input_tokens": 100, "output_tokens": 50},
            {"category": "graded-text", "input_tokens": 200, "output_tokens": 100},
            {"category": "answer-image", "input_tokens": 150, "output_tokens": 75},
        ]
        result = egress.average_tokens(entries, category="answer-image")
        assert result == (125.0, 62.5)

    def test_average_tokens_no_calls_with_tokens(self) -> None:
        summary = egress.EgressSummary(
            total_calls=2,
            calls_by_category={},
            calls_by_host={},
            total_input_tokens=0,
            total_output_tokens=0,
            calls_with_tokens=0,
            first_at=None,
            last_at=None,
        )
        result = egress.average_tokens(summary)
        assert result is None

    def test_average_tokens_empty_entries(self) -> None:
        entries: list[dict] = []
        result = egress.average_tokens(entries)
        assert result is None


class TestEstimateCost:
    def test_estimate_cost_with_pricing(self) -> None:
        from opengrader.core.app_config import ProviderPricing

        pricing = ProviderPricing(input_per_mtok=0.50, output_per_mtok=1.50)
        # 1M input tokens * $0.50 + 1M output tokens * $1.50 = $2.00
        cost = egress.estimate_cost(1_000_000, 1_000_000, pricing)
        assert cost == 2.0

    def test_estimate_cost_partial_tokens(self) -> None:
        from opengrader.core.app_config import ProviderPricing

        pricing = ProviderPricing(input_per_mtok=0.50, output_per_mtok=1.50)
        # 100 input tokens * ($0.50/1M) = 0.00005
        # 50 output tokens * ($1.50/1M) = 0.000075
        # Total = 0.000125
        cost = egress.estimate_cost(100, 50, pricing)
        assert abs(cost - 0.000125) < 1e-7

    def test_estimate_cost_no_pricing(self) -> None:
        cost = egress.estimate_cost(1_000_000, 1_000_000, None)
        assert cost is None

    def test_estimate_cost_zero_pricing(self) -> None:
        from opengrader.core.app_config import ProviderPricing

        pricing = ProviderPricing(input_per_mtok=0, output_per_mtok=0)
        cost = egress.estimate_cost(1_000_000, 1_000_000, pricing)
        assert cost is None

    def test_estimate_cost_invalid_pricing(self) -> None:
        cost = egress.estimate_cost(1_000_000, 1_000_000, "invalid")
        assert cost is None

    def test_estimate_cost_missing_attribute(self) -> None:
        class PartialPricing:
            input_per_mtok = 0.50

        cost = egress.estimate_cost(1_000_000, 1_000_000, PartialPricing())
        assert cost is None


class TestRecordNeverRaises:
    def test_record_on_unwritable_directory(self, tmp_path: Path, monkeypatch) -> None:
        """record() should never raise, even with permission errors."""
        # Create a read-only directory
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        try:
            # Set the egress dir to a subdirectory of the readonly dir
            monkeypatch.setenv(egress.ENV_VAR, str(readonly_dir / "subdir"))
            # This should not raise
            egress.record(provider="test", host="localhost", category="answer-image")
        finally:
            # Restore permissions for cleanup
            readonly_dir.chmod(0o755)


class TestRecordAndRead:
    def test_record_and_read_round_trip(self, tmp_path: Path, monkeypatch) -> None:
        """Integration test: record entries and read them back."""
        monkeypatch.setenv(egress.ENV_VAR, str(tmp_path))

        # Record some entries
        egress.record(provider="ollama", host="localhost", category="answer-image", count=1, input_tokens=100, output_tokens=50)
        egress.record(provider="anthropic", host="api.anthropic.com", category="graded-text", count=2, input_tokens=200, output_tokens=100)

        # Get the active log path
        log_path = egress.active_log_path()

        # Read them back
        entries = egress.read_entries(log_path)
        assert len(entries) == 2
        assert entries[0]["provider"] == "ollama"
        assert entries[1]["provider"] == "anthropic"

        # Summarize
        summary = egress.summarize(log_path)
        assert summary.total_calls == 2
        assert summary.total_input_tokens == 300
        assert summary.total_output_tokens == 150


@pytest.mark.parametrize(
    "host, secret, expected",
    [
        ("https://alice:hunter2pass@gw.example.edu/v1", "hunter2pass", "https://gw.example.edu/v1"),
        ("https://gw.example.edu/v1?key=AIzaSyFAKEFAKEFAKE", "AIzaSyFAKEFAKEFAKE", "https://gw.example.edu/v1"),
        ("https://gw.example.edu:8443/v1#token=abcdef123456", "abcdef123456", "https://gw.example.edu:8443/v1"),
    ],
)
def test_a_secret_in_the_host_url_is_never_written_to_the_log(
    tmp_path: Path, host: str, secret: str, expected: str
) -> None:
    with egress.recording(tmp_path) as log:
        egress.record(provider="p", host=host, category="answer-image")

    text = log.read_text(encoding="utf-8")
    assert secret not in text
    assert json.loads(text.splitlines()[0])["host"] == expected

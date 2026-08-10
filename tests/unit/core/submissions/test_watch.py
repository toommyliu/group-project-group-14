"""Tests for the extraction-progress poller (`core/submissions/watch.py`)."""

from __future__ import annotations

import os
import time
from pathlib import Path

from opengrader.core.submissions.manifest import ExtractionRecord, append_record
from opengrader.core.submissions.watch import ExtractionWatcher, _ACTIVE_RECENCY_SECONDS


def _record(key: str, *, outputs: tuple[str, ...] = ()) -> ExtractionRecord:
    return ExtractionRecord(
        key=key,
        source=f"/scans/{key}.pdf",
        source_sha256="aaa",
        settings_id="sss",
        outputs=outputs,
    )


def _touch_submission(output_dir: Path, key: str) -> tuple[str, ...]:
    """Write a real submission file for ``key`` and return its manifest-relative path."""
    submission_dir = output_dir / key
    submission_dir.mkdir(parents=True, exist_ok=True)
    markdown = submission_dir / f"{key}.submission.md"
    markdown.write_text("stub")
    return (f"{key}/{key}.submission.md",)


# --------------------------------------------------------------------------- #
# The torn-write guard — this is the test that earns the feature
# --------------------------------------------------------------------------- #


def test_files_on_disk_with_no_manifest_record_are_not_folded_in(tmp_path: Path) -> None:
    """A submission's files existing is not enough; the manifest record is
    what says the write finished. Without this guard a poll that lands while
    the extractor is mid-write would hand the grader a torn submission."""
    _touch_submission(tmp_path, "student-01")

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()

    assert result.new_keys == ()
    assert result.appears_active is False


def test_record_whose_outputs_are_missing_is_not_folded_in_yet(tmp_path: Path) -> None:
    """Extra insurance beyond "the key is in the manifest": the files the
    record names must actually be present (mirrors `record_is_reusable`)."""
    append_record(
        tmp_path,
        _record("student-01", outputs=("student-01/student-01.submission.md",)),
    )

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()

    assert result.new_keys == ()

    # Once the file actually shows up, the same record becomes eligible —
    # nothing needs to be re-appended to the manifest.
    _touch_submission(tmp_path, "student-01")
    result = watcher.poll()
    assert result.new_keys == ("student-01",)


# --------------------------------------------------------------------------- #
# New keys reported once
# --------------------------------------------------------------------------- #


def test_new_keys_are_reported_once_and_not_repeated(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs))

    watcher = ExtractionWatcher(tmp_path)
    first = watcher.poll()
    assert first.new_keys == ("student-01",)

    second = watcher.poll()
    assert second.new_keys == ()


def test_keys_accumulate_across_polls_in_first_seen_order(tmp_path: Path) -> None:
    watcher = ExtractionWatcher(tmp_path)

    outputs_1 = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs_1))
    first = watcher.poll()
    assert first.new_keys == ("student-01",)

    outputs_2 = _touch_submission(tmp_path, "student-02")
    append_record(tmp_path, _record("student-02", outputs=outputs_2))
    second = watcher.poll()
    assert second.new_keys == ("student-02",)

    # A record for an already-seen key (e.g. a re-run) is not re-reported.
    append_record(tmp_path, _record("student-01", outputs=outputs_1))
    third = watcher.poll()
    assert third.new_keys == ()


# --------------------------------------------------------------------------- #
# No manifest at all
# --------------------------------------------------------------------------- #


def test_no_manifest_reports_nothing_new_and_not_active(tmp_path: Path) -> None:
    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()
    assert result.new_keys == ()
    assert result.appears_active is False


def test_no_manifest_still_honors_known_active_override(tmp_path: Path) -> None:
    """The one caller with certainty (the TUI, mid-launch) must be able to say
    "this is active" even before the ledger's first line has been written."""
    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll(known_active=True)
    assert result.appears_active is True


# --------------------------------------------------------------------------- #
# appears_active heuristic and the known_active override
# --------------------------------------------------------------------------- #


def test_recently_touched_manifest_appears_active(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs))

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()
    assert result.appears_active is True


def test_stale_manifest_does_not_appear_active(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    # Back-date the manifest well past the recency window.
    old = time.time() - _ACTIVE_RECENCY_SECONDS - 5
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()
    assert result.appears_active is False


def test_known_active_override_wins_over_the_heuristic(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))
    old = time.time() - _ACTIVE_RECENCY_SECONDS - 5
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)

    # The heuristic alone would say inactive (backdated above)...
    assert watcher.poll().appears_active is False
    # ...but a caller who knows better can still assert it is active.
    assert watcher.poll(known_active=True).appears_active is True
    # And a caller who knows extraction has definitely finished can force the
    # opposite even if the manifest were touched a moment ago.
    assert watcher.poll(known_active=False).appears_active is False


def test_a_long_vision_model_gap_still_reads_as_active(tmp_path: Path) -> None:
    """The regression this window exists for: `--llm-responses` on CPU-only
    Ollama can spend a minute or more on a single exam, so a manifest that has
    been quiet that long is a *running* extraction between two students, not a
    finished one. Calling it finished here is what made the app tell a grader
    "all students finalized" with a hundred submissions still to come."""
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    slow_submission = time.time() - 90.0
    os.utime(manifest_path, (slow_submission, slow_submission))

    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().appears_active is True


# --------------------------------------------------------------------------- #
# idle_seconds — the separate signal callers stop watching on
# --------------------------------------------------------------------------- #


def test_idle_seconds_reports_the_manifest_age(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    old = time.time() - 300.0
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)
    idle = watcher.poll().idle_seconds
    assert idle is not None
    assert 290.0 <= idle <= 310.0  # loose: only the clock's own jitter


def test_idle_seconds_is_none_when_there_is_no_manifest(tmp_path: Path) -> None:
    """Distinguishable from "idle for a long time" on purpose: a caller
    deciding whether to stop watching treats "no ledger at all" differently
    from "a ledger that has gone quiet"."""
    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().idle_seconds is None


def test_idle_seconds_is_never_negative(tmp_path: Path) -> None:
    """A manifest written by a process whose clock runs slightly ahead of ours
    would otherwise come back as a negative age, which reads as "the future" to
    every caller's comparison."""
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    future = time.time() + 120.0
    os.utime(manifest_path, (future, future))

    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().idle_seconds == 0.0


def test_idle_seconds_is_reported_even_when_known_active_overrides(tmp_path: Path) -> None:
    """`known_active` decides `appears_active`; it must not suppress the raw
    age, or a caller that trusts the override for its status line loses the
    only signal it has for when to stop polling."""
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))
    old = time.time() - 400.0
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll(known_active=True)

    assert result.appears_active is True
    assert result.idle_seconds is not None and result.idle_seconds > 300.0


# --------------------------------------------------------------------------- #
# Cheap polling: skip re-reading an unchanged manifest
# --------------------------------------------------------------------------- #


def test_unchanged_manifest_is_not_re_read_between_polls(tmp_path: Path, monkeypatch) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs))

    watcher = ExtractionWatcher(tmp_path)
    watcher.poll()  # primes the cached signature/records

    import opengrader.core.submissions.watch as watch_module

    calls = []
    original = watch_module.read_manifest

    def _spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(watch_module, "read_manifest", _spy)

    watcher.poll()
    watcher.poll()
    assert calls == []  # manifest mtime/size unchanged -> never re-read

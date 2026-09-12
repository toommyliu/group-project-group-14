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
    submission_dir = output_dir / key
    submission_dir.mkdir(parents=True, exist_ok=True)
    markdown = submission_dir / f"{key}.submission.md"
    markdown.write_text("stub")
    return (f"{key}/{key}.submission.md",)


def test_files_on_disk_with_no_manifest_record_are_not_folded_in(tmp_path: Path) -> None:
    _touch_submission(tmp_path, "student-01")

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()

    assert result.new_keys == ()
    assert result.appears_active is False


def test_record_whose_outputs_are_missing_is_not_folded_in_yet(tmp_path: Path) -> None:
    append_record(
        tmp_path,
        _record("student-01", outputs=("student-01/student-01.submission.md",)),
    )

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()

    assert result.new_keys == ()

    _touch_submission(tmp_path, "student-01")
    result = watcher.poll()
    assert result.new_keys == ("student-01",)


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

    append_record(tmp_path, _record("student-01", outputs=outputs_1))
    third = watcher.poll()
    assert third.new_keys == ()


def test_no_manifest_reports_nothing_new_and_not_active(tmp_path: Path) -> None:
    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()
    assert result.new_keys == ()
    assert result.appears_active is False


def test_no_manifest_still_honors_known_active_override(tmp_path: Path) -> None:
    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll(known_active=True)
    assert result.appears_active is True


def test_recently_touched_manifest_appears_active(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs))

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll()
    assert result.appears_active is True


def test_stale_manifest_does_not_appear_active(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

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

    assert watcher.poll().appears_active is False
    assert watcher.poll(known_active=True).appears_active is True
    assert watcher.poll(known_active=False).appears_active is False


def test_a_long_vision_model_gap_still_reads_as_active(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    slow_submission = time.time() - 90.0
    os.utime(manifest_path, (slow_submission, slow_submission))

    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().appears_active is True


def test_idle_seconds_reports_the_manifest_age(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    old = time.time() - 300.0
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)
    idle = watcher.poll().idle_seconds
    assert idle is not None
    assert 290.0 <= idle <= 310.0


def test_idle_seconds_is_none_when_there_is_no_manifest(tmp_path: Path) -> None:
    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().idle_seconds is None


def test_idle_seconds_is_never_negative(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))

    future = time.time() + 120.0
    os.utime(manifest_path, (future, future))

    watcher = ExtractionWatcher(tmp_path)
    assert watcher.poll().idle_seconds == 0.0


def test_idle_seconds_is_reported_even_when_known_active_overrides(tmp_path: Path) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    manifest_path = append_record(tmp_path, _record("student-01", outputs=outputs))
    old = time.time() - 400.0
    os.utime(manifest_path, (old, old))

    watcher = ExtractionWatcher(tmp_path)
    result = watcher.poll(known_active=True)

    assert result.appears_active is True
    assert result.idle_seconds is not None and result.idle_seconds > 300.0


def test_unchanged_manifest_is_not_re_read_between_polls(tmp_path: Path, monkeypatch) -> None:
    outputs = _touch_submission(tmp_path, "student-01")
    append_record(tmp_path, _record("student-01", outputs=outputs))

    watcher = ExtractionWatcher(tmp_path)
    watcher.poll()

    import opengrader.core.submissions.watch as watch_module

    calls = []
    original = watch_module.read_manifest

    def _spy(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(watch_module, "read_manifest", _spy)

    watcher.poll()
    watcher.poll()
    assert calls == []

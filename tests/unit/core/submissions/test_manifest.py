"""Tests for the extraction resume ledger (`core/submissions/manifest.py`)."""

from pathlib import Path

from opengrader.core.submissions.manifest import (
    RUN_LEDGER_FILENAME,
    ExtractionRecord,
    append_record,
    file_fingerprint,
    input_fingerprint,
    read_manifest,
    record_is_reusable,
    relative_outputs,
    resolve_manifest_path,
    settings_fingerprint,
    utc_timestamp,
)


def _record(**overrides) -> ExtractionRecord:
    base = dict(
        key="submission-0001",
        source="/scans/submission-0001.pdf",
        source_sha256="aaa",
        settings_id="sss",
        outputs=(),
    )
    base.update(overrides)
    return ExtractionRecord(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Location & round-trip
# --------------------------------------------------------------------------- #


def test_manifest_lives_under_the_dot_opengrader_dir(tmp_path) -> None:
    path = resolve_manifest_path(tmp_path)
    assert path == tmp_path / ".opengrader" / "extraction-manifest.jsonl"


def test_manifest_path_from_a_file_uses_its_parent(tmp_path) -> None:
    marker = tmp_path / "x.submission.md"
    marker.write_text("")
    assert resolve_manifest_path(marker).parent.parent == tmp_path


def test_record_round_trips_through_json() -> None:
    original = _record(
        outputs=("a/x.submission.md", "a/responses/q1.png"),
        student_id="111",
        student_name="Ann Lee",
        completed_at="2026-08-01T00:00:00+00:00",
        duration_seconds=1.2345,
    )
    restored = ExtractionRecord.from_json(original.to_json())
    # duration is rounded on write, so compare against the rounded value.
    assert restored == ExtractionRecord(
        key=original.key,
        source=original.source,
        source_sha256=original.source_sha256,
        settings_id=original.settings_id,
        outputs=original.outputs,
        student_id="111",
        student_name="Ann Lee",
        completed_at="2026-08-01T00:00:00+00:00",
        duration_seconds=1.234,
    )


# --------------------------------------------------------------------------- #
# The append-only log
# --------------------------------------------------------------------------- #


def test_append_then_read_returns_the_record(tmp_path) -> None:
    append_record(tmp_path, _record())
    ledger = read_manifest(tmp_path)
    assert set(ledger) == {"submission-0001"}
    assert ledger["submission-0001"].source_sha256 == "aaa"


def test_missing_manifest_reads_as_empty(tmp_path) -> None:
    assert read_manifest(tmp_path) == {}


def test_later_line_supersedes_an_earlier_one(tmp_path) -> None:
    """Re-extracting a PDF appends; the newest line is the current truth."""
    append_record(tmp_path, _record(source_sha256="old"))
    append_record(tmp_path, _record(source_sha256="new"))
    assert read_manifest(tmp_path)["submission-0001"].source_sha256 == "new"


def test_truncated_final_line_does_not_break_the_ledger(tmp_path) -> None:
    """A process killed mid-write can only damage the last line."""
    append_record(tmp_path, _record(key="submission-0001"))
    path = resolve_manifest_path(tmp_path)
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"key":"submission-0002","source_sha')  # cut off mid-write
    assert set(read_manifest(tmp_path)) == {"submission-0001"}


def test_lines_without_a_key_are_skipped(tmp_path) -> None:
    path = resolve_manifest_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text('{"source":"x"}\n[]\n\n')
    assert read_manifest(tmp_path) == {}


# --------------------------------------------------------------------------- #
# Fingerprints
# --------------------------------------------------------------------------- #


def test_file_fingerprint_tracks_content_not_name(tmp_path) -> None:
    a = tmp_path / "a.pdf"
    b = tmp_path / "b.pdf"
    a.write_bytes(b"same")
    b.write_bytes(b"same")
    assert file_fingerprint(a) == file_fingerprint(b)
    b.write_bytes(b"different")
    assert file_fingerprint(a) != file_fingerprint(b)


def test_unreadable_file_fingerprints_as_empty(tmp_path) -> None:
    assert file_fingerprint(tmp_path / "nope.pdf") == ""


def test_settings_fingerprint_ignores_key_order() -> None:
    assert settings_fingerprint({"a": 1, "b": 2}) == settings_fingerprint({"b": 2, "a": 1})


def test_settings_fingerprint_changes_with_any_value() -> None:
    assert settings_fingerprint({"dpi": 300}) != settings_fingerprint({"dpi": 200})


def test_settings_fingerprint_survives_unjsonable_values(tmp_path) -> None:
    """A Path (or anything else odd) contributes rather than raising mid-run."""
    assert settings_fingerprint({"template": tmp_path / "blank.pdf"})


# --------------------------------------------------------------------------- #
# The reuse decision — the heart of --resume
# --------------------------------------------------------------------------- #


def test_no_record_is_never_reusable(tmp_path) -> None:
    assert not record_is_reusable(
        None, output_dir=tmp_path, source_sha256="aaa", settings_id="sss"
    )


def test_matching_record_with_present_outputs_is_reusable(tmp_path) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.submission.md").write_text("x")
    record = _record(outputs=("sub/x.submission.md",))
    assert record_is_reusable(
        record, output_dir=tmp_path, source_sha256="aaa", settings_id="sss"
    )


def test_changed_input_is_not_reusable(tmp_path) -> None:
    assert not record_is_reusable(
        _record(), output_dir=tmp_path, source_sha256="bbb", settings_id="sss"
    )


def test_changed_settings_are_not_reusable(tmp_path) -> None:
    assert not record_is_reusable(
        _record(), output_dir=tmp_path, source_sha256="aaa", settings_id="ttt"
    )


def test_deleted_output_is_not_reusable(tmp_path) -> None:
    record = _record(outputs=("sub/x.submission.md",))
    assert not record_is_reusable(
        record, output_dir=tmp_path, source_sha256="aaa", settings_id="sss"
    )


def test_deleted_response_crop_is_not_reusable(tmp_path) -> None:
    """Crops count as outputs, so a wiped responses/ folder forces a re-extract."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "x.submission.md").write_text("x")
    record = _record(outputs=("sub/x.submission.md", "sub/responses/q1.png"))
    assert not record_is_reusable(
        record, output_dir=tmp_path, source_sha256="aaa", settings_id="sss"
    )


def test_edited_output_stays_reusable(tmp_path) -> None:
    """The TUI rewrites .submission.md on every grade save. Invalidating on
    content would make --resume re-extract graded work and destroy the grades,
    so only existence is checked."""
    (tmp_path / "sub").mkdir()
    markdown = tmp_path / "sub" / "x.submission.md"
    markdown.write_text("original")
    record = _record(outputs=("sub/x.submission.md",))
    markdown.write_text("graded: 8/10")
    assert record_is_reusable(
        record, output_dir=tmp_path, source_sha256="aaa", settings_id="sss"
    )


def test_empty_fingerprints_are_never_reusable(tmp_path) -> None:
    """An unreadable input hashes to "" — that must not match a "" on record."""
    record = _record(source_sha256="")
    assert not record_is_reusable(
        record, output_dir=tmp_path, source_sha256="", settings_id="sss"
    )


# --------------------------------------------------------------------------- #
# Output path normalization
# --------------------------------------------------------------------------- #


def test_outputs_are_stored_relative_to_the_output_dir(tmp_path) -> None:
    markdown = tmp_path / "sub" / "x.submission.md"
    markdown.parent.mkdir()
    markdown.write_text("x")
    assert relative_outputs(tmp_path, [markdown]) == ("sub/x.submission.md",)


def test_outputs_outside_the_output_dir_stay_absolute(tmp_path) -> None:
    outside = tmp_path.parent / "elsewhere.md"
    (result,) = relative_outputs(tmp_path / "out", [outside])
    assert Path(result).is_absolute()


def test_outputs_are_deduplicated_in_order(tmp_path) -> None:
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    assert relative_outputs(tmp_path, [a, b, a, "", None]) == ("a.md", "b.md")


def test_utc_timestamp_is_sortable_iso8601() -> None:
    stamp = utc_timestamp()
    assert stamp.endswith("+00:00") and stamp[4] == "-" and "T" in stamp


# --------------------------------------------------------------------------- #
# Two ledgers, one module
# --------------------------------------------------------------------------- #


def test_the_run_ledger_is_a_separate_file_in_the_same_dot_dir(tmp_path) -> None:
    path = resolve_manifest_path(tmp_path, filename=RUN_LEDGER_FILENAME)
    assert path == tmp_path / ".opengrader" / "run-ledger.jsonl"
    assert path != resolve_manifest_path(tmp_path)


def test_the_two_ledgers_never_read_each_others_records(tmp_path) -> None:
    """A shared file would hand each reader the other's keys to filter out."""
    append_record(tmp_path, _record(key="submission-0001"))
    append_record(tmp_path, _record(key="split"), filename=RUN_LEDGER_FILENAME)

    assert set(read_manifest(tmp_path)) == {"submission-0001"}
    assert set(read_manifest(tmp_path, filename=RUN_LEDGER_FILENAME)) == {"split"}


# --------------------------------------------------------------------------- #
# Fingerprinting an input that may be a directory
# --------------------------------------------------------------------------- #


def test_input_fingerprint_of_a_file_is_its_bytes(tmp_path) -> None:
    scan = tmp_path / "bundle.pdf"
    scan.write_bytes(b"%PDF-1.4 one")
    assert input_fingerprint(scan) == file_fingerprint(scan)


def test_input_fingerprint_of_a_directory_covers_its_pdfs(tmp_path) -> None:
    folder = tmp_path / "split"
    folder.mkdir()
    (folder / "a.pdf").write_bytes(b"%PDF a")
    (folder / "b.pdf").write_bytes(b"%PDF b")
    before = input_fingerprint(folder)

    assert before
    assert input_fingerprint(folder) == before  # stable across calls

    (folder / "c.pdf").write_bytes(b"%PDF c")
    assert input_fingerprint(folder) != before


def test_input_fingerprint_ignores_non_pdfs_and_subdirectories(tmp_path) -> None:
    """It must cover exactly the files the stage will read — no more."""
    folder = tmp_path / "split"
    (folder / "nested").mkdir(parents=True)
    (folder / "a.pdf").write_bytes(b"%PDF a")
    before = input_fingerprint(folder)

    (folder / "notes.txt").write_text("hello")
    (folder / "nested" / "deep.pdf").write_bytes(b"%PDF deep")
    assert input_fingerprint(folder) == before


def test_input_fingerprint_of_a_missing_or_empty_path_is_never_reusable(tmp_path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert input_fingerprint(tmp_path / "nope") == ""
    assert input_fingerprint(empty) == ""
    assert not record_is_reusable(
        _record(source_sha256=""), output_dir=tmp_path, source_sha256="", settings_id="sss"
    )

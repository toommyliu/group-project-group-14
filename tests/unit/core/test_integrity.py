from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from opengrader.core.integrity import (
    BACKUP_MANIFEST_NAME,
    FileRecord,
    IntegrityError,
    IntegritySnapshot,
    build_snapshot,
    compare_measurements,
    create_backup,
    default_backup_path,
    hash_bytes,
    is_append_only,
    parse_snapshot,
    read_backup_manifest,
    read_snapshot,
    render_snapshot,
    resolve_snapshot_path,
    restore_backup,
    summarize_report,
    verify_backup,
    verify_folder,
    write_snapshot,
)

NOW = datetime(2026, 8, 4, 9, 0, 0, tzinfo=timezone.utc)


def _snapshot(*records: FileRecord) -> IntegritySnapshot:
    return IntegritySnapshot(created_at=NOW.isoformat(), root="/x", files=records)


def _record(path: str, data: bytes, *, append_only: bool = False) -> FileRecord:
    return FileRecord(
        path=path, size=len(data), sha256=hash_bytes(data), append_only=append_only
    )


def test_identical_bytes_are_unchanged() -> None:
    data = b"hello"
    report = compare_measurements(
        _snapshot(_record("a.md", data)), {"a.md": (len(data), hash_bytes(data), "")}
    )
    assert report.count("unchanged") == 1
    assert report.ok


def test_different_bytes_are_modified() -> None:
    report = compare_measurements(
        _snapshot(_record("a.md", b"hello")), {"a.md": (5, hash_bytes(b"world"), "")}
    )
    assert report.count("modified") == 1
    assert not report.ok


def test_a_recorded_file_that_vanished_is_missing() -> None:
    report = compare_measurements(_snapshot(_record("a.md", b"hi")), {})
    assert report.count("missing") == 1


def test_a_new_file_is_added_and_is_not_a_concern() -> None:
    report = compare_measurements(_snapshot(), {"new.png": (3, hash_bytes(b"abc"), "")})
    assert report.count("added") == 1
    assert report.ok


def test_an_append_only_log_that_grew_with_its_prefix_intact_is_appended() -> None:
    original = b'{"a":1}\n'
    grown = original + b'{"a":2}\n'
    report = compare_measurements(
        _snapshot(_record("log.jsonl", original, append_only=True)),
        {"log.jsonl": (len(grown), hash_bytes(grown), hash_bytes(original))},
    )
    assert report.count("appended") == 1
    assert report.ok


def test_an_append_only_log_whose_recorded_lines_changed_is_rewritten() -> None:
    original = b'{"a":1}\n'
    tampered = b'{"a":9}\n{"a":2}\n'
    report = compare_measurements(
        _snapshot(_record("log.jsonl", original, append_only=True)),
        {"log.jsonl": (len(tampered), hash_bytes(tampered), hash_bytes(b'{"a":9}\n'))},
    )
    assert [f.status for f in report.concerning] == ["rewritten"]


def test_a_truncated_append_only_log_is_rewritten() -> None:
    original = b'{"a":1}\n{"a":2}\n'
    report = compare_measurements(
        _snapshot(_record("log.jsonl", original, append_only=True)),
        {"log.jsonl": (0, hash_bytes(b""), "")},
    )
    finding = report.concerning[0]
    assert finding.status == "rewritten" and "shrank" in finding.detail


def test_an_unreadable_file_is_reported_not_assumed_fine() -> None:
    report = compare_measurements(_snapshot(_record("a.md", b"hi")), {"a.md": (0, "", "")})
    assert [f.status for f in report.concerning] == ["unreadable"]


def test_only_jsonl_is_treated_as_append_only() -> None:
    assert is_append_only(".opengrader/history/x.jsonl")
    assert not is_append_only("a.submission.md")


def test_a_change_with_recorded_grading_is_labelled_explained() -> None:
    report = compare_measurements(
        _snapshot(_record("a.submission.md", b"hi")),
        {"a.submission.md": (2, hash_bytes(b"ho"), "")},
        activity={"a.submission.md": 4},
    )
    finding = report.findings[0]
    assert finding.recorded_activity == 4
    assert "4 grading action(s) recorded since" in finding.describe()
    assert not report.unexplained


def test_a_change_with_no_recorded_grading_is_the_unexplained_one() -> None:
    report = compare_measurements(
        _snapshot(_record("a.submission.md", b"hi")),
        {"a.submission.md": (2, hash_bytes(b"ho"), "")},
        activity={"a.submission.md": 0},
    )
    assert [f.path for f in report.unexplained] == ["a.submission.md"]
    assert "no grading activity recorded since" in report.findings[0].describe()


def test_a_change_nobody_asked_about_is_neither(tmp_path: Path) -> None:
    report = compare_measurements(
        _snapshot(_record("responses/q1.png", b"hi")),
        {"responses/q1.png": (2, hash_bytes(b"ho"), "")},
    )
    assert report.findings[0].recorded_activity is None
    assert not report.unexplained
    assert report.concerning


def test_a_snapshot_round_trips() -> None:
    snapshot = _snapshot(_record("a.md", b"hi"), _record("l.jsonl", b"x", append_only=True))
    restored = parse_snapshot(render_snapshot(snapshot))
    assert restored.created_at == snapshot.created_at
    assert restored.by_path()["l.jsonl"].append_only is True


def test_a_snapshot_from_a_newer_format_is_refused() -> None:
    with pytest.raises(IntegrityError, match="newer Opengrader"):
        parse_snapshot('{"snapshot_format": 99, "files": []}')


def test_a_snapshot_with_no_file_list_is_refused_not_read_as_empty() -> None:
    with pytest.raises(IntegrityError, match="no file list"):
        parse_snapshot('{"created_at": "x"}')


@pytest.fixture()
def folder(tmp_path: Path) -> Path:
    root = tmp_path / "subs"
    (root / "sub-0001" / "responses").mkdir(parents=True)
    (root / "sub-0001" / "sub-0001.submission.md").write_text("grades")
    (root / "sub-0001" / "responses" / "q1.png").write_bytes(b"crop")
    (root / ".opengrader" / "history").mkdir(parents=True)
    (root / ".opengrader" / "history" / "grade-journal-a.jsonl").write_text("{}\n")
    return root


def test_a_snapshot_covers_the_dot_dir_but_not_itself(folder: Path) -> None:
    snapshot = build_snapshot(folder)
    paths = {record.path for record in snapshot.files}
    assert ".opengrader/history/grade-journal-a.jsonl" in paths
    write_snapshot(folder, snapshot)
    assert ".opengrader/integrity.json" not in {r.path for r in build_snapshot(folder).files}


def test_the_disposable_image_cache_is_not_tracked(folder: Path) -> None:
    (folder / ".tui_cache").mkdir()
    (folder / ".tui_cache" / "x.png").write_bytes(b"junk")
    assert not any(".tui_cache" in r.path for r in build_snapshot(folder).files)


def test_verify_sees_an_edit_and_an_append(folder: Path) -> None:
    write_snapshot(folder, build_snapshot(folder))
    (folder / "sub-0001" / "responses" / "q1.png").write_bytes(b"corrupted")
    with (folder / ".opengrader" / "history" / "grade-journal-a.jsonl").open("a") as h:
        h.write("{}\n")

    report = verify_folder(folder, explain=False)
    by_path = {f.path: f.status for f in report.findings}
    assert by_path["sub-0001/responses/q1.png"] == "modified"
    assert by_path[".opengrader/history/grade-journal-a.jsonl"] == "appended"
    assert "modified" in summarize_report(report)


def test_verify_without_a_snapshot_says_what_to_do(folder: Path) -> None:
    with pytest.raises(IntegrityError, match="run 'snapshot' first"):
        verify_folder(folder)


def test_reading_a_snapshot_from_a_folder_without_one_is_none(folder: Path) -> None:
    assert read_snapshot(folder) is None
    write_snapshot(folder, build_snapshot(folder))
    assert resolve_snapshot_path(folder).is_file()
    assert read_snapshot(folder) is not None


def test_a_backup_is_verified_against_its_own_manifest(folder: Path, tmp_path: Path) -> None:
    result = create_backup(folder, tmp_path / "b.zip")
    assert result.verified and not result.mismatches
    assert result.file_count == len(build_snapshot(folder).files)
    assert BACKUP_MANIFEST_NAME in zipfile.ZipFile(tmp_path / "b.zip").namelist()


def test_the_manifest_travels_inside_the_archive(folder: Path, tmp_path: Path) -> None:
    create_backup(folder, tmp_path / "b.zip")
    manifest = read_backup_manifest(tmp_path / "b.zip")
    assert any(r.path.endswith(".submission.md") for r in manifest.files)


def test_a_backup_never_overwrites_an_existing_archive(folder: Path, tmp_path: Path) -> None:
    create_backup(folder, tmp_path / "b.zip")
    with pytest.raises(IntegrityError, match="never overwritten"):
        create_backup(folder, tmp_path / "b.zip")


def test_a_damaged_archive_is_caught_by_check(folder: Path, tmp_path: Path) -> None:
    archive = tmp_path / "b.zip"
    create_backup(folder, archive)
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as dest:
        for name in source.namelist():
            data = source.read(name)
            if name.endswith(".submission.md"):
                data = b"tampered"
            dest.writestr(name, data)
    assert verify_backup(tampered)


def test_an_archive_without_a_manifest_is_refused(tmp_path: Path) -> None:
    plain = tmp_path / "plain.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("a.txt", "hi")
    with pytest.raises(IntegrityError, match="not written by Opengrader"):
        read_backup_manifest(plain)


def test_restore_recreates_every_file_and_verifies_it(folder: Path, tmp_path: Path) -> None:
    create_backup(folder, tmp_path / "b.zip")
    result = restore_backup(tmp_path / "b.zip", tmp_path / "recovered")
    assert result.verified
    restored = tmp_path / "recovered" / "sub-0001" / "sub-0001.submission.md"
    assert restored.read_text() == "grades"


def test_restore_refuses_to_write_over_an_existing_folder(
    folder: Path, tmp_path: Path
) -> None:
    create_backup(folder, tmp_path / "b.zip")
    with pytest.raises(IntegrityError, match="already exists"):
        restore_backup(tmp_path / "b.zip", folder)


def test_the_default_archive_sits_beside_the_folder_never_inside_it(
    folder: Path,
) -> None:
    destination = default_backup_path(folder, today=NOW)
    assert destination == folder.parent / f"{folder.name}-backup-2026-08-04.zip"
    assert folder not in destination.parents


def test_a_same_day_second_backup_gets_its_own_name(folder: Path) -> None:
    first = default_backup_path(folder, today=NOW)
    first.write_bytes(b"pretend archive")
    second = default_backup_path(folder, today=NOW)
    assert second != first
    assert not second.exists()


def test_writing_an_archive_inside_the_folder_is_refused(folder: Path) -> None:
    with pytest.raises(IntegrityError, match="inside the folder being backed up"):
        create_backup(folder, folder / "backup.zip")


def test_the_refusal_names_the_default_so_it_is_actionable(folder: Path) -> None:
    with pytest.raises(IntegrityError) as exc_info:
        create_backup(folder, folder / "nested" / "backup.zip")
    assert str(folder.parent) in str(exc_info.value)


def test_a_snapshot_always_lives_in_the_folder_it_describes(folder: Path) -> None:
    assert resolve_snapshot_path(folder) == folder / ".opengrader" / "integrity.json"
    assert resolve_snapshot_path(folder / "sub-0001" / "sub-0001.submission.md") == (
        folder / "sub-0001" / ".opengrader" / "integrity.json"
    )


def test_an_encrypted_backup_round_trips(folder: Path, tmp_path: Path) -> None:
    archive = tmp_path / "quiz1.zip.enc"
    result = create_backup(folder, archive, passphrase="correct horse")

    assert result.verified
    assert result.encrypted
    restored = restore_backup(archive, tmp_path / "out", passphrase="correct horse")
    assert restored.verified
    assert (tmp_path / "out" / "sub-0001" / "sub-0001.submission.md").read_text() == "grades"


def test_an_encrypted_backup_hides_its_contents(folder: Path, tmp_path: Path) -> None:
    archive = tmp_path / "quiz1.zip.enc"
    create_backup(folder, archive, passphrase="p")
    blob = archive.read_bytes()
    assert b"grades" not in blob
    assert b"sub-0001" not in blob


def test_verifying_an_encrypted_backup_needs_the_passphrase(
    folder: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "quiz1.zip.enc"
    create_backup(folder, archive, passphrase="p")

    assert verify_backup(archive, passphrase="p") == ()
    with pytest.raises(IntegrityError, match="encrypted"):
        verify_backup(archive)
    with pytest.raises(IntegrityError):
        verify_backup(archive, passphrase="wrong")


def test_the_read_back_check_still_runs_through_the_encryption(
    folder: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "quiz1.zip.enc"
    result = create_backup(folder, archive, passphrase="p")
    assert result.verified
    assert read_backup_manifest(archive, passphrase="p").files


def test_no_plaintext_archive_is_left_beside_an_encrypted_one(
    folder: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "quiz1.zip.enc"
    create_backup(folder, archive, passphrase="p")
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file()) == ["quiz1.zip.enc"]


def test_an_unencrypted_archive_rejects_a_passphrase(
    folder: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "plain.zip"
    create_backup(folder, archive)
    with pytest.raises(IntegrityError, match="not encrypted"):
        verify_backup(archive, passphrase="p")


def test_an_unencrypted_backup_is_unchanged_by_the_feature(
    folder: Path, tmp_path: Path
) -> None:
    archive = tmp_path / "plain.zip"
    result = create_backup(folder, archive)
    assert result.verified
    assert not result.encrypted
    assert zipfile.is_zipfile(archive)


def test_the_default_name_says_when_a_passphrase_is_needed(folder: Path) -> None:
    assert default_backup_path(folder, encrypted=True).name.endswith(".zip.enc")
    assert default_backup_path(folder).name.endswith(".zip")

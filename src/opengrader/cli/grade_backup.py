"""Checksum, verify, back up and restore a grading folder."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.crypto import (
    EncryptionError,
    is_encrypted,
    read_passphrase,
)
from opengrader.core.integrity import (
    IntegrityError,
    build_snapshot,
    create_backup,
    default_backup_path,
    iter_concerning,
    read_snapshot,
    restore_backup,
    summarize_report,
    verify_backup,
    verify_folder,
    write_snapshot,
)

EPILOG = """\
\b
Examples:
  uv run grade-backup snapshot --submissions out/subs/
  uv run grade-backup verify   --submissions out/subs/
  uv run grade-backup verify   --submissions out/subs/ --fail-on-change
  uv run grade-backup backup   --submissions out/subs/ --output ~/backups/quiz1.zip
  uv run grade-backup check    --archive ~/backups/quiz1.zip
  uv run grade-backup restore  --archive ~/backups/quiz1.zip --output ~/recovered/
  uv run grade-backup backup   --submissions out/subs/ --encrypt

\b
Encryption:
  --encrypt asks for a passphrase (twice) and writes a .zip.enc nobody can read
  without it — for a backup that leaves this machine on a drive or a stick.
  check/restore notice an encrypted archive on their own and ask. There is no
  --passphrase flag on purpose: it would be saved in your shell history and
  visible to every other program running on the computer. For an unattended
  script, set the OPENGRADER_PASSPHRASE environment variable instead.

\b
What the statuses mean:
  unchanged   byte-identical to the snapshot
  appended    an append-only log grew, and its recorded lines are intact (normal)
  added       a new file the snapshot never saw (normal while grading)
  modified    an ordinary file's contents differ
  rewritten   an append-only log's recorded lines changed — history was edited
  missing     recorded, but no longer on disk
"""


_submissions_option = click.option("--submissions", required=True)
_archive_option = click.option("--archive", "archive_file", required=True)


@click.group(
    "grade-backup",
    cls=Group,
    context_settings=CONTEXT_SETTINGS,
    epilog=EPILOG,
)
def command() -> None:
    """Fingerprint a grading folder, verify it later, and make verified
    backups of it.
    """


def _resolve(raw: str, *, what: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: {what} not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


@command.command("snapshot")
@_submissions_option
def snapshot_command(submissions: str) -> None:
    """Record a checksum of every file in the folder."""
    folder = _resolve(submissions, what="submissions path")
    previous = read_snapshot(folder)
    snapshot = build_snapshot(folder)
    path = write_snapshot(folder, snapshot)
    print(
        f"Recorded {len(snapshot.files)} file(s), "
        f"{snapshot.total_bytes:,} bytes → {path}"
    )
    if previous is not None:
        print(f"(Replaced the snapshot taken at {previous.created_at}.)")


@command.command("verify")
@_submissions_option
@click.option(
    "--all",
    "show_all",
    is_flag=True,
    help="List every file, not only the ones that need attention.",
)
@click.option(
    "--no-explain",
    is_flag=True,
    help="Skip the audit-journal cross-reference (faster on a long journal).",
)
@click.option(
    "--fail-on-change",
    is_flag=True,
    help="Exit 2 if anything needs attention — for scripting.",
)
def verify_command(
    submissions: str, show_all: bool, no_explain: bool, fail_on_change: bool
) -> None:
    """Compare the folder against its recorded snapshot."""
    folder = _resolve(submissions, what="submissions path")
    try:
        report = verify_folder(folder, explain=not no_explain)
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Snapshot taken {report.snapshot_at}")
    print(f"{summarize_report(report)}\n")

    if show_all:
        for finding in report.findings:
            print(f"  {finding.describe()}")
        print()

    concerning = list(iter_concerning(report))
    if concerning:
        print(f"{len(concerning)} file(s) need attention:")
        for finding in concerning:
            print(f"  {finding.describe()}")
        unexplained = report.unexplained
        if unexplained:
            print(
                f"\n{len(unexplained)} of those changed with no grading recorded "
                "for that student since the snapshot — check these first:"
            )
            for finding in unexplained:
                print(f"  {finding.path}")
    else:
        print("Nothing needs attention.")

    if fail_on_change and concerning:
        sys.exit(2)


def _passphrase(wanted: bool, *, confirm: bool = False) -> str:
    if not wanted:
        return ""
    try:
        return read_passphrase(confirm=confirm)
    except EncryptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _passphrase_for(archive: Path) -> str:
    return _passphrase(is_encrypted(archive))


@command.command("backup")
@_submissions_option
@click.option(
    "--output",
    default="",
    help=(
        "Archive path to create (.zip). Default: <folder>-backup-<date>.zip "
        "beside the submissions directory — never inside it, since a backup "
        "kept there is lost with what it protects."
    ),
)
@click.option(
    "--encrypt",
    is_flag=True,
    help=(
        "Protect the archive with a passphrase (prompted, never typed on "
        "the command line). Writes <name>.zip.enc."
    ),
)
@click.option(
    "--snapshot",
    "record_snapshot",
    is_flag=True,
    help="Also record the snapshot into the folder, so verify works after.",
)
def backup_command(
    submissions: str, output: str, encrypt: bool, record_snapshot: bool
) -> None:
    """Write a verified zip archive of the folder."""
    folder = _resolve(submissions, what="submissions path")
    passphrase = _passphrase(encrypt, confirm=True)
    destination = (
        Path(output).expanduser()
        if output.strip()
        else default_backup_path(folder, encrypted=bool(passphrase))
    )
    try:
        result = create_backup(folder, destination, passphrase=passphrase)
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(
        f"Backed up {result.file_count} file(s), {result.total_bytes:,} bytes → "
        f"{result.archive_path}"
    )
    if result.encrypted:
        print(
            "Encrypted. Nobody — including you — can read this archive without "
            "that passphrase, so store it somewhere you will still have it in a "
            "year."
        )
    if result.verified:
        print("Archive read back and verified against its own manifest.")
    else:
        print(
            f"WARNING: {len(result.mismatches)} file(s) in the archive do not "
            "match the manifest — do not rely on this backup:",
            file=sys.stderr,
        )
        for path in result.mismatches:
            print(f"  {path}", file=sys.stderr)
        sys.exit(2)

    if record_snapshot:
        snapshot = build_snapshot(folder)
        write_snapshot(folder, snapshot)
        print("Snapshot recorded in the folder — 'verify' now works against it.")


@command.command("check")
@_archive_option
def check_command(archive_file: str) -> None:
    """Verify an archive against the manifest inside it."""
    archive = _resolve(archive_file, what="archive")
    try:
        mismatches = verify_backup(archive, passphrase=_passphrase_for(archive))
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    if not mismatches:
        print(f"{archive}: intact.")
        return
    print(f"{archive}: {len(mismatches)} file(s) do not match the manifest:", file=sys.stderr)
    for path in mismatches:
        print(f"  {path}", file=sys.stderr)
    sys.exit(2)


@command.command("restore")
@_archive_option
@click.option("--output", required=True, help="Directory to create (must not exist).")
def restore_command(archive_file: str, output: str) -> None:
    """Extract an archive into a new directory and verify it."""
    archive = _resolve(archive_file, what="archive")
    try:
        result = restore_backup(
            archive,
            Path(output).expanduser(),
            passphrase=_passphrase_for(archive),
        )
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Restored {result.file_count} file(s) → {Path(output).expanduser()}")
    if not result.verified:
        print(
            f"WARNING: {len(result.mismatches)} restored file(s) do not match "
            "the manifest.",
            file=sys.stderr,
        )
        sys.exit(2)
    print("Every restored file matches the archive's manifest.")


if __name__ == "__main__":
    command()

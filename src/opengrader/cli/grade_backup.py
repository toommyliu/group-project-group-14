"""Checksum, verify, back up and restore a grading folder.

Everything Opengrader keeps is a file in one directory, and there is no server
holding a second copy. The failures that follow from that are quiet ones: a
drive yanked mid-write, a sync client resolving a conflict by picking a side, a
stray command in the wrong folder. Nothing raises; a grade is simply different
than it was.

    # record what is here now (do this before anything risky)
    uv run grade-backup snapshot --submissions out/subs/

    # ...later: what moved?
    uv run grade-backup verify --submissions out/subs/

    # one verified archive of the lot
    uv run grade-backup backup --submissions out/subs/ --output ~/backups/quiz1.zip

    # check an old archive is still readable, years later
    uv run grade-backup check --archive ~/backups/quiz1.zip

    # unpack it somewhere new (never on top of an existing folder)
    uv run grade-backup restore --archive ~/backups/quiz1.zip --output ~/recovered/

``verify`` reads the audit journal too, so a changed ``.submission.md`` is
reported as *explained* (grading was recorded for that student since) or
*unexplained* (it changed and nothing recorded it) — which is the difference
between ordinary work and the thing worth investigating.
"""

from __future__ import annotations

import sys
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter
from pathlib import Path

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

EPILOG = """
examples:
  uv run grade-backup snapshot --submissions out/subs/
  uv run grade-backup verify   --submissions out/subs/
  uv run grade-backup verify   --submissions out/subs/ --fail-on-change
  uv run grade-backup backup   --submissions out/subs/ --output ~/backups/quiz1.zip
  uv run grade-backup check    --archive ~/backups/quiz1.zip
  uv run grade-backup restore  --archive ~/backups/quiz1.zip --output ~/recovered/
  uv run grade-backup backup   --submissions out/subs/ --encrypt

encryption:
  --encrypt asks for a passphrase (twice) and writes a .zip.enc nobody can read
  without it — for a backup that leaves this machine on a drive or a stick.
  check/restore notice an encrypted archive on their own and ask. There is no
  --passphrase flag on purpose: it would be saved in your shell history and
  visible to every other program running on the computer. For an unattended
  script, set the OPENGRADER_PASSPHRASE environment variable instead.

what the statuses mean:
  unchanged   byte-identical to the snapshot
  appended    an append-only log grew, and its recorded lines are intact (normal)
  added       a new file the snapshot never saw (normal while grading)
  modified    an ordinary file's contents differ
  rewritten   an append-only log's recorded lines changed — history was edited
  missing     recorded, but no longer on disk
"""


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Fingerprint a grading folder, verify it later, and make verified "
            "backups of it."
        ),
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser(
        "snapshot", help="Record a checksum of every file in the folder."
    )
    snapshot.add_argument("--submissions", required=True)

    verify = subparsers.add_parser(
        "verify", help="Compare the folder against its recorded snapshot."
    )
    verify.add_argument("--submissions", required=True)
    verify.add_argument(
        "--all",
        action="store_true",
        help="List every file, not only the ones that need attention.",
    )
    verify.add_argument(
        "--no-explain",
        action="store_true",
        help="Skip the audit-journal cross-reference (faster on a long journal).",
    )
    verify.add_argument(
        "--fail-on-change",
        action="store_true",
        help="Exit 2 if anything needs attention — for scripting.",
    )

    backup = subparsers.add_parser(
        "backup", help="Write a verified zip archive of the folder."
    )
    backup.add_argument("--submissions", required=True)
    backup.add_argument(
        "--output",
        default="",
        help=(
            "Archive path to create (.zip). Default: <folder>-backup-<date>.zip "
            "beside the submissions directory — never inside it, since a backup "
            "kept there is lost with what it protects."
        ),
    )
    backup.add_argument(
        "--encrypt",
        action="store_true",
        help=(
            "Protect the archive with a passphrase (prompted, never typed on "
            "the command line). Writes <name>.zip.enc."
        ),
    )
    backup.add_argument(
        "--snapshot",
        action="store_true",
        help="Also record the snapshot into the folder, so verify works after.",
    )

    check = subparsers.add_parser(
        "check", help="Verify an archive against the manifest inside it."
    )
    check.add_argument("--archive", required=True)

    restore = subparsers.add_parser(
        "restore", help="Extract an archive into a new directory and verify it."
    )
    restore.add_argument("--archive", required=True)
    restore.add_argument(
        "--output", required=True, help="Directory to create (must not exist)."
    )

    return parser


def _resolve(raw: str, *, what: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: {what} not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _cmd_snapshot(args: Namespace) -> None:
    folder = _resolve(args.submissions, what="submissions path")
    previous = read_snapshot(folder)
    snapshot = build_snapshot(folder)
    path = write_snapshot(folder, snapshot)
    print(
        f"Recorded {len(snapshot.files)} file(s), "
        f"{snapshot.total_bytes:,} bytes → {path}"
    )
    if previous is not None:
        # Overwriting a snapshot silently would let someone "fix" a failing
        # verify by re-recording the damage as the new normal, so say it moved.
        print(f"(Replaced the snapshot taken at {previous.created_at}.)")


def _cmd_verify(args: Namespace) -> None:
    folder = _resolve(args.submissions, what="submissions path")
    try:
        report = verify_folder(folder, explain=not args.no_explain)
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Snapshot taken {report.snapshot_at}")
    print(f"{summarize_report(report)}\n")

    if args.all:
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
            # Printed last because it is the conclusion, not another list: these
            # are the files that changed with nothing in the audit trail saying
            # why, which is the signal the whole cross-reference exists for.
            print(
                f"\n{len(unexplained)} of those changed with no grading recorded "
                "for that student since the snapshot — check these first:"
            )
            for finding in unexplained:
                print(f"  {finding.path}")
    else:
        print("Nothing needs attention.")

    if args.fail_on_change and concerning:
        sys.exit(2)


def _passphrase(wanted: bool, *, confirm: bool = False) -> str:
    """Collect a passphrase when one was asked for, else return "".

    Exits with a message rather than a traceback: "the two passphrases do not
    match" is an ordinary thing to do, not a crash.
    """
    if not wanted:
        return ""
    try:
        return read_passphrase(confirm=confirm)
    except EncryptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _passphrase_for(archive: Path) -> str:
    """Ask for a passphrase only when the archive actually needs one.

    Detected from the file itself rather than from a flag the user has to
    remember to pass — a year later nobody recalls which archives were
    encrypted, and the file already knows.
    """
    return _passphrase(is_encrypted(archive))


def _cmd_backup(args: Namespace) -> None:
    folder = _resolve(args.submissions, what="submissions path")
    # Asked for before anything is built, and confirmed by typing it twice: a
    # mistyped passphrase cannot be recovered from, and discovering that at the
    # end of a ten-minute archive of a 300-student cohort wastes both.
    passphrase = _passphrase(args.encrypt, confirm=True)
    destination = (
        Path(args.output).expanduser()
        if args.output.strip()
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
        # Stated as a consequence, not a fact: the passphrase is now the only
        # thing standing between this archive and being unreadable forever.
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

    if args.snapshot:
        snapshot = build_snapshot(folder)
        write_snapshot(folder, snapshot)
        print("Snapshot recorded in the folder — 'verify' now works against it.")


def _cmd_check(args: Namespace) -> None:
    archive = _resolve(args.archive, what="archive")
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


def _cmd_restore(args: Namespace) -> None:
    archive = _resolve(args.archive, what="archive")
    try:
        result = restore_backup(
            archive,
            Path(args.output).expanduser(),
            passphrase=_passphrase_for(archive),
        )
    except IntegrityError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"Restored {result.file_count} file(s) → {Path(args.output).expanduser()}")
    if not result.verified:
        print(
            f"WARNING: {len(result.mismatches)} restored file(s) do not match "
            "the manifest.",
            file=sys.stderr,
        )
        sys.exit(2)
    print("Every restored file matches the archive's manifest.")


_COMMANDS = {
    "snapshot": _cmd_snapshot,
    "verify": _cmd_verify,
    "backup": _cmd_backup,
    "check": _cmd_check,
    "restore": _cmd_restore,
}


def run_cli(argv: list[str] | None = None) -> None:
    args = _build_parser().parse_args(argv)
    _COMMANDS[args.command](args)

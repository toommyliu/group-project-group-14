"""Create grader copies and merge their changes back."""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from pathlib import Path

import click

from opengrader.cli import CONTEXT_SETTINGS, Group
from opengrader.core.handoff.merge import (
    ConflictPolicy,
    MergeError,
    MergePlan,
    alias_translation,
    apply_merge_plan,
    build_merge_plan,
    import_incoming_journal,
    locate_handoff_key,
    merge_actor,
    resolve_conflicts,
    resolve_incoming_root,
)
from opengrader.core.crypto import EncryptionError, read_passphrase
from opengrader.core.handoff.packet import (
    PacketError,
    build_packet,
    default_packet_output_dir,
    is_sealed_packet,
    list_handoff_keys,
    opened_packet,
    resolve_handoff_dir,
    seal_packet,
)
from opengrader.core.history import GradeJournal, resolve_history_dir

EPILOG = """\
\b
Examples:
  opengrader grade-merge pack --submissions out/subs/ --output ~/handouts --grader alice --qa-file qa.md
  opengrader grade-merge pack --submissions out/subs/ --output ~/handouts --name quiz1-alice
  opengrader grade-merge list --submissions out/subs/
  opengrader grade-merge preview --submissions out/subs/ --incoming ~/from-alice/
  opengrader grade-merge apply --submissions out/subs/ --incoming ~/from-alice/ --yes
  opengrader grade-merge apply --submissions out/subs/ --incoming ~/from-alice/ --conflicts theirs --yes

\b
  opengrader grade-merge pack --submissions out/subs/ --grader alice --encrypt

\b
Encryption:
  --encrypt seals the packet into one passphrase-protected file instead of a
  folder, for a copy that travels by email or on a stick. preview/apply notice a
  sealed packet on their own and ask for the passphrase. There is no
  --passphrase flag on purpose: it would be saved in your shell history and
  visible to every other program on the computer. For a script, set the
  OPENGRADER_PASSPHRASE environment variable instead.

\b
  Redaction and encryption solve different problems and compose: redaction stops
  the *grader* seeing who they are marking, encryption stops *everyone else*
  seeing the packet at all.

\b
Undo a merge:
  opengrader grade-restore apply --submissions out/subs/ --without-actor merge:<packet-id> --yes
"""


def _merge_options(function):
    function = click.option(
        "--packet",
        default="",
        help="Packet id, if the returned folder lost its manifest.",
    )(function)
    function = click.option(
        "--key",
        "raw_key",
        default="",
        help=(
            "Handoff key for the packet. Found automatically from the "
            "packet's id; give it only if you moved the file."
        ),
    )(function)
    function = click.option(
        "--incoming",
        "raw_incoming",
        required=True,
        help=(
            "The returned packet: a directory, or the sealed .zip.enc file "
            "if it was packed with --encrypt."
        ),
    )(function)
    return click.option(
        "--submissions",
        "raw_submissions",
        required=True,
        help="Your master submissions directory.",
    )(function)


@click.group("grade-merge", cls=Group, context_settings=CONTEXT_SETTINGS, epilog=EPILOG)
def command() -> None:
    """Hand a redacted copy of a cohort to another grader, then three-way
    merge their grades back into yours.
    """


def _resolve_path(raw: str, *, what: str) -> Path:
    path = Path(raw).expanduser()
    if not path.exists():
        print(f"Error: {what} not found: {path}", file=sys.stderr)
        sys.exit(1)
    return path


def _passphrase(wanted: bool, *, confirm: bool = False) -> str:
    if not wanted:
        return ""
    try:
        return read_passphrase(confirm=confirm)
    except EncryptionError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


@command.command("pack")
@click.option(
    "--submissions",
    "raw_submissions",
    required=True,
    help="The master submissions directory.",
)
@click.option(
    "--output",
    "raw_output",
    default="",
    help=(
        "Directory to create the packet folder in. Default: a "
        "grader-copies/ folder beside the submissions directory."
    ),
)
@click.option(
    "--name",
    default="",
    help="Packet id (also the folder name). Defaults to <assignment>-for-<grader>.",
)
@click.option(
    "--grader", default="", help="Who the packet is for; used in the default name."
)
@click.option(
    "--qa-file",
    "raw_qa_file",
    default="",
    help="Answer key to copy in, so the grader can open the packet directly.",
)
@click.option(
    "--no-redact",
    is_flag=True,
    help=(
        "Keep real names and ids. Only for a trusted co-teacher — the copy "
        "is then no more anonymous than the original."
    ),
)
@click.option(
    "--encrypt",
    is_flag=True,
    help=(
        "Seal the packet into one passphrase-protected file (prompted, "
        "never typed on the command line) instead of a folder."
    ),
)
@click.option(
    "--with-pdfs",
    is_flag=True,
    help=(
        "Also copy the scanned PDFs (whole-page context). Incompatible with "
        "redaction: the student's name is written on every sheet."
    ),
)
def pack_command(
    raw_submissions: str,
    raw_output: str,
    name: str,
    grader: str,
    raw_qa_file: str,
    no_redact: bool,
    encrypt: bool,
    with_pdfs: bool,
) -> None:
    """Create an anonymous grader copy of this cohort."""
    submissions = _resolve_path(raw_submissions, what="submissions path")
    passphrase = _passphrase(encrypt, confirm=True)
    output = (
        Path(raw_output).expanduser()
        if raw_output.strip()
        else default_packet_output_dir(submissions)
    )
    qa_file = (
        _resolve_path(raw_qa_file, what="QA file") if raw_qa_file.strip() else None
    )
    try:
        result = build_packet(
            submissions,
            output,
            packet_id=name,
            grader=grader,
            qa_file_path=qa_file,
            redact=not no_redact,
            include_pdfs=with_pdfs,
        )
    except PacketError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(2)

    sealed_path: Path | None = None
    if passphrase:
        try:
            sealed_path = seal_packet(result.packet_path, passphrase)
        except PacketError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            sys.exit(2)

    print(f"Packet {result.manifest.packet_id} created.")
    if sealed_path is not None:
        print(f"  file:     {sealed_path}")
    else:
        print(f"  folder:   {result.packet_path}")
    print(f"  students: {result.student_count}")
    print(f"  crops:    {result.copied_crops}")
    if result.qa_file_path is not None:
        print(f"  answer key: {result.qa_file_path.name}")
    if result.skipped_crops:
        print(
            f"  note: {len(result.skipped_crops)} crop(s) live outside their "
            "submission folder and were left behind; those answers travel as "
            "text only:",
            file=sys.stderr,
        )
        for item in result.skipped_crops:
            print(f"    - {item}", file=sys.stderr)

    if sealed_path is not None:
        print(
            "\nSealed with a passphrase. The grader needs it to open the packet, "
            "so tell them some other way than the message carrying the file — "
            "a passphrase emailed alongside its own packet protects nothing."
        )

    print(f"\nHandoff key kept here: {result.key_path}")
    what_to_send = "packet file" if sealed_path is not None else "packet folder"
    if result.manifest.redacted:
        print(
            f"Send the {what_to_send}. Do NOT send the key — it is the only "
            "thing that turns the codes back into students, and merging the "
            "returned copy needs it."
        )
    else:
        print("This copy is NOT redacted: it carries real names and ids.")


@command.command("list")
@click.option("--submissions", "raw_submissions", required=True)
def list_command(raw_submissions: str) -> None:
    """Show the packets cut from this cohort and where they went."""
    submissions = _resolve_path(raw_submissions, what="submissions path")
    keys = list_handoff_keys(submissions)
    if not keys:
        print(
            f"No packets have been cut from {submissions}.\n"
            f"(Keys would live in {resolve_handoff_dir(submissions)}.)"
        )
        return
    print(f"{len(keys)} packet(s) from {submissions}:\n")
    for key in keys:
        print(f"  {key.packet_id}")
        print(f"    created:  {key.created_at} by {key.created_by or 'unknown'}")
        print(f"    students: {len(key.members)}")
        print(f"    packet:   {key.packet_path}")
        print()


def _print_plan(plan: MergePlan) -> None:
    if plan.changes:
        print(f"{len(plan.changes)} change(s) to take, across {plan.student_count} student(s):\n")
        for change in plan.changes:
            marker = "+" if change.reason == "filled_blank" else "→"
            print(f"  {marker} {change.describe()}")
        print()
    if plan.conflicts:
        print(
            f"{len(plan.conflicts)} conflict(s) — both copies changed these, "
            "so nothing is written until you choose:\n"
        )
        for conflict in plan.conflicts:
            note = (
                "no baseline to compare against"
                if conflict.reason == "no_baseline"
                else "both edited"
            )
            print(f"  ! {conflict.describe()}  [{note}]")
        print()
    print(
        f"Also: {plan.already_agree} answer(s) already agree, "
        f"{plan.kept_ours} kept from your copy."
    )


def _print_caveats(plan: MergePlan, ours, theirs) -> None:  # noqa: ANN001
    if plan.unmatched_students:
        print(
            f"\n{len(plan.unmatched_students)} submission(s) in the returned copy "
            "could not be matched to a student here:",
            file=sys.stderr,
        )
        for key in plan.unmatched_students:
            print(f"  - {key}", file=sys.stderr)
    if plan.unknown_questions:
        print(
            f"\n{len(plan.unknown_questions)} answer(s) have no matching question "
            "in your copy and were skipped:",
            file=sys.stderr,
        )
        for item in plan.unknown_questions:
            print(f"  - {item}", file=sys.stderr)
    for label, snapshot in (("your copy", ours), ("the returned copy", theirs)):
        for problem in snapshot.unreadable:
            print(f"Warning: unreadable in {label}: {problem}", file=sys.stderr)


def _plan_json(plan: MergePlan) -> str:
    return json.dumps(
        {
            "changes": [
                {
                    "student": change.student_label,
                    "student_key": change.student_key,
                    "question": change.question_id,
                    "aspect": change.aspect,
                    "reason": change.reason,
                }
                for change in plan.changes
            ],
            "conflicts": [
                {
                    "id": conflict.conflict_id,
                    "student": conflict.student_label,
                    "question": conflict.question_id,
                    "aspect": conflict.aspect,
                    "reason": conflict.reason,
                }
                for conflict in plan.conflicts
            ],
            "already_agree": plan.already_agree,
            "kept_ours": plan.kept_ours,
            "unmatched_students": list(plan.unmatched_students),
            "unknown_questions": list(plan.unknown_questions),
        },
        indent=2,
    )


@contextmanager
def _prepare_merge(  # noqa: ANN202
    *, raw_submissions: str, raw_incoming: str, raw_key: str, packet: str
):
    ours = _resolve_path(raw_submissions, what="submissions path")
    incoming = _resolve_path(raw_incoming, what="incoming path")
    passphrase = _passphrase(is_sealed_packet(incoming))
    try:
        with opened_packet(incoming, passphrase) as unpacked:
            key, manifest = locate_handoff_key(
                ours,
                unpacked,
                explicit_key=raw_key.strip() or None,
                packet_id=packet,
            )
            incoming_root = resolve_incoming_root(unpacked)
            plan, ours_side, theirs_side = build_merge_plan(
                ours, incoming_root, handoff_key=key, manifest=manifest
            )
            yield ours, incoming_root, key, manifest, plan, ours_side, theirs_side
    except (MergeError, PacketError) as exc:
        print(f"Error: {exc}\nPass --key if you moved the handoff key.", file=sys.stderr)
        sys.exit(2)


@command.command("preview")
@_merge_options
@click.option("--json", "as_json", is_flag=True, help="Emit the plan as JSON.")
def preview_command(
    raw_submissions: str, raw_incoming: str, raw_key: str, packet: str, as_json: bool
) -> None:
    """Show what a returned copy would change, writing nothing."""
    with _prepare_merge(
        raw_submissions=raw_submissions,
        raw_incoming=raw_incoming,
        raw_key=raw_key,
        packet=packet,
    ) as prepared:
        _ours, _incoming, _key, _manifest, plan, ours_side, theirs_side = prepared
        if as_json:
            print(_plan_json(plan))
            return
        if not plan:
            print("Nothing to merge: the two copies already agree.")
            _print_caveats(plan, ours_side, theirs_side)
            return
        _print_plan(plan)
        _print_caveats(plan, ours_side, theirs_side)
        print("\nPreview only. Use 'opengrader grade-merge apply … --yes' to write.")


@command.command("apply")
@_merge_options
@click.option(
    "--conflicts",
    type=click.Choice(("skip", "ours", "theirs")),
    default="skip",
    help=(
        "What to do with answers you both changed: skip (default, "
        "leaves them for a human), keep ours, or take theirs."
    ),
)
@click.option(
    "--yes",
    is_flag=True,
    help="Actually write. Without it the merge is only previewed.",
)
@click.option(
    "--import-history",
    is_flag=True,
    help=(
        "Also copy the other grader's audit journal in (de-aliased), "
        "so their session shows up in grade-history and "
        "grading-stats. Off by default because it costs the "
        "one-command undo — see the note printed after the merge."
    ),
)
def apply_command(
    raw_submissions: str,
    raw_incoming: str,
    raw_key: str,
    packet: str,
    conflicts: ConflictPolicy,
    yes: bool,
    import_history: bool,
) -> None:
    """Merge a returned copy into this cohort."""
    with _prepare_merge(
        raw_submissions=raw_submissions,
        raw_incoming=raw_incoming,
        raw_key=raw_key,
        packet=packet,
    ) as prepared:
        _apply_prepared(
            *prepared, policy=conflicts, yes=yes, import_history=import_history
        )


def _apply_prepared(  # noqa: ANN001
    ours,
    incoming,
    key,
    manifest,
    plan,
    ours_side,
    theirs_side,
    *,
    policy: ConflictPolicy,
    yes: bool,
    import_history: bool,
) -> None:
    resolved = resolve_conflicts(plan.conflicts, policy=policy)
    changes = list(plan.changes) + list(resolved)

    if not changes and not plan.conflicts:
        print("Nothing to merge: the two copies already agree.")
        _print_caveats(plan, ours_side, theirs_side)
        return

    _print_plan(plan)
    if resolved:
        print(f"Conflict policy '{policy}' additionally takes {len(resolved)} answer(s).\n")
    elif plan.conflicts and policy == "skip":
        print(
            "Conflicts are left untouched. Re-run with --conflicts theirs (or "
            "ours) once you have decided, or settle them in the TUI.\n"
        )
    _print_caveats(plan, ours_side, theirs_side)

    if not yes:
        print("\nPreview only. Re-run with --yes to write these changes.")
        return

    label = (manifest.packet_id if manifest else "") or incoming.name
    journal = GradeJournal(resolve_history_dir(ours), actor=merge_actor(label))
    outcome = apply_merge_plan(
        changes, ours_side.paths_by_key, journal=journal, source_label=label
    )
    print(
        f"\nMerged {len(outcome.written)} change(s) into "
        f"{outcome.files_touched} student file(s)."
    )

    undo_actors = [merge_actor(label)]
    if import_history:
        imported = import_incoming_journal(
            incoming,
            ours,
            translate=alias_translation(key) if key is not None else None,
            names_by_key={
                member.student_key: member.student_name for member in key.members
            }
            if key is not None
            else None,
            source_label=label,
        )
        if imported.entries:
            print(
                f"Imported {imported.entries} journal entry(ies) from "
                f"{imported.files} grading session(s) in the returned copy."
            )
            undo_actors.extend(imported.actors)

    undo_flags = " ".join(f"--without-actor {actor}" for actor in undo_actors)
    print(f"\nUndo with:\n  opengrader grade-restore apply --submissions {ours} {undo_flags} --yes")
    if len(undo_actors) > 1:
        print(
            "  (the imported history names the other grader too, so their "
            "entries have to be excluded as well)"
        )

    if outcome.failed:
        print(f"\n{len(outcome.failed)} change(s) could not be written:", file=sys.stderr)
        for change, error in outcome.failed:
            print(f"  {change.describe()}\n    {error}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    command()

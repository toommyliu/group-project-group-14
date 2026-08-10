"""Three-way merge of two copies of one cohort — bring a grader's copy home.

`packet.py` hands out a copy; this is the other half. A TA grades offline, hands
back a folder, and the question is which of the two folders is right about each
answer. "Newest file wins" is not an answer — both files changed, and the parts
that changed are usually *different* answers, so either copy would be wrong.

The merge is therefore the ordinary three-way one, over the same per-answer
vocabulary the journal and the restore already use (`core/history/restore.py`'s
``Aspect`` / ``AspectValue`` / ``CellState``, so a merged grade is written by the
same code that writes a restored one):

    base    what the answer held when the copy was made
    ours    what the master copy holds now
    theirs  what the returned copy holds now

    ours == base, theirs != base   ->  take theirs      (their work)
    ours != base, theirs == base   ->  keep ours        (nothing to do)
    ours == theirs                 ->  already agree
    both changed, and differently  ->  CONFLICT, a human decides

Design decisions:

* **The baseline comes from the packet when there is one.** A packet manifest
  records every answer's value at the moment it was cut, so the merge knows
  exactly what each side started from. That is the difference between a merge
  and a guess.
* **Without a packet, the incoming journal supplies the baseline** — the first
  entry for an answer carries its ``old`` value, which `restore.py` already
  establishes as the pre-journal state. This is what lets two hand-made ``cp
  -r`` copies merge at all.
* **With no baseline for an answer, the merge refuses to overwrite work.** It
  will only fill a blank: if our copy has nothing there and theirs does, taking
  theirs cannot destroy anything. Anything else is a conflict. Guessing here
  would silently discard a grade, which is the one outcome that must not happen
  quietly.
* **Pure planning, isolated application.** Everything down to `plan_merge` is a
  function over cells, so the whole decision table unit-tests with no folders.
* **An applied merge is journalled**, like a restore, under an actor naming the
  packet it came from. That is what makes it undoable:
  ``grade-restore apply --without-actor merge:<packet>`` puts the master copy
  back exactly as it was, so trying a merge is a reversible act. Importing the
  other grader's own journal (`import_incoming_journal`) is therefore opt-in:
  it is worth having, but it costs that one-command undo, and a safety property
  should not be traded for an analytics one without being asked.

Deliberately out of scope: the QA file (a rubric edit propagates across every
student, so reconciling two rubric trees is a different problem with different
rules) and submissions that exist only in the incoming copy (creating a
submission is a copy, not a merge). Both are reported rather than half-done.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Mapping, Sequence

from opengrader.core.export.gradebook import discover_submission_paths
from opengrader.core.history.journal import (
    GradeJournal,
    GradeJournalEntry,
    read_journal,
    resolve_history_dir,
)
from opengrader.core.history.restore import (
    RESTORABLE_ASPECTS,
    Aspect,
    AspectValue,
    CellState,
    current_cells_from_documents,
    document_student_key,
    format_aspect_value,
    journal_payload,
    merge_update_for_question,
    replay_journal,
)
from opengrader.core.submissions.student_file import load_student_submission_markdown

from .packet import (
    PACKET_SUBMISSIONS_DIRNAME,
    HandoffKey,
    PacketManifest,
    read_handoff_key,
    read_packet_manifest,
    resolve_handoff_dir,
    slugify_packet_name,
)

# Why a change was taken automatically, or why one could not be.
ChangeReason = Literal["incoming_edit", "filled_blank"]
ConflictReason = Literal["both_edited", "no_baseline"]
# What to do with every conflict at once, when a human does not want to sit
# through them one at a time.
ConflictPolicy = Literal["skip", "ours", "theirs"]

# A timestamp guaranteed to precede every journal entry. Replaying "as of" it
# lands on each answer's earliest recorded ``old`` — which is precisely the
# pre-copy baseline, reusing the fallback `restore.py` already implements
# rather than re-deriving it here.
_BEFORE_ANY_ENTRY = datetime(1, 1, 1, tzinfo=timezone.utc)


class MergeError(ValueError):
    """A merge could not be planned (unreadable folder, unusable key)."""


@dataclass(frozen=True)
class MergeChange:
    """One aspect of one answer that the merge can take without asking."""

    student_key: str
    student_label: str
    question_id: str
    aspect: Aspect
    ours: AspectValue
    theirs: AspectValue
    reason: ChangeReason

    def describe(self) -> str:
        return (
            f"{self.student_label} · {self.question_id} · {self.aspect}: "
            f"{format_aspect_value(self.aspect, self.ours)} → "
            f"{format_aspect_value(self.aspect, self.theirs)}"
        )


@dataclass(frozen=True)
class MergeConflict:
    """One aspect both copies changed differently — only a human can settle it."""

    student_key: str
    student_label: str
    question_id: str
    aspect: Aspect
    base: AspectValue | None
    ours: AspectValue
    theirs: AspectValue
    reason: ConflictReason

    @property
    def conflict_id(self) -> str:
        """A stable handle for one conflict, so a UI can record a decision."""
        return f"{self.student_key}|{self.question_id}|{self.aspect}"

    def describe(self) -> str:
        return (
            f"{self.student_label} · {self.question_id} · {self.aspect}: "
            f"ours {format_aspect_value(self.aspect, self.ours)} / "
            f"theirs {format_aspect_value(self.aspect, self.theirs)}"
        )


@dataclass(frozen=True)
class MergePlan:
    """Everything the merge decided, and everything it could not decide.

    ``kept_ours`` and ``already_agree`` are counts rather than lists on purpose:
    they are how a reader confirms the merge looked at the whole cohort, but
    naming three hundred unchanged answers would bury the dozen that matter.
    """

    changes: tuple[MergeChange, ...] = ()
    conflicts: tuple[MergeConflict, ...] = ()
    unmatched_students: tuple[str, ...] = ()
    unknown_questions: tuple[str, ...] = ()
    kept_ours: int = 0
    already_agree: int = 0

    def __bool__(self) -> bool:
        return bool(self.changes or self.conflicts)

    @property
    def student_count(self) -> int:
        return len({change.student_key for change in self.changes})

    def conflict_by_id(self) -> dict[str, MergeConflict]:
        return {conflict.conflict_id: conflict for conflict in self.conflicts}


def baseline_from_journal(
    entries: Iterable[GradeJournalEntry],
) -> dict[tuple[str, str], CellState]:
    """Reconstruct each answer's value *before* the incoming journal started.

    Replaying "as of" a time older than any entry makes `restore.py` fall
    through to each answer's earliest recorded ``old`` — the state the copy was
    handed over in. Answers the grader never touched have no entry and so no
    baseline, which is correct: they also have no change to merge.
    """
    snapshot = replay_journal(entries, at=_BEFORE_ANY_ENTRY)
    return dict(snapshot.cells)


def plan_merge(
    ours: Mapping[tuple[str, str], CellState],
    theirs: Mapping[tuple[str, str], CellState],
    base: Mapping[tuple[str, str], CellState] | None = None,
    *,
    labels: Mapping[str, str] | None = None,
    aspects: Sequence[Aspect] = RESTORABLE_ASPECTS,
    unmatched_students: Iterable[str] = (),
) -> MergePlan:
    """Decide, answer by answer, what the merged cohort should look like.

    ``ours``/``theirs``/``base`` are all keyed by ``(student_key, question_id)``
    in *our* vocabulary — the caller translates a packet's aliases before
    calling, so the decision table never has to know packets exist.
    """
    baseline = base or {}
    names = labels or {}

    changes: list[MergeChange] = []
    conflicts: list[MergeConflict] = []
    unknown_questions: list[str] = []
    kept_ours = 0
    already_agree = 0

    for (key, question_id), their_cell in sorted(theirs.items()):
        our_cell = ours.get((key, question_id))
        if our_cell is None:
            # Their copy has an answer ours has no block for (a different
            # assignment version, or a question added after the copy was cut).
            # There is nothing on disk to write it into, so say so instead of
            # failing one write at a time during apply.
            unknown_questions.append(f"{names.get(key, key)} · {question_id}")
            continue

        base_cell = baseline.get((key, question_id))
        label = names.get(key, key)

        for aspect in aspects:
            their_value = their_cell.value(aspect)
            if their_value is None:
                continue
            our_value = our_cell.value(aspect) or AspectValue()
            if our_value.matches(their_value, aspect):
                already_agree += 1
                continue

            base_value = base_cell.value(aspect) if base_cell is not None else None
            if base_value is None:
                # No baseline: only a change that cannot destroy work is safe.
                if _is_empty(aspect, our_value):
                    changes.append(
                        MergeChange(
                            student_key=key,
                            student_label=label,
                            question_id=question_id,
                            aspect=aspect,
                            ours=our_value,
                            theirs=their_value,
                            reason="filled_blank",
                        )
                    )
                else:
                    conflicts.append(
                        MergeConflict(
                            student_key=key,
                            student_label=label,
                            question_id=question_id,
                            aspect=aspect,
                            base=None,
                            ours=our_value,
                            theirs=their_value,
                            reason="no_baseline",
                        )
                    )
                continue

            ours_moved = not our_value.matches(base_value, aspect)
            theirs_moved = not their_value.matches(base_value, aspect)
            if theirs_moved and not ours_moved:
                changes.append(
                    MergeChange(
                        student_key=key,
                        student_label=label,
                        question_id=question_id,
                        aspect=aspect,
                        ours=our_value,
                        theirs=their_value,
                        reason="incoming_edit",
                    )
                )
            elif ours_moved and not theirs_moved:
                # We moved on and they did not: their copy is simply stale here.
                kept_ours += 1
            else:
                conflicts.append(
                    MergeConflict(
                        student_key=key,
                        student_label=label,
                        question_id=question_id,
                        aspect=aspect,
                        base=base_value,
                        ours=our_value,
                        theirs=their_value,
                        reason="both_edited",
                    )
                )

    changes.sort(key=_change_order)
    conflicts.sort(key=_conflict_order)
    return MergePlan(
        changes=tuple(changes),
        conflicts=tuple(conflicts),
        unmatched_students=tuple(sorted(set(unmatched_students))),
        unknown_questions=tuple(sorted(set(unknown_questions))),
        kept_ours=kept_ours,
        already_agree=already_agree,
    )


def _is_empty(aspect: Aspect, value: AspectValue) -> bool:
    """Is this aspect unset — i.e. is there no work here to be destroyed?

    An ungraded question has no points *and* no rubric selection; a comment or a
    transcription is empty when it has no non-whitespace text.
    """
    if aspect == "grade":
        return value.points is None and not value.rubric
    return not value.text.strip()


def _change_order(change: MergeChange) -> tuple:
    return (
        change.student_label.lower(),
        change.student_key,
        change.question_id,
        RESTORABLE_ASPECTS.index(change.aspect),
    )


def _conflict_order(conflict: MergeConflict) -> tuple:
    return (
        conflict.student_label.lower(),
        conflict.student_key,
        conflict.question_id,
        RESTORABLE_ASPECTS.index(conflict.aspect),
    )


def resolve_conflicts(
    conflicts: Iterable[MergeConflict],
    *,
    policy: ConflictPolicy = "skip",
    decisions: Mapping[str, ConflictPolicy] | None = None,
) -> tuple[MergeChange, ...]:
    """Turn settled conflicts into applicable changes.

    ``decisions`` is a per-conflict override keyed by
    :attr:`MergeConflict.conflict_id` — how a UI records one click per row —
    and ``policy`` covers the rest. Keeping ours is not a change (the file
    already says that), so only "take theirs" produces work.
    """
    chosen = decisions or {}
    resolved: list[MergeChange] = []
    for conflict in conflicts:
        decision = chosen.get(conflict.conflict_id, policy)
        if decision != "theirs":
            continue
        resolved.append(
            MergeChange(
                student_key=conflict.student_key,
                student_label=conflict.student_label,
                question_id=conflict.question_id,
                aspect=conflict.aspect,
                ours=conflict.ours,
                theirs=conflict.theirs,
                reason="incoming_edit",
            )
        )
    resolved.sort(key=_change_order)
    return tuple(resolved)


# --------------------------------------------------------------------------
# Everything below touches the filesystem. Keep new decision logic above this
# line so the merge rules stay testable without two folders on disk.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SideSnapshot:
    """One copy of the cohort, read into the shape `plan_merge` compares."""

    cells: Mapping[tuple[str, str], CellState] = field(default_factory=dict)
    paths_by_key: Mapping[str, Path] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)
    # Submissions whose key could not be translated into our vocabulary — an
    # alias missing from the handoff key, or a student added in the copy.
    untranslated: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


def read_side(
    submissions_path: str | Path,
    *,
    translate: Mapping[str, str] | None = None,
) -> SideSnapshot:
    """Load a cohort's answers, optionally re-keying it into our vocabulary.

    ``translate`` maps the *file's* student key onto ours — the alias map of a
    redacted packet. Omitted, keys are taken as they are, which is what merging
    two un-redacted copies needs.

    A submission that will not parse is recorded and skipped rather than
    aborting: one damaged file must not make the other thirty unmergeable.
    """
    base = Path(submissions_path)
    try:
        paths = discover_submission_paths(base)
    except FileNotFoundError as exc:
        raise MergeError(str(exc)) from exc
    if not paths:
        raise MergeError(f"no .submission.md files found under {base}")

    cells: dict[tuple[str, str], CellState] = {}
    paths_by_key: dict[str, Path] = {}
    labels: dict[str, str] = {}
    untranslated: list[str] = []
    unreadable: list[str] = []

    for path in paths:
        try:
            document = load_student_submission_markdown(path)
        except (OSError, ValueError) as exc:
            unreadable.append(f"{path.name}: {exc}")
            continue
        raw_key = document_student_key(document)
        if not raw_key:
            untranslated.append(path.name)
            continue
        key = raw_key if translate is None else translate.get(raw_key, "")
        if not key:
            untranslated.append(raw_key)
            continue

        paths_by_key.setdefault(key, path)
        name = document.student_name.strip()
        if name:
            labels[key] = name
        labels.setdefault(key, document.student_id.strip() or key)
        for cell_key, cell in current_cells_from_documents([document]).items():
            cells[(key, cell_key[1])] = cell

    return SideSnapshot(
        cells=cells,
        paths_by_key=paths_by_key,
        labels=labels,
        untranslated=tuple(untranslated),
        unreadable=tuple(unreadable),
    )


def alias_translation(key: HandoffKey) -> dict[str, str]:
    """Alias → our student key, in the casefolded form journal keys use.

    An alias is minted lowercase, but reading it out of a submission file goes
    through the same normalization every other key does, so translate on the
    normalized form or a stray upper-case alias silently matches nothing.
    """
    return {
        member.alias.casefold(): member.student_key
        for member in key.members
        if member.student_key
    }


def resolve_incoming_root(incoming_path: str | Path) -> Path:
    """The cohort folder inside a returned copy.

    A packet holds its submissions in a ``submissions/`` subfolder, while a
    hand-made copy *is* the cohort folder. Preferring the subfolder when it
    exists is what lets a grader hand the packet back exactly as they received
    it, rather than having to know which level to point at.
    """
    path = Path(incoming_path)
    nested = path / PACKET_SUBMISSIONS_DIRNAME
    return nested if nested.is_dir() else path


def locate_handoff_key(
    ours_path: str | Path,
    incoming_path: str | Path,
    *,
    explicit_key: str | Path | None = None,
    packet_id: str = "",
) -> tuple[HandoffKey | None, PacketManifest | None]:
    """Find the key and manifest for a returned copy.

    Order: an explicitly named key wins, then the packet id (from the returned
    folder's own manifest, or supplied) is looked up in our handoff directory.
    ``(None, None)`` is a valid answer — merging two hand-made copies needs no
    key at all.

    A *redacted* packet whose key is missing raises instead of degrading: with
    no map, every alias would fail to match and the merge would cheerfully
    report that nothing came back, which is the worst possible way to lose an
    evening of someone's grading.
    """
    manifest = read_packet_manifest(incoming_path)

    if explicit_key is not None and str(explicit_key).strip():
        return read_handoff_key(explicit_key), manifest

    wanted_id = packet_id.strip() or (manifest.packet_id if manifest else "")
    if wanted_id:
        candidate = resolve_handoff_dir(ours_path) / f"{wanted_id}.json"
        if candidate.exists():
            return read_handoff_key(candidate), manifest

    if manifest is not None and manifest.redacted:
        expected = resolve_handoff_dir(ours_path) / f"{manifest.packet_id}.json"
        raise MergeError(
            f"{incoming_path} is an anonymous packet "
            f"({manifest.packet_id or 'unknown id'}) and its handoff key was not "
            f"found.\nExpected: {expected}\nWithout the key the codes in the "
            "packet cannot be matched to your students."
        )
    return None, manifest


def build_merge_plan(
    ours_path: str | Path,
    theirs_path: str | Path,
    *,
    handoff_key: HandoffKey | None = None,
    manifest: PacketManifest | None = None,
    aspects: Sequence[Aspect] = RESTORABLE_ASPECTS,
    anonymous: bool = False,
) -> tuple[MergePlan, SideSnapshot, SideSnapshot]:
    """Read both copies and plan the merge, picking the best baseline available.

    Baseline preference, best first: the packet manifest (exact — it recorded
    every answer at the moment the copy was cut), then the incoming journal
    (which knows the ``old`` value of everything the grader actually changed,
    and nothing about what they didn't — which is all that is needed). With
    neither, `plan_merge` falls back to its no-baseline rules.
    """
    translate = alias_translation(handoff_key) if handoff_key is not None else None
    ours = read_side(ours_path)
    theirs = read_side(theirs_path, translate=translate)

    base: dict[tuple[str, str], CellState] = {}
    if manifest is not None and translate is not None:
        for (alias, question_id), cell in manifest.baseline_cells().items():
            key = translate.get(alias.casefold())
            if key:
                base[(key, question_id)] = cell
    elif manifest is not None:
        # An un-redacted packet: the manifest is already keyed the way the
        # submissions are, so it needs no translation.
        base = dict(manifest.baseline_cells())

    if not base:
        incoming_entries = read_journal(resolve_history_dir(theirs_path))
        for (raw_key, question_id), cell in baseline_from_journal(
            incoming_entries
        ).items():
            key = raw_key if translate is None else translate.get(raw_key, "")
            if key:
                base[(key, question_id)] = cell

    # Our copy's names are the only real ones when the incoming copy is
    # redacted, so ours win and theirs only fill gaps. ``anonymous`` drops back
    # to the student key — what the UI shows as a display id — so a merge plan
    # can be read during anonymous grading without naming anyone, the same
    # treatment the consistency and similarity audits get.
    if anonymous:
        labels = {key: key for key in set(ours.labels) | set(theirs.labels)}
    else:
        labels = dict(theirs.labels)
        labels.update(ours.labels)

    plan = plan_merge(
        ours.cells,
        theirs.cells,
        base,
        labels=labels,
        aspects=aspects,
        unmatched_students=theirs.untranslated,
    )
    return plan, ours, theirs


@dataclass(frozen=True)
class MergeOutcome:
    """What `apply_merge_plan` actually wrote."""

    written: tuple[MergeChange, ...] = ()
    failed: tuple[tuple[MergeChange, str], ...] = ()

    @property
    def files_touched(self) -> int:
        return len({change.student_key for change in self.written})


def apply_merge_plan(
    changes: Sequence[MergeChange],
    paths_by_key: Mapping[str, Path],
    *,
    journal: GradeJournal | None = None,
    source_label: str = "",
) -> MergeOutcome:
    """Write merged values into our copy, one write per (student, question).

    Aspects of the same answer are folded into a single update through
    `merge_update_for_question`, so a grade+comment merge cannot leave a file
    half-written — and so a merged grade is written by exactly the code that
    writes a restored one.

    A failure on one answer is recorded and the rest still runs: a merge that
    aborts halfway leaves a cohort in a state nobody chose, which is worse than
    a complete one that names what it could not write.
    """
    # Imported here rather than at module scope: student_file pulls in OpenCV
    # and PIL via the extractor, and the planning half of this module must stay
    # importable (and fast) without them — the same split restore.py makes.
    from opengrader.core.submissions.student_file import update_student_submission_file

    grouped: dict[tuple[str, str], dict[Aspect, MergeChange]] = {}
    for change in changes:
        grouped.setdefault((change.student_key, change.question_id), {})[
            change.aspect
        ] = change

    written: list[MergeChange] = []
    failed: list[tuple[MergeChange, str]] = []

    for (key, question_id), by_aspect in sorted(grouped.items()):
        path = paths_by_key.get(key)
        if path is None:
            for change in by_aspect.values():
                failed.append((change, "no submission file for this student"))
            continue

        update = merge_update_for_question(
            question_id,
            {aspect: change.theirs for aspect, change in by_aspect.items()},
        )
        try:
            update_student_submission_file(path, update)
        except (OSError, ValueError) as exc:
            for change in by_aspect.values():
                failed.append((change, str(exc)))
            continue

        for aspect, change in by_aspect.items():
            written.append(change)
            if journal is None:
                continue
            try:
                journal.append(
                    kind=aspect,
                    student_id="" if key.startswith("name:") else key,
                    student_name=change.student_label,
                    question_id=question_id,
                    summary=_merge_summary(change, source_label),
                    old=journal_payload(aspect, change.ours),
                    new=journal_payload(aspect, change.theirs),
                )
            except OSError:
                # Accountability is best-effort here exactly as it is on an
                # ordinary save: a read-only history dir must never fail a
                # write that already succeeded.
                pass

    return MergeOutcome(written=tuple(written), failed=tuple(failed))


def _merge_summary(change: MergeChange, source_label: str) -> str:
    base = (
        f"merged {change.aspect} → "
        f"{format_aspect_value(change.aspect, change.theirs)}"
    )
    return f"{base} (from {source_label})" if source_label else base


def merge_actor(source_label: str) -> str:
    """The journal actor a merge writes under.

    Naming the source rather than the person running the merge is what makes
    the whole operation reversible in one command:
    ``grade-restore apply --without-actor merge:<label>``.
    """
    return f"merge:{slugify_packet_name(source_label, fallback='copy')}"


@dataclass(frozen=True)
class JournalImport:
    """What `import_incoming_journal` brought across.

    ``actors`` is not a statistic — it is what the caller needs to tell the user
    how to undo the merge afterwards (see that function's warning).
    """

    entries: int = 0
    actors: tuple[str, ...] = ()
    files: int = 0


def import_incoming_journal(
    theirs_path: str | Path,
    ours_path: str | Path,
    *,
    translate: Mapping[str, str] | None = None,
    names_by_key: Mapping[str, str] | None = None,
    source_label: str = "copy",
) -> JournalImport:
    """Copy the incoming copy's audit journal into ours, de-aliased.

    The merge journals its *net* effect, which is enough to undo it but loses the
    grader's actual session: who marked what, in which order, and how long it
    took — the input `grading-stats` runs on. Importing the session files keeps
    that, with each entry's alias rewritten back to the real student so the
    imported history reads like everyone else's.

    **This costs the one-command undo, which is why it is opt-in.** Once the
    other grader's entries are in our journal, the journal says they graded here
    directly: a replay that drops only ``merge:<label>`` still finds their entry
    asserting the merged value, so the merge does not come back out. Undoing an
    imported merge means excluding the merge *and* every actor named in
    :attr:`JournalImport.actors` — which is why they are returned rather than
    counted.

    Idempotent by construction: the destination filename is derived from the
    source session's, so re-running a merge overwrites the same file instead of
    doubling every entry.
    """
    source_dir = resolve_history_dir(theirs_path)
    if not source_dir.is_dir():
        return JournalImport()
    dest_dir = resolve_history_dir(ours_path)
    label = slugify_packet_name(source_label, fallback="copy")
    names = names_by_key or {}

    imported = 0
    files = 0
    actors: set[str] = set()
    for session_file in sorted(source_dir.glob("grade-journal-*.jsonl")):
        try:
            text = session_file.read_text(encoding="utf-8")
        except OSError:
            continue
        lines: list[str] = []
        for raw_line in text.splitlines():
            stripped = raw_line.strip()
            if not stripped:
                continue
            try:
                entry = GradeJournalEntry.from_json(stripped)
            except (ValueError, TypeError):
                continue
            if entry.actor.strip():
                actors.add(entry.actor.strip())
            lines.append(_translate_entry(entry, translate, names).to_json())
        if not lines:
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        session_id = session_file.stem.removeprefix("grade-journal-")
        dest = dest_dir / f"grade-journal-{session_id}-via-{label}.jsonl"
        dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        imported += len(lines)
        files += 1
    return JournalImport(
        entries=imported, actors=tuple(sorted(actors)), files=files
    )


def _translate_entry(
    entry: GradeJournalEntry,
    translate: Mapping[str, str] | None,
    names: Mapping[str, str],
) -> GradeJournalEntry:
    """Rewrite one imported entry's student from an alias to the real person.

    An entry whose alias is not in the map is imported unchanged: showing the
    grader's work under a code they were given is honest, and dropping the
    entry would quietly shorten the audit trail.
    """
    if translate is None:
        return entry
    key = translate.get(entry.student_id.strip().casefold())
    if not key:
        return entry
    return GradeJournalEntry(
        timestamp=entry.timestamp,
        actor=entry.actor,
        kind=entry.kind,
        student_id="" if key.startswith("name:") else key,
        student_name=names.get(key, entry.student_name),
        question_id=entry.question_id,
        summary=entry.summary,
        old=entry.old,
        new=entry.new,
    )

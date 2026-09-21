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

ChangeReason = Literal["incoming_edit", "filled_blank"]
ConflictReason = Literal["both_edited", "no_baseline"]
ConflictPolicy = Literal["skip", "ours", "theirs"]

# Replay before all entries to recover the pre-copy baseline from each answer's earliest old value.
_BEFORE_ANY_ENTRY = datetime(1, 1, 1, tzinfo=timezone.utc)


class MergeError(ValueError):
    pass


@dataclass(frozen=True)
class MergeChange:
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
        return f"{self.student_key}|{self.question_id}|{self.aspect}"

    def describe(self) -> str:
        return (
            f"{self.student_label} · {self.question_id} · {self.aspect}: "
            f"ours {format_aspect_value(self.aspect, self.ours)} / "
            f"theirs {format_aspect_value(self.aspect, self.theirs)}"
        )


@dataclass(frozen=True)
class MergePlan:
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

def baseline_from_journal(
    entries: Iterable[GradeJournalEntry],
) -> dict[tuple[str, str], CellState]:
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


@dataclass(frozen=True)
class SideSnapshot:
    cells: Mapping[tuple[str, str], CellState] = field(default_factory=dict)
    paths_by_key: Mapping[str, Path] = field(default_factory=dict)
    labels: Mapping[str, str] = field(default_factory=dict)
    untranslated: tuple[str, ...] = ()
    unreadable: tuple[str, ...] = ()


def read_side(
    submissions_path: str | Path,
    *,
    translate: Mapping[str, str] | None = None,
) -> SideSnapshot:
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
    return {
        member.alias.casefold(): member.student_key
        for member in key.members
        if member.student_key
    }


def resolve_incoming_root(incoming_path: str | Path) -> Path:
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
        base = dict(manifest.baseline_cells())

    if not base:
        incoming_entries = read_journal(resolve_history_dir(theirs_path))
        for (raw_key, question_id), cell in baseline_from_journal(
            incoming_entries
        ).items():
            key = raw_key if translate is None else translate.get(raw_key, "")
            if key:
                base[(key, question_id)] = cell

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
                pass

    return MergeOutcome(written=tuple(written), failed=tuple(failed))


def _merge_summary(change: MergeChange, source_label: str) -> str:
    base = (
        f"merged {change.aspect} → "
        f"{format_aspect_value(change.aspect, change.theirs)}"
    )
    return f"{base} (from {source_label})" if source_label else base


def merge_actor(source_label: str) -> str:
    return f"merge:{slugify_packet_name(source_label, fallback='copy')}"


@dataclass(frozen=True)
class JournalImport:
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
    """Undo must exclude every returned actor as well as the merge actor: imported entries
    independently assert the merged grades.
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

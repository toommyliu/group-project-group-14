"""Poll the extraction manifest for submissions the TUI may safely fold in.

Extraction of a big cohort takes minutes; grading it takes hours. There is no
reason for those to be sequential — the first submission is gradeable the
moment it lands, so the TUI wants to open on a folder that is still filling in
and grade what is ready while the rest extracts in the background.

The hard part is knowing *when* "ready" actually means ready. A
``*.submission.md`` found by globbing the output directory may be
**half-written at the moment it is read** — the extractor writes a submission's
markdown and crop images as several separate file operations, and a poll that
lands mid-write would hand the grader a submission with missing questions, or
crash the parser outright.

``core/submissions/manifest.py`` already solves exactly this problem for
``--resume``: :func:`~opengrader.core.submissions.manifest.append_record` is
called only *after* a submission's outputs are written and flushed, so the
manifest is a strictly after-the-fact completion signal. This module reuses
that signal rather than inventing a second one — a submission becomes eligible
to fold in only once its key appears in the manifest, confirmed by checking (as
``record_is_reusable`` does) that the files it names are actually present on
disk, in case a record was ever copied somewhere its outputs did not follow.

**No filesystem-notification dependency, no thread of its own.** The TUI
already owns a thread/worker model (see ``tui/ui/screens/setup_screen.py``'s
``@work(thread=True)`` extractor worker); this is a plain object with a
``poll()`` method that a caller drives on its own timer. That also keeps it
trivially testable: a test calls ``poll()`` directly, with no sleeps or timing
games, and sees exactly what a real poll would see.

Two edge cases are load-bearing enough to spell out:

**No manifest at all.** Either the folder predates this ledger, or nothing has
ever extracted into it. Either way there is no run in flight to watch — a run
*always* writes the ledger before it writes anything else durable — so "no
manifest" reliably means "nothing is happening here," and :meth:`poll` reports
no new keys and no activity.

**Is extraction still running?** This module cannot know that for certain from
outside the process; it can only guess from how recently the manifest was
touched, via :data:`_ACTIVE_RECENCY_SECONDS`. That guess is named
``appears_active`` rather than ``is_running`` on purpose — it is an honest
label for an honest heuristic, not a claim of certainty. The one caller who
*does* have certainty — the TUI, when it launched the extraction itself in the
same process — can pass ``known_active`` to :meth:`poll` and skip the guess
entirely.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from .manifest import ExtractionRecord, read_manifest, resolve_manifest_path

# How long since the manifest file's last write before a poll assumes nobody
# is actively extracting into this folder anymore.
#
# This has to clear the **slowest realistic gap between two submissions**, not
# the typical one. A text-layer extraction writes a record every second or two,
# but `--llm-responses` sends every answer crop to a vision model, and on a
# CPU-only Ollama one exam can take a minute or more all by itself. Any window
# shorter than that makes a *running* VLM extraction read as finished in the gap
# between two students — and the whole point of this module is to stop the app
# telling a grader "you're done" while a hundred submissions are still coming.
#
# The error this constant can still make is the harmless direction: an
# abandoned folder keeps saying "extraction still running" for a few minutes
# before going quiet. The caller that actually knows (the TUI, when it launched
# the run itself) overrides this with `known_active` and never sees the lag.
_ACTIVE_RECENCY_SECONDS = 180.0


@dataclass(frozen=True)
class ExtractionPollResult:
    """What one :meth:`ExtractionWatcher.poll` call found.

    ``new_keys`` are manifest keys (the source PDF's stem — see
    :class:`~opengrader.core.submissions.manifest.ExtractionRecord`) that
    became fold-in-eligible since the *previous* poll on this watcher, in the
    order the manifest first recorded them. Each key is reported exactly once
    across the life of a watcher, never repeated on a later poll.
    """

    new_keys: tuple[str, ...]
    appears_active: bool
    # Seconds since the manifest was last written, or None when there is no
    # manifest at all. Reported separately from `appears_active` because the two
    # answer different questions: `appears_active` decides what the grader is
    # *told*, while this lets a caller decide when to stop watching entirely —
    # a much more consequential call, since a watch that stops early can never
    # restart itself and silently strands every submission still to come.
    idle_seconds: float | None = None


class ExtractionWatcher:
    """Tracks one output directory's extraction manifest across repeated polls.

    Stateful and cheap to poll on a timer: the manifest file is ``stat()``-ed
    on every call, but it is only re-read (and re-folded into records) when
    its mtime or size has actually changed since the last poll — on a session
    where extraction finished long ago, or hasn't touched the ledger between
    two UI ticks, that is a single syscall and nothing more.
    """

    def __init__(self, output_dir: str | Path) -> None:
        self._output_dir = Path(output_dir)
        self._manifest_path = resolve_manifest_path(self._output_dir)
        # Keys already reported to a caller. Not the same set as "everything
        # in the manifest" — a resumed/re-run extraction can rewrite a key
        # that was already folded in, and that update must not be re-reported
        # as a "new" submission (the TUI already has it loaded).
        self._seen_keys: set[str] = set()
        self._stat_signature: tuple[int, int] | None = None
        self._records: dict[str, ExtractionRecord] = {}

    def poll(self, *, known_active: bool | None = None) -> ExtractionPollResult:
        """Report submissions newly eligible to fold in since the last poll.

        ``known_active`` overrides the recency heuristic with certainty when
        the caller has it (see this module's docstring) — ``True`` while a
        run this same process launched is still going, ``False`` the instant
        that worker reports done, ``None`` (the default) to fall back to
        guessing from the manifest's mtime.
        """
        signature = self._stat_manifest()
        if signature is None:
            # No ledger on disk at all: nothing has ever completed here, and
            # (per this module's docstring) nothing can be in flight without
            # having written to it already — except the one case the caller
            # can rule out for us directly, which `known_active` is for.
            return ExtractionPollResult(
                new_keys=(),
                appears_active=bool(known_active),
                idle_seconds=None,
            )

        if signature != self._stat_signature:
            self._records = read_manifest(self._output_dir)
            self._stat_signature = signature

        new_keys = tuple(
            key
            for key, record in self._records.items()
            if key not in self._seen_keys
            and _outputs_present(self._output_dir, record)
        )
        self._seen_keys.update(new_keys)

        idle_seconds = self._idle_seconds()
        appears_active = (
            known_active
            if known_active is not None
            else (idle_seconds is not None and idle_seconds < _ACTIVE_RECENCY_SECONDS)
        )
        return ExtractionPollResult(
            new_keys=new_keys,
            appears_active=appears_active,
            idle_seconds=idle_seconds,
        )

    def _stat_manifest(self) -> tuple[int, int] | None:
        try:
            stat = self._manifest_path.stat()
        except OSError:
            return None
        return (stat.st_mtime_ns, stat.st_size)

    def _idle_seconds(self) -> float | None:
        """Seconds since the manifest was last written, or None if there is none.

        Clamped at zero: a manifest written by a process whose clock is a
        moment ahead of this one would otherwise come back negative and read as
        "the future", which no caller is prepared for.
        """
        try:
            mtime = self._manifest_path.stat().st_mtime
        except OSError:
            return None
        return max(0.0, time.time() - mtime)


def _outputs_present(output_dir: Path, record: ExtractionRecord) -> bool:
    """Mirrors the existence half of ``record_is_reusable`` (manifest.py).

    Not the sha/settings matching that function also does — that is about
    deciding whether a *resume* may skip re-extracting a PDF, which is
    irrelevant here. This only guards against handing the grader a record
    whose files are not actually where the ledger says, which
    `append_record`'s own write-after-flush ordering should prevent but which
    costs nothing extra to double-check before exposing a submission.
    """
    for relative in record.outputs:
        candidate = Path(relative)
        resolved = candidate if candidate.is_absolute() else output_dir / candidate
        if not resolved.exists():
            return False
    return True

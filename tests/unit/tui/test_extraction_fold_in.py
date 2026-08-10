"""Tests for the TUI's background extraction fold-in loop (`TuiApp._poll_extraction`).

The interesting behaviour here is not "does a new submission get loaded" —
`ExtractionWatcher` and `SubmissionFilesProvider.load_new_submissions` own that
and are tested where they live. It is **when the loop is allowed to stop**.
Stopping is a one-way door: nothing restarts the watch, so a loop that ends
while extraction is merely between two students strands every submission still
to come *and* lets the app go back to saying "all students finalized" while a
hundred are still extracting. These tests pin the asymmetry that fix rests on —
certainty stops the loop immediately, a merely-quiet manifest does not.

They drive `TuiApp` without mounting it: the stop policy is a plain function of
(known_active, idle_seconds) over one attribute, so a headless pilot would add
minutes of runtime and hide the thing under test behind screen composition.
"""

from __future__ import annotations

from opengrader.tui.tui import TuiApp, _EXTRACTION_GIVE_UP_SECONDS


class _FakeTimer:
    """Stands in for the Textual timer `set_interval` hands back."""

    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _app_with_a_running_watch() -> tuple[TuiApp, _FakeTimer]:
    """A `TuiApp` posed mid-watch, without mounting it.

    Only the three attributes `_stop_extraction_watch` touches need to exist;
    everything else about the app is irrelevant to the stop policy.
    """
    app = TuiApp()
    timer = _FakeTimer()
    app._extraction_watch_timer = timer  # pyright: ignore[reportAttributeAccessIssue]
    app._extraction_watcher = object()  # pyright: ignore[reportAttributeAccessIssue]
    app._extraction_known_active = None  # pyright: ignore[reportAttributeAccessIssue]
    return app, timer


def _watch_is_running(app: TuiApp) -> bool:
    return getattr(app, "_extraction_watcher", None) is not None


# --------------------------------------------------------------------------- #
# Certainty stops the loop
# --------------------------------------------------------------------------- #


def test_a_caller_that_knows_extraction_finished_stops_the_watch() -> None:
    """The setup screen's own extractor worker reporting done is authoritative:
    there is nothing left to fold in, so the loop ends at once rather than
    waiting out the silence timer."""
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(known_active=False, idle_seconds=0.0)

    assert timer.stopped is True
    assert _watch_is_running(app) is False


def test_a_caller_that_knows_extraction_is_running_never_stops_the_watch() -> None:
    """Not even after a silence long enough to trip the give-up timer — a
    worker that says it is still going outranks any inference from mtimes
    (it may be deep inside one slow submission and yet to write a record)."""
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(
        known_active=True, idle_seconds=_EXTRACTION_GIVE_UP_SECONDS * 10
    )

    assert timer.stopped is False
    assert _watch_is_running(app) is True


# --------------------------------------------------------------------------- #
# Without certainty, only a *sustained* silence counts
# --------------------------------------------------------------------------- #


def test_a_merely_quiet_manifest_does_not_stop_the_watch() -> None:
    """The regression test for the bug this policy replaced. A gap of a couple
    of minutes is an ordinary vision-model submission, not the end of the run;
    the old code stopped here and stranded the rest of the cohort."""
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(known_active=None, idle_seconds=200.0)

    assert timer.stopped is False
    assert _watch_is_running(app) is True


def test_a_silence_just_short_of_the_give_up_window_keeps_watching() -> None:
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(
        known_active=None, idle_seconds=_EXTRACTION_GIVE_UP_SECONDS - 1.0
    )

    assert _watch_is_running(app) is True
    assert timer.stopped is False


def test_a_sustained_silence_gives_up() -> None:
    """Ten minutes with nothing written at all is longer than any single
    submission takes even through a vision model, so the run is over or was
    abandoned and the timer should not keep ticking forever."""
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(
        known_active=None, idle_seconds=_EXTRACTION_GIVE_UP_SECONDS
    )

    assert timer.stopped is True
    assert _watch_is_running(app) is False


def test_no_manifest_at_all_stops_the_watch() -> None:
    """`idle_seconds is None` means no ledger exists — and a run always writes
    the ledger before anything else durable, so there is no extraction here to
    wait for (the caller with certainty passes `known_active` instead)."""
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(known_active=None, idle_seconds=None)

    assert timer.stopped is True
    assert _watch_is_running(app) is False


# --------------------------------------------------------------------------- #
# The two windows must not be confused with each other
# --------------------------------------------------------------------------- #


def test_the_give_up_window_is_far_longer_than_the_status_line_window() -> None:
    """These constants answer different questions and the gap between them is
    the whole fix: mislabelling a finished run costs one stale sentence, while
    stopping the loop early loses submissions. If they are ever collapsed back
    into one number, the original bug returns."""
    from opengrader.core.submissions.watch import _ACTIVE_RECENCY_SECONDS

    assert _EXTRACTION_GIVE_UP_SECONDS > _ACTIVE_RECENCY_SECONDS * 2

from __future__ import annotations

from opengrader.tui.tui import TuiApp, _EXTRACTION_GIVE_UP_SECONDS


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _app_with_a_running_watch() -> tuple[TuiApp, _FakeTimer]:
    app = TuiApp()
    timer = _FakeTimer()
    app._extraction_watch_timer = timer  # pyright: ignore[reportAttributeAccessIssue]
    app._extraction_watcher = object()  # pyright: ignore[reportAttributeAccessIssue]
    app._extraction_known_active = None  # pyright: ignore[reportAttributeAccessIssue]
    return app, timer


def _watch_is_running(app: TuiApp) -> bool:
    return getattr(app, "_extraction_watcher", None) is not None


def test_a_caller_that_knows_extraction_finished_stops_the_watch() -> None:
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(known_active=False, idle_seconds=0.0)

    assert timer.stopped is True
    assert _watch_is_running(app) is False


def test_a_caller_that_knows_extraction_is_running_never_stops_the_watch() -> None:
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(
        known_active=True, idle_seconds=_EXTRACTION_GIVE_UP_SECONDS * 10
    )

    assert timer.stopped is False
    assert _watch_is_running(app) is True


def test_a_merely_quiet_manifest_does_not_stop_the_watch() -> None:
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
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(
        known_active=None, idle_seconds=_EXTRACTION_GIVE_UP_SECONDS
    )

    assert timer.stopped is True
    assert _watch_is_running(app) is False


def test_no_manifest_at_all_stops_the_watch() -> None:
    app, timer = _app_with_a_running_watch()

    app._stop_extraction_watch_if_finished(known_active=None, idle_seconds=None)

    assert timer.stopped is True
    assert _watch_is_running(app) is False


def test_the_give_up_window_is_far_longer_than_the_status_line_window() -> None:
    from opengrader.core.submissions.watch import _ACTIVE_RECENCY_SECONDS

    assert _EXTRACTION_GIVE_UP_SECONDS > _ACTIVE_RECENCY_SECONDS * 2

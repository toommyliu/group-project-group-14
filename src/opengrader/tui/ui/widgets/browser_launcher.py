"""Open a class folder in the browser app, and the status line that follows it.

Opt-in (`core/browser_app`): the Start grading card only shows *Open in
browser* once the user ticks it in Tools. Pressing it imports the folder into
a workspace — the same import `opengrader tui FOLDER` does, grades already in
the files kept — starts that workspace's local service (detached, so it
outlives this window) and opens the assignment in the default browser. The
first time, when the web app is not built yet, it offers to build it — and,
on a Mac that has Homebrew, to install Node.js first if the build
has nothing to run with.

A folder opened this way is a *folder session* (`core/workspace/folder_session`):
every grade saved in the browser is written straight into the student's
`.submission.md`, so the files stay the truth. That holds for a folder with
no workspace yet, or one already in a folder session. A folder that is
already a normal (shared) workspace opens as that workspace instead; its
browser grades stay in it until written back.

This widget is the status line under the card's buttons: hidden until a
service is running (or starting), then its address with *Open* and *Stop*.
It also remembers the last workspace, so a service left running by an
earlier session can still be stopped from here, and it notices when the
service goes away on its own (`opengrader stop`, a reboot).
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from opengrader.core import browser_app, homebrew
from opengrader.tui.ui import button_roles
from opengrader.core.recents import short_path

if TYPE_CHECKING:
    from opengrader.tui.make_workspace import MakeWorkspaceRequest

POLL_SECONDS = 15.0


class BrowserLauncher(Vertical):
    DEFAULT_CSS = """
    BrowserLauncher {
        width: 100%;
        height: auto;
        margin-top: 1;
        padding: 0 1;
        border-left: tall $success;
    }

    BrowserLauncher.busy {
        border-left: tall $warning;
    }

    BrowserLauncher.failed {
        border-left: tall $error;
    }

    BrowserLauncher #browser-status-row {
        width: 100%;
        height: auto;
    }

    BrowserLauncher #browser-status-text {
        width: 1fr;
        height: auto;
        color: $text-muted;
    }

    BrowserLauncher #browser-status-actions {
        width: auto;
        height: auto;
    }

    BrowserLauncher #browser-status-actions Button {
        margin-left: 1;
    }
    """

    def __init__(self, **kwargs) -> None:  # noqa: ANN003
        super().__init__(**kwargs)
        self.add_class("hidden")
        self._root: Path | None = None
        self._url: str | None = None
        self._folder_session = False
        self._install_process: subprocess.Popen[str] | None = None
        self.busy = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="browser-status-row"):
            yield Static("", id="browser-status-text", markup=False)
            with Horizontal(id="browser-status-actions"):
                yield Button(
                    "Open",
                    id="browser-reopen",
                    classes=button_roles.BROWSER,
                    compact=True,
                )
                yield Button("Stop server", id="browser-stop", variant="error", compact=True)

    def on_mount(self) -> None:
        saved = browser_app.load_browser_preferences()
        if saved.last_workspace:
            self._root = Path(saved.last_workspace)
            self._check_worker(self._root)
        self.set_interval(POLL_SECONDS, self._poll)

    @property
    def url(self) -> str | None:
        return self._url

    # -- opening ---------------------------------------------------------

    def open_folder(self, qa_path: str | Path, submissions_path: str | Path) -> None:
        """What *Open in browser* does with the card's answer key + submissions."""
        self._when_web_app_ready(
            lambda build, brew: self._open_worker(
                str(qa_path), str(submissions_path), build, brew
            )
        )

    def open_workspace(
        self,
        root: Path,
        request: MakeWorkspaceRequest | None = None,
        *,
        on_progress: Callable[[str], None] | None = None,
        on_done: Callable[[Path, str | None], None] | None = None,
    ) -> None:
        """Open a workspace's assignment list in the browser — made first
        from `request` when given (the Workspace card in Helper Tools).

        `on_progress` gets each status line and `on_done(root, error)` the
        outcome (`error` None when it worked), both on the app's thread, so a card far from this status
        line can show them too. Nothing is made while the web app still
        needs building and that question is open: a Cancel there makes
        nothing (`on_done` is not called).
        """
        self._when_web_app_ready(
            lambda build, brew: self._workspace_worker(
                root, request, build, brew, on_progress, on_done
            )
        )

    def _when_web_app_ready(self, start: Callable[[bool, str | None], None]) -> None:
        """Call `start(build, install_with)` once the web app can be served.

        `web_dist_state()` decides what happens: missing asks to build first
        (offering to install Node.js when Homebrew can, or else just
        saying why it can't); stale — sources have changed since the
        last build — offers a rebuild but doesn't force one, since a stale
        bundle still mostly works; ready starts straight away.
        """
        if self.busy:
            self.app.notify("The browser app is still starting — one moment.")
            return
        state = browser_app.web_dist_state()
        if state == "ready":
            start(False, None)
            return
        if state == "missing" and not browser_app.web_buildable():
            self.app.notify(
                "This copy of Opengrader was installed without the browser app. "
                "Reinstall from a copy where `npm --prefix src/opengrader/web "
                "run build` has been run.",
                severity="error",
                timeout=12,
            )
            return
        can_build = browser_app.web_buildable() and browser_app.npm_command() is not None
        if state == "stale" and not can_build:
            # Nothing to rebuild from (or nothing to rebuild with) — the old
            # bundle is still better than nothing.
            self.app.notify(
                "The browser app may be out of date on this computer "
                "(Node.js and npm are needed to rebuild it).",
                severity="warning",
                timeout=12,
            )
            start(False, None)
            return

        from opengrader.tui.ui.screens.browser_build_screen import BrowserBuildScreen

        # Nothing to build with: Homebrew, when it is here, can fix that.
        brew = None if can_build else homebrew.find_homebrew()

        def _answer(choice: str | None) -> None:
            if choice == "build":
                start(True, None)
            elif choice == "install" and brew:
                start(True, brew)
            elif choice == "open":
                start(False, None)

        self.app.push_screen(
            BrowserBuildScreen(
                can_build=can_build, stale=state == "stale", can_install=bool(brew)
            ),
            _answer,
        )

    def _ready_web_app(self, build: bool, install_with: str | None) -> bool:
        """Install Node.js and build the web app, as asked; False on failure
        (already shown). Runs on a worker thread."""
        app = self.app
        if install_with:

            def on_install_line(line: str) -> None:
                if self.is_attached:
                    app.call_from_thread(
                        self._set_busy,
                        f"Installing Node.js (one time) — {line}",
                    )

            app.call_from_thread(
                self._set_busy,
                "Installing Node.js with Homebrew (one time, a few minutes)…",
            )
            try:
                browser_app.install_tools(
                    install_with,
                    on_line=on_install_line,
                    on_start=self._remember_install,
                )
            except browser_app.WebBuildError as exc:
                # Quit mid-install: the card is gone and nobody is left to tell.
                if self.is_attached:
                    detail = f"\n{exc.tail}" if exc.tail else ""
                    app.call_from_thread(self._fail, f"{exc}{detail}", keep=True)
                return False
            finally:
                self._install_process = None
        if build:

            def on_line(line: str) -> None:
                app.call_from_thread(
                    self._set_busy, f"Building the browser app (one time) — {line}"
                )

            try:
                browser_app.build_web(on_line=on_line)
            except browser_app.WebBuildError as exc:
                detail = f"\n{exc.tail}" if exc.tail else ""
                app.call_from_thread(self._fail, f"{exc}{detail}", keep=True)
                return False
            app.call_from_thread(self._set_busy, "Starting the browser app…")
        return True

    @work(thread=True, exclusive=True, group="browser-launch")
    def _open_worker(
        self,
        qa_path: str,
        submissions_path: str,
        build: bool,
        install_with: str | None = None,
    ) -> None:
        """`install_with` is Homebrew's path when Node.js goes in first."""
        app = self.app
        app.call_from_thread(self._set_busy, "Starting the browser app…")
        if not self._ready_web_app(build, install_with):
            return
        try:
            from opengrader.server.workspace.client import ensure_service

            root, assignment_id, folder_session = import_folder(
                qa_path, submissions_path
            )
            connection = ensure_service(root)
        except Exception as exc:  # noqa: BLE001 - shown to the user, app stays up
            app.call_from_thread(self._fail, f"Could not open the browser app: {exc}")
            return
        root = Path(root)
        if folder_session:
            # Grades changed in classic since last time reach the browser.
            problem = sync_folder_session(connection)
            if problem:
                app.call_from_thread(
                    app.notify, problem, severity="warning", timeout=15
                )
        else:
            app.call_from_thread(
                app.notify,
                "This folder is a shared workspace, so grades entered in the "
                "browser stay in the workspace until they are written back "
                "(opengrader tui, open it, then W).",
                severity="warning",
                timeout=15,
            )
        from opengrader.core.recents import record_class_folder

        # The Start grading card lists it, as if it had been graded here.
        record_class_folder(qa_path, submissions_path)
        self._remember(root, assignment_id)
        page = f"{connection.url}/assignments/{assignment_id}"
        opened = self._open_url(page)
        app.call_from_thread(
            self._show_running, root, connection.url, page, opened, folder_session
        )

    @work(thread=True, exclusive=True, group="browser-launch")
    def _workspace_worker(
        self,
        root: Path,
        request: MakeWorkspaceRequest | None,
        build: bool,
        install_with: str | None,
        on_progress: Callable[[str], None] | None,
        on_done: Callable[[Path, str | None], None] | None,
    ) -> None:
        app = self.app

        def say(message: str) -> None:
            app.call_from_thread(self._set_busy, message)
            if on_progress is not None:
                app.call_from_thread(on_progress, message)

        def done(error: str | None) -> None:
            if on_done is not None:
                app.call_from_thread(on_done, root, error)

        say("Starting the browser app…")
        if not self._ready_web_app(build, install_with):
            done("The browser app could not be prepared; the Start grading card says why.")
            return
        try:
            from opengrader.server.workspace.client import ensure_service

            if request is not None:
                from opengrader.tui.make_workspace import make_workspace

                root = make_workspace(request, echo=lambda line: say(line.strip()))
            say("Opening the workspace in your browser…")
            connection = ensure_service(root)
        except Exception as exc:  # noqa: BLE001 - shown to the user, app stays up
            verb = "make" if request is not None else "open"
            message = f"Could not {verb} the workspace: {exc}"
            app.call_from_thread(self._fail, message, keep=True)
            done(message)
            return
        self._remember(root, None)
        page = f"{connection.url}/assignments"
        opened = self._open_url(page)
        app.call_from_thread(self._show_running, root, connection.url, page, opened, False)
        done(None)

    def _remember_install(self, process: subprocess.Popen[str]) -> None:
        self._install_process = process

    def on_unmount(self) -> None:
        # A worker thread cannot be cancelled; ending the install is what
        # lets it, and the app, finish.
        process = self._install_process
        if process is not None and process.poll() is None:
            process.terminate()

    @staticmethod
    def _remember(root: Path, assignment_id: str | None) -> None:
        from opengrader.core.recents import record_recent

        # `opengrader tui` lists it, so writing grades back (W) is one step away.
        record_recent("workspace", root, title=root.name, assignment=assignment_id)
        try:
            browser_app.save_browser_preferences(
                replace(
                    browser_app.load_browser_preferences(), last_workspace=str(root)
                )
            )
        except OSError:
            pass

    @staticmethod
    def _open_url(url: str) -> bool:
        # Over SSH the browser that would open is the server's, not the user's.
        if os.environ.get("SSH_CONNECTION"):
            return False
        import webbrowser

        try:
            return bool(webbrowser.open(url))
        except webbrowser.Error:
            return False

    # -- status line -----------------------------------------------------

    def _set_busy(self, message: str) -> None:
        self.busy = True
        self.add_class("busy")
        self.remove_class("failed")
        self.remove_class("hidden")
        self.query_one("#browser-status-text", Static).update(message)
        self.query_one("#browser-status-actions").display = False

    def _fail(self, message: str, *, keep: bool = False) -> None:
        """`keep` leaves the message in the status line until the next try.

        For a failed install or build: Homebrew's and npm's errors are too
        long to read off a notification before it goes. A running service
        keeps its own status line (and its Stop button) instead.
        """
        self.busy = False
        self.remove_class("busy")
        self.app.notify(message, severity="error", timeout=15)
        if self._url:
            self._show(self._url)
        elif keep:
            self.add_class("failed")
            self.query_one("#browser-status-text", Static).update(f"✗ {message}")
            self.query_one("#browser-status-actions").display = False
            self.remove_class("hidden")
        else:
            self.add_class("hidden")

    def _show_running(
        self, root: Path, url: str, page: str, opened: bool, folder_session: bool
    ) -> None:
        self.busy = False
        self.remove_class("busy")
        self._root = root
        self._folder_session = folder_session
        self._show(url)
        if opened:
            self.app.notify("Opened in your browser.")
        else:
            self.app.notify(
                f"Open {page} in a browser on this computer "
                "(over SSH, forward that port first).",
                timeout=15,
            )

    def _show(self, url: str) -> None:
        self._url = url
        where = short_path(self._root) if self._root else ""
        if self._folder_session:
            kind = "Folder session"
            grades = "Grades saved there go straight into the .submission.md files."
        else:
            kind = "Workspace"
            grades = "Grades saved there stay in the workspace until written back."
        self.query_one("#browser-status-text", Static).update(
            f"● {kind} running at {url} — {where}. {grades} It keeps running "
            "after you close Opengrader; Stop server disconnects all workspaces."
        )
        self.query_one("#browser-status-actions").display = True
        self.remove_class("failed")
        self.remove_class("hidden")

    def _hide(self) -> None:
        self._url = None
        self.remove_class("failed")
        self.add_class("hidden")

    # -- open again, stop, notice it stopped -----------------------------

    @on(Button.Pressed, "#browser-reopen")
    def _reopen(self, event: Button.Pressed) -> None:
        event.stop()
        if self._url and not self._open_url(self._url):
            self.app.notify(f"Open {self._url} in a browser on this computer.")

    @on(Button.Pressed, "#browser-stop")
    def _stop(self, event: Button.Pressed) -> None:
        event.stop()
        if self._root is None or self.busy:
            return
        self._set_busy("Stopping the server for all workspaces…")
        self._stop_worker(self._root)

    @work(thread=True, exclusive=True, group="browser-launch")
    def _stop_worker(self, root: Path) -> None:
        import time

        from textual.worker import get_current_worker

        from opengrader.server.workspace.client import (
            WorkspaceClient,
            running_service,
            workspace_free,
        )

        app = self.app
        worker = get_current_worker()
        try:
            connection = running_service(root)
            if connection:
                WorkspaceClient(connection).request("POST", "/api/shutdown")
                waiting_at = time.monotonic() + 1
                reported_wait = False
                while not workspace_free(root):
                    if worker.is_cancelled:
                        return
                    if not reported_wait and time.monotonic() >= waiting_at:
                        app.call_from_thread(
                            self._set_busy, "Waiting for running jobs to finish…"
                        )
                        reported_wait = True
                    time.sleep(0.2)
        except Exception as exc:  # noqa: BLE001 - shown to the user
            if not worker.is_cancelled:
                app.call_from_thread(self._fail, f"Could not stop the browser app: {exc}")
            return
        if not worker.is_cancelled:
            app.call_from_thread(self._stopped)

    def _stopped(self) -> None:
        self.busy = False
        self.remove_class("busy")
        self._hide()
        self.app.notify("Server stopped for all workspaces. Your grades are saved.")

    def _poll(self) -> None:
        if self._root is not None and not self.busy:
            self._check_worker(self._root)

    @work(thread=True, exclusive=True, group="browser-poll")
    def _check_worker(self, root: Path) -> None:
        from opengrader.core.workspace.folder_session import load_folder_session
        from opengrader.server.workspace.client import running_service

        connection = running_service(root)
        folder_session = load_folder_session(root) is not None
        self.app.call_from_thread(
            self._checked, root, connection.url if connection else None, folder_session
        )

    def _checked(self, root: Path, url: str | None, folder_session: bool) -> None:
        if self.busy or root != self._root:
            return
        if url:
            self._folder_session = folder_session
            self._show(url)
        elif self._url:
            self._hide()


def folder_root(qa_path: str | Path, submissions_path: str | Path) -> Path:
    """Where `main._import_as_workspace` puts the workspace for this pair."""
    qa = Path(qa_path).expanduser().resolve()
    submissions = Path(submissions_path).expanduser().resolve()
    return Path(os.path.commonpath([qa.parent, submissions]))


def import_folder(
    qa_path: str | Path, submissions_path: str | Path
) -> tuple[Path, str, bool]:
    """Import the folder as `opengrader tui FOLDER` does; say if it is a folder session.

    It is one when the folder had no workspace before this import, or was
    already a folder session. A normal workspace (database, no marker) is
    shared by other graders or tools and keeps its behaviour.
    """
    from opengrader.core.workspace.folder_session import (
        load_folder_session,
        start_folder_session,
    )
    from opengrader.core.workspace.store import DATABASE, Workspace
    from opengrader.tui import main as launcher

    expected = folder_root(qa_path, submissions_path)
    folder_session = (
        not (expected / DATABASE).is_file()
        or load_folder_session(expected) is not None
    )
    try:
        root, assignment_id = launcher._import_as_workspace(qa_path, submissions_path)
    except Exception:
        # The import made the database but failed later (a bad file, say):
        # mark it now, or the retry would find a database and no marker and
        # treat the folder as a shared workspace.
        if folder_session and (expected / DATABASE).is_file():
            try:
                start_folder_session(
                    expected, Workspace(expected).assignment_id_for_folder(expected)
                )
            except (OSError, ValueError):
                pass
        raise
    root = Path(root)
    if folder_session:
        start_folder_session(root, assignment_id)
    return root, assignment_id, folder_session


def sync_folder_session(connection) -> str | None:  # noqa: ANN001 - ServiceConnection
    """Pull file edits into a folder session; a problem to show, or None."""
    from opengrader.core.workspace.models import FolderSyncResult
    from opengrader.server.workspace.client import ServiceError, WorkspaceClient

    try:
        result = FolderSyncResult.model_validate_json(
            WorkspaceClient(connection).request(
                "POST", "/api/folder-session/sync", timeout=120
            )
        )
    except (ServiceError, ValueError) as exc:
        return f"Could not bring the browser up to date with the folder: {exc}"
    if result.failed:
        first = result.problems[0] if result.problems else ""
        return (
            f"{result.failed} grade(s) could not be synced with the folder. {first}"
        ).strip()
    return None

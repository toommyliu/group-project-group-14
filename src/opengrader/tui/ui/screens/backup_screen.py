"""Backup and integrity checks for the open cohort, from inside the TUI.

Everything Opengrader keeps is a file in one folder and there is no server
holding a second copy, so the two operations that matter are *record what is
here* and *make a copy you have actually read back*. Both live here
(`core/integrity.py`), on the setup screen's Helper Tools, because they are
about the folder rather than about any answer in it.

Restoring an archive is deliberately **not** here: it unpacks into a new
directory, and a session pointed at the old one would carry on writing to the
old one. That is a terminal job (``grade-backup restore``), and the hint says so
rather than leaving the reader wondering where the button is.
"""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, RichLog, Static

from opengrader.core.crypto import is_encrypted, passphrases_match
from opengrader.core.integrity import (
    IntegrityError,
    IntegrityReport,
    build_snapshot,
    create_backup,
    default_backup_path,
    iter_concerning,
    read_snapshot,
    summarize_report,
    verify_backup,
    verify_folder,
    write_snapshot,
)
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen


class BackupScreen(ModalScreen[None]):
    """Record checksums, verify the folder, and write a verified archive."""

    DEFAULT_CSS = """
    BackupScreen {
        align: center middle;
    }

    #backup-dialog {
        width: 82%;
        max-width: 118;
        height: 82%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #backup-hint, #backup-folder {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    BackupScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    BackupScreen .path-row {
        height: auto;
    }

    BackupScreen .path-row Input {
        width: 1fr;
    }

    BackupScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }

    /* The form scrolls, the result pane and the buttons do not. Without this
       the dialog's content can grow past its own height once the passphrase
       fields are revealed, and the buttons are then drawn *outside* the dialog
       — where they look fine and cannot be clicked. */
    #backup-form {
        width: 100%;
        height: 1fr;
    }

    #backup-log {
        height: 10;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    BackupScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    /* Explicit auto height: a bare Vertical defaults to 1fr and would stretch
       to fill the dialog, pushing the log and the buttons off-screen. */
    #backup-archive-area, #backup-passphrase-area {
        width: 100%;
        height: auto;
    }

    #backup-passphrase-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    /* Declared on the screen, not only on the app (`tui.py`), so a disclosure
       area really collapses wherever this modal is hosted — including in tests,
       where a bare host app supplies no CSS and the "hidden" rows would take up
       space and push the buttons off-screen. */
    .hidden {
        display: none;
    }

    #backup-archive-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    #backup-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #backup-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, submissions_path: str = "", archive_path: str = "") -> None:
        super().__init__()
        self._submissions_path = submissions_path
        self._initial_archive = archive_path

    def _default_archive(self) -> Path:
        """Where the archive lands when the location box is left alone.

        An encrypted archive gets a ``.enc`` name, so the file says out loud
        that it needs a passphrase — the person who finds it in two years is
        usually not the person who wrote it.
        """
        return default_backup_path(
            self._submissions_path or ".", encrypted=self._encrypting()
        )

    def _encrypting(self) -> bool:
        return self.is_mounted and self.query_one("#backup-encrypt", Checkbox).value

    def _resolved_passphrase(self, *, confirm: bool) -> str | None:
        """The passphrase to use: "" for none, None when it is unusable.

        Validated on the UI thread so a mismatch comes back as a notification
        immediately, rather than as a failure after a long archive has been
        written. ``confirm`` is only asked when *creating* something, since a
        typo there is what cannot be recovered from.
        """
        if not self._encrypting():
            return ""
        first = self.query_one("#backup-passphrase", Input).value
        if not first:
            self.app.notify("Enter a passphrase, or untick the box.", severity="error")
            return None
        if confirm:
            second = self.query_one("#backup-passphrase-confirm", Input).value
            if not passphrases_match(first, second):
                self.app.notify("The two passphrases do not match.", severity="error")
                return None
        return first

    def _archive_area_classes(self) -> str:
        return "" if self._initial_archive else "hidden"

    def _archive_hint(self) -> str:
        """One muted line naming the file that will actually be written.

        It also says where the *checksums* go, because that is the question a
        reader of this dialog has and the answer is different: a snapshot is a
        fingerprint of the folder and only means anything beside it, so it is
        not relocatable at all.
        """
        target = str(self._resolved_archive())
        return (
            f"[dim]The archive will be written to {target}\n"
            "Checksums are recorded inside the folder itself "
            "(.opengrader/integrity.json) — they describe that folder and only "
            "work beside it.[/dim]"
        )

    def _resolved_archive(self) -> Path:
        """The archive path to actually use.

        With the box unchecked the field is ignored **entirely**, so a path left
        behind in a hidden input can never silently apply to a later run.
        """
        if self.is_mounted and self.query_one("#backup-custom-archive", Checkbox).value:
            typed = self.query_one("#backup-archive", Input).value.strip()
            if typed:
                return Path(typed).expanduser()
        return self._default_archive()

    @staticmethod
    def _field_label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="field-label")

    def compose(self) -> ComposeResult:
        with Vertical(id="backup-dialog") as dialog:
            dialog.border_title = "▸ Backup & integrity"
            with VerticalScroll(id="backup-form"):
                yield Static(
                    "Record a checksum of every file now, and later ask what moved. "
                    "Or write one archive of the lot — it is read back and verified "
                    "before this says it worked. To unpack an archive, use "
                    "'grade-backup restore' in a terminal: it creates a new folder, "
                    "which this session would not be pointing at.",
                    id="backup-hint",
                )
                yield Static(
                    f"Folder: {self._submissions_path or '(none open)'}",
                    id="backup-folder",
                )
                # Same disclosure pattern as the extractor's advanced options: a
                # sensible default, and the field only appears for someone who
                # wants a different answer.
                yield Checkbox(
                    "Save the archive somewhere else",
                    value=bool(self._initial_archive),
                    id="backup-custom-archive",
                )
                with Vertical(
                    id="backup-archive-area", classes=self._archive_area_classes()
                ):
                    yield self._field_label("Archive", "the .zip to write or check")
                    with Horizontal(classes="path-row"):
                        yield Input(
                            value=self._initial_archive,
                            placeholder=str(self._default_archive()),
                            id="backup-archive",
                        )
                        yield Button("Browse", id="backup-browse", compact=True)
                # A backup holds every name, id and scan there is, and it is the one
                # artifact that routinely leaves this machine on a stick or a drive.
                yield Checkbox(
                    "Protect the archive with a passphrase",
                    value=False,
                    id="backup-encrypt",
                )
                with Vertical(id="backup-passphrase-area", classes="hidden"):
                    yield self._field_label(
                        "Passphrase", "also used to read an encrypted archive back"
                    )
                    yield Input(password=True, id="backup-passphrase")
                    yield self._field_label("Confirm passphrase", "when writing a new one")
                    yield Input(password=True, id="backup-passphrase-confirm")
                    yield Static(
                        "[dim]Nobody can recover this for you. Lose it and the "
                        "archive is unreadable — including by you.[/dim]",
                        id="backup-passphrase-hint",
                    )
                yield Static(self._archive_hint(), id="backup-archive-hint")
            log = RichLog(id="backup-log", wrap=True, auto_scroll=True)
            log.border_title = "Result"
            yield log
            with Horizontal(id="backup-buttons"):
                yield Button("Record checksums", id="backup-snapshot", compact=True)
                yield Button("Verify folder", variant="primary", id="backup-verify", compact=True)
                yield Button("Back up", id="backup-run", compact=True)
                yield Button("Check archive", id="backup-check", compact=True)
                yield Button("Close", id="backup-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#backup-archive", Input).focus()
        snapshot = read_snapshot(self._submissions_path) if self._submissions_path else None
        log = self.query_one("#backup-log", RichLog)
        if snapshot is None:
            # Said up front rather than as an error later: "Verify" has nothing
            # to compare against until checksums exist, and the fix is one click
            # away in this same dialog.
            log.write(
                "No checksums recorded for this folder yet.\n"
                "Press 'Record checksums' to take the first one — then 'Verify "
                "folder' can say what changed since."
            )
        else:
            log.write(
                f"Checksums recorded {snapshot.created_at}\n"
                f"{len(snapshot.files)} file(s), {snapshot.total_bytes:,} bytes."
            )

    @on(Checkbox.Changed, "#backup-custom-archive")
    def _on_custom_archive_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#backup-archive-area").set_class(not event.value, "hidden")
        self._refresh_archive_hint()

    @on(Checkbox.Changed, "#backup-encrypt")
    def _on_encrypt_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#backup-passphrase-area").set_class(not event.value, "hidden")
        # The default archive name gains or loses its .enc, so the line naming
        # the file that will be written has to be redrawn with it.
        self._refresh_archive_hint()
        if event.value:
            self.query_one("#backup-passphrase", Input).focus()

    @on(Input.Changed, "#backup-archive")
    def _on_archive_typed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_archive_hint()

    def _refresh_archive_hint(self) -> None:
        self.query_one("#backup-archive-hint", Static).update(self._archive_hint())

    @on(Button.Pressed, "#backup-browse")
    def _on_browse(self, event: Button.Pressed) -> None:
        event.stop()
        target = self.query_one("#backup-archive", Input)
        current = target.value.strip().strip("'\"")
        start_path: Path | None = None
        if current:
            candidate = Path(current).expanduser()
            start_path = candidate if candidate.is_dir() else candidate.parent

        def _apply(path: Path | None) -> None:
            if path is None:
                return
            # A directory choice is completed into a filename, since the field
            # wants an archive and the picker cannot confirm a file that does
            # not exist yet.
            chosen = path / "opengrader-backup.zip" if path.is_dir() else path
            target.value = str(chosen)
            target.cursor_position = len(target.value)

        self.app.push_screen(
            PathPickerScreen(
                title="Choose the archive (or the folder to write it in)",
                mode="any",
                start_path=start_path,
            ),
            _apply,
        )

    def _require_folder(self) -> bool:
        if self._submissions_path:
            return True
        self.app.notify("No submissions folder is open.", severity="error")
        return False

    def _busy(self, message: str) -> None:
        for button in self.query(Button):
            button.disabled = True
        log = self.query_one("#backup-log", RichLog)
        log.clear()
        log.write(message)

    def _done(self, text: str, *, severity: str = "information") -> None:
        if not self.is_attached:
            return
        for button in self.query(Button):
            button.disabled = False
        log = self.query_one("#backup-log", RichLog)
        log.clear()
        log.write(text)
        if severity != "information":
            self.app.notify("See the result pane.", severity=severity)  # type: ignore[arg-type]

    @on(Button.Pressed, "#backup-snapshot")
    def _on_snapshot(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._require_folder():
            return
        self._busy("Recording checksums…")
        self.run_worker(self._snapshot, thread=True, exclusive=True)

    def _snapshot(self) -> None:
        try:
            snapshot = build_snapshot(self._submissions_path)
            path = write_snapshot(self._submissions_path, snapshot)
        except (IntegrityError, OSError) as exc:
            self.app.call_from_thread(self._done, f"Failed: {exc}", severity="error")
            return
        self.app.call_from_thread(
            self._done,
            f"Recorded {len(snapshot.files)} file(s), "
            f"{snapshot.total_bytes:,} bytes.\n{path}",
        )

    @on(Button.Pressed, "#backup-verify")
    def _on_verify(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._require_folder():
            return
        self._busy("Checking every file…")
        self.run_worker(self._verify, thread=True, exclusive=True)

    def _verify(self) -> None:
        try:
            report = verify_folder(self._submissions_path)
        except (IntegrityError, OSError) as exc:
            self.app.call_from_thread(self._done, f"{exc}", severity="error")
            return
        self.app.call_from_thread(
            self._done,
            _format_report(report),
            severity="warning" if report.concerning else "information",
        )

    @on(Button.Pressed, "#backup-run")
    def _on_backup(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._require_folder():
            return
        passphrase = self._resolved_passphrase(confirm=True)
        if passphrase is None:
            return
        archive = self._resolved_archive()
        self._busy(f"Writing and verifying {archive}…")
        self.run_worker(
            lambda: self._backup(archive, passphrase), thread=True, exclusive=True
        )

    def _backup(self, archive: Path, passphrase: str = "") -> None:
        try:
            result = create_backup(
                self._submissions_path, archive, passphrase=passphrase
            )
        except (IntegrityError, OSError) as exc:
            self.app.call_from_thread(self._done, f"Failed: {exc}", severity="error")
            return
        if result.verified:
            protection = (
                "\n\nEncrypted. Nobody — including you — can read this without "
                "that passphrase, so store it somewhere you will still have it "
                "in a year."
                if result.encrypted
                else ""
            )
            text = (
                f"Backed up {result.file_count} file(s), "
                f"{result.total_bytes:,} bytes.\n{result.archive_path}\n\n"
                "Read back and verified against its own manifest." + protection
            )
            self.app.call_from_thread(self._done, text)
            return
        text = (
            f"WARNING: {len(result.mismatches)} file(s) in the archive do not "
            "match the manifest. Do not rely on this backup:\n"
            + "\n".join(f"  {path}" for path in result.mismatches)
        )
        self.app.call_from_thread(self._done, text, severity="error")

    @on(Button.Pressed, "#backup-check")
    def _on_check(self, event: Button.Pressed) -> None:
        event.stop()
        # Checking re-reads an archive that already exists, so — unlike writing
        # one — an unchecked box has nothing sensible to fall back to: today's
        # default name is a file that was never created.
        archive = self._resolved_archive()
        if not archive.exists():
            self.app.notify(
                f"No archive at {archive} — tick 'Save the archive somewhere "
                "else' and point at the one you want to check.",
                severity="error",
            )
            return
        # Whether a passphrase is needed is read off the file, not off the
        # checkbox: a year later nobody remembers which archives were encrypted.
        passphrase = ""
        if is_encrypted(archive):
            typed = self._resolved_passphrase(confirm=False)
            if typed is None:
                return
            if not typed:
                self.app.notify(
                    "That archive is encrypted — tick 'Protect the archive with "
                    "a passphrase' and enter it.",
                    severity="error",
                )
                return
            passphrase = typed
        self._busy(f"Reading {archive}…")
        self.run_worker(
            lambda: self._check(archive, passphrase), thread=True, exclusive=True
        )

    def _check(self, archive: Path, passphrase: str = "") -> None:
        try:
            mismatches = verify_backup(archive, passphrase=passphrase)
        except (IntegrityError, OSError) as exc:
            self.app.call_from_thread(self._done, f"{exc}", severity="error")
            return
        if not mismatches:
            self.app.call_from_thread(self._done, f"{archive}\n\nIntact.")
            return
        self.app.call_from_thread(
            self._done,
            f"{len(mismatches)} file(s) do not match the manifest:\n"
            + "\n".join(f"  {path}" for path in mismatches),
            severity="error",
        )

    @on(Button.Pressed, "#backup-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


def _format_report(report: IntegrityReport) -> str:
    """Render a verify report: the headline, then only what needs attention."""
    lines = [
        f"Checksums recorded {report.snapshot_at}",
        summarize_report(report),
        "",
    ]
    concerning = list(iter_concerning(report))
    if not concerning:
        lines.append("Nothing needs attention.")
        return "\n".join(lines)

    lines.append(f"{len(concerning)} file(s) need attention:")
    lines += [f"  {finding.describe()}" for finding in concerning]
    if report.unexplained:
        # The conclusion, not another list: these changed with nothing in the
        # audit trail saying why, which is what the journal cross-reference is
        # for in the first place.
        lines += [
            "",
            f"{len(report.unexplained)} of those changed with no grading "
            "recorded for that student since — check these first:",
        ]
        lines += [f"  {finding.path}" for finding in report.unexplained]
    return "\n".join(lines)

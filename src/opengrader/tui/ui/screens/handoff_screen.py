"""Hand a cohort to another grader, and merge their copy back — in the TUI.

Two modals for the two halves of the round trip (`core/handoff/`), reached from
the setup screen's Helper Tools:

* :class:`GraderPacketScreen` cuts an anonymous copy for a TA. It is a form and
  a result, because creating a packet has exactly one decision in it (redact or
  not) and one thing the user must not miss afterwards (where the key went).
* :class:`MergeScreen` brings one back. It is deliberately *not* a form and a
  log: a merge's whole difficulty is the handful of answers both copies changed,
  and reading those out of a text dump and re-running a CLI with a blanket
  ``--conflicts theirs`` is the worst possible way to decide them. So the plan
  is rendered, every conflict is a focusable row, and ``o``/``t`` set that one
  answer's outcome before a single Apply writes the lot.

Both call the core engine directly rather than shelling through the CLI. The
merge needs the *structured* plan to build the conflict list, and a merge spans
two folders — one of which this session was never opened with — which is the
same reason the course gradebook does not go through the data provider either.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from textual import events, on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, RichLog, Static

from opengrader.core.handoff.merge import (
    ConflictPolicy,
    MergeConflict,
    MergeError,
    MergePlan,
    SideSnapshot,
    alias_translation,
    apply_merge_plan,
    build_merge_plan,
    import_incoming_journal,
    locate_handoff_key,
    merge_actor,
    resolve_conflicts,
    resolve_incoming_root,
)
from opengrader.core.crypto import passphrases_match
from opengrader.core.handoff.packet import (
    HandoffKey,
    PacketError,
    PacketManifest,
    build_packet,
    default_packet_output_dir,
    is_sealed_packet,
    seal_packet,
    unseal_packet,
)
from opengrader.core.history import GradeJournal, resolve_history_dir
from opengrader.tui.ui.screens.path_picker_screen import PathPickerScreen

def _field_label(text: str, detail: str = "") -> Static:
    markup = f"[b]{text}[/b]"
    if detail:
        markup = f"{markup}  [dim]{detail}[/dim]"
    return Static(markup, classes="field-label")


def _browse_into(screen: ModalScreen, target: Input, *, title: str, mode: str) -> None:
    """Open the shared path picker and write the choice into ``target``."""
    start_path: Path | None = None
    current = target.value.strip().strip("'\"")
    if current:
        candidate = Path(current).expanduser()
        if candidate.is_dir():
            start_path = candidate
        elif candidate.parent.is_dir():
            start_path = candidate.parent

    def _apply(path: Path | None) -> None:
        if path is None:
            return
        target.value = str(path)
        target.cursor_position = len(target.value)

    screen.app.push_screen(
        PathPickerScreen(title=title, mode=mode, start_path=start_path), _apply
    )


class GraderPacketScreen(ModalScreen[None]):
    """Create an anonymous copy of this cohort for another grader."""

    DEFAULT_CSS = """
    GraderPacketScreen {
        align: center middle;
    }

    #packet-dialog {
        width: 80%;
        max-width: 110;
        height: 80%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #packet-hint, #packet-encrypt-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    GraderPacketScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    GraderPacketScreen .path-row {
        height: auto;
    }

    GraderPacketScreen .path-row Input {
        width: 1fr;
    }

    GraderPacketScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }

    GraderPacketScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    /* Explicit auto height: a bare Vertical defaults to 1fr and would stretch
       to fill the dialog, pushing the log and the buttons off-screen — the same
       trap #ext-parallel-options documents. */
    #packet-output-area, #packet-passphrase-area {
        width: 100%;
        height: auto;
    }

    #packet-passphrase, #packet-passphrase-confirm {
        width: 1fr;
    }

    /* Declared on the screen, not only on the app (`tui.py`), so a disclosure
       area really does collapse wherever this modal is hosted — including in
       tests, where a bare host app supplies no CSS of its own and the "hidden"
       rows would otherwise take up space and push the buttons off-screen.
       Same reason `llm_eval_screen.py` carries its own copy. */
    .hidden {
        display: none;
    }

    #packet-destination-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }

    /* The form scrolls, the result pane and the buttons do not. Without this
       the dialog's content can grow past its own height once a disclosure area
       is revealed, and the buttons are then drawn *outside* the dialog — where
       they look fine and cannot be clicked. */
    #packet-form {
        width: 100%;
        height: 1fr;
    }

    #packet-log {
        height: 10;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #packet-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #packet-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(
        self,
        *,
        submissions_path: str = "",
        qa_file_path: str = "",
        output_path: str = "",
    ) -> None:
        super().__init__()
        self._submissions_path = submissions_path
        self._qa_file_path = qa_file_path
        self._initial_output = output_path

    def _default_output(self) -> Path:
        """Where the copy lands when the destination box is left alone."""
        return default_packet_output_dir(self._submissions_path or ".")

    def _output_area_classes(self) -> str:
        return "" if self._initial_output else "hidden"

    def _destination_hint(self) -> str:
        """One muted line naming the folder that will actually be written.

        The default is only a good default if the user can see where it points;
        an unexplained "it went somewhere" is worse than asking.
        """
        if self.is_mounted and self.query_one("#packet-custom-output", Checkbox).value:
            typed = self.query_one("#packet-output", Input).value.strip()
            if typed:
                return f"[dim]The copy will be created in {typed}[/dim]"
        return f"[dim]The copy will be created in {self._default_output()}[/dim]"

    def _resolved_output(self) -> Path:
        """The destination to actually use.

        With the box unchecked the field is ignored **entirely**, so a path left
        behind in a hidden input can never silently apply to a later run — the
        same rule `SetupScreen._ollama_values` and `_jobs_argv` follow.
        """
        if self.query_one("#packet-custom-output", Checkbox).value:
            typed = self.query_one("#packet-output", Input).value.strip()
            if typed:
                return Path(typed).expanduser()
        return self._default_output()

    def compose(self) -> ComposeResult:
        with Vertical(id="packet-dialog") as dialog:
            dialog.border_title = "▸ Create a grader copy"
            with VerticalScroll(id="packet-form"):
                yield Static(
                    "Makes a copy of this class another grader can take away and "
                    "grade on their own machine, then hand back for merging. Names "
                    "and student ids are replaced by codes, and the scanned exams "
                    "stay here.",
                    id="packet-hint",
                )
                yield _field_label("Grader", "who this copy is for; names the folder")
                yield Input(placeholder="e.g. alice", id="packet-grader")
                yield Checkbox(
                    "Hide student names and ids (recommended)",
                    value=True,
                    id="packet-redact",
                )
                # Redaction and encryption answer different questions and are worth
                # keeping visibly separate: hiding names stops the *grader* knowing
                # whose work they mark, a passphrase stops *everyone else* reading
                # the copy at all — which is what matters once it is on a USB stick
                # or in an inbox.
                yield Checkbox(
                    "Protect the copy with a passphrase",
                    value=False,
                    id="packet-encrypt",
                )
                yield Static(
                    "Seals the packet into a single passphrase-protected file, "
                    "for a copy that travels by email or on a USB stick. Hiding "
                    "names stops the grader knowing whose work they mark; this "
                    "stops anyone else reading the copy at all.",
                    id="packet-encrypt-hint",
                )
                with Vertical(id="packet-passphrase-area", classes="hidden"):
                    # Both boxes on one row rather than stacked with a label each:
                    # a revealed disclosure area that costs ten rows pushes the
                    # buttons off a short terminal, which is the trap the dialog's
                    # CSS comments already warn about.
                    yield _field_label(
                        "Passphrase",
                        "typed twice — nobody can recover it for you; "
                        "tell the grader separately",
                    )
                    with Horizontal(classes="path-row"):
                        yield Input(
                            password=True,
                            placeholder="Passphrase",
                            id="packet-passphrase",
                        )
                        yield Input(
                            password=True,
                            placeholder="Confirm",
                            id="packet-passphrase-confirm",
                        )
                # Same disclosure pattern as the extractor's parallel/Ollama
                # options: a sensible default, and the field only appears for
                # someone who wants a different answer.
                yield Checkbox(
                    "Save the copy somewhere else",
                    value=bool(self._initial_output),
                    id="packet-custom-output",
                )
                with Vertical(id="packet-output-area", classes=self._output_area_classes()):
                    yield _field_label(
                        "Destination", "where the copy folder is created"
                    )
                    with Horizontal(classes="path-row"):
                        yield Input(
                            value=self._initial_output,
                            placeholder=str(self._default_output()),
                            id="packet-output",
                        )
                        yield Button("Browse", id="packet-browse", compact=True)
                yield Static(self._destination_hint(), id="packet-destination-hint")
            log = RichLog(id="packet-log", wrap=True, auto_scroll=True)
            log.border_title = "Result"
            yield log
            with Horizontal(id="packet-buttons"):
                yield Button(
                    "Create copy", variant="primary", id="packet-run", compact=True
                )
                yield Button("Close", id="packet-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#packet-grader", Input).focus()

    @on(Checkbox.Changed, "#packet-custom-output")
    def _on_custom_output_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#packet-output-area").set_class(not event.value, "hidden")
        self._refresh_destination_hint()

    @on(Checkbox.Changed, "#packet-encrypt")
    def _on_encrypt_toggled(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#packet-passphrase-area").set_class(not event.value, "hidden")
        if event.value:
            self.query_one("#packet-passphrase", Input).focus()

    def _resolved_passphrase(self) -> str | None:
        """The passphrase to seal with: "" for none, None when it is unusable.

        Validated here rather than in the worker thread, so a typo comes back as
        a notification the moment the button is pressed instead of as a failure
        after the packet has already been built.
        """
        if not self.query_one("#packet-encrypt", Checkbox).value:
            return ""
        first = self.query_one("#packet-passphrase", Input).value
        second = self.query_one("#packet-passphrase-confirm", Input).value
        if not first:
            self.app.notify("Enter a passphrase, or untick the box.", severity="error")
            return None
        if not passphrases_match(first, second):
            self.app.notify("The two passphrases do not match.", severity="error")
            return None
        return first

    @on(Input.Changed, "#packet-output")
    def _on_output_typed(self, event: Input.Changed) -> None:
        event.stop()
        self._refresh_destination_hint()

    def _refresh_destination_hint(self) -> None:
        self.query_one("#packet-destination-hint", Static).update(
            self._destination_hint()
        )

    @on(Button.Pressed, "#packet-browse")
    def _on_browse(self, event: Button.Pressed) -> None:
        event.stop()
        _browse_into(
            self,
            self.query_one("#packet-output", Input),
            title="Choose where to create the grader copy",
            mode="directory",
        )

    @on(Button.Pressed, "#packet-run")
    def _on_run(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._submissions_path:
            self.app.notify("No submissions folder to share.", severity="error")
            return
        output = self._resolved_output()
        grader = self.query_one("#packet-grader", Input).value.strip()
        redact = self.query_one("#packet-redact", Checkbox).value
        passphrase = self._resolved_passphrase()
        if passphrase is None:
            return

        self.query_one("#packet-run", Button).disabled = True
        log = self.query_one("#packet-log", RichLog)
        log.clear()
        log.write("Creating the copy…")
        self.run_worker(
            lambda: self._create(output, grader, redact, passphrase),
            thread=True,
            exclusive=True,
        )

    def _create(
        self, output: Path, grader: str, redact: bool, passphrase: str = ""
    ) -> None:
        """Worker thread: build the packet and report it back to the UI."""
        try:
            result = build_packet(
                self._submissions_path,
                output,
                grader=grader,
                qa_file_path=self._qa_file_path or None,
                redact=redact,
            )
            # Sealed as a second step, exactly like the CLI: the plaintext
            # folder is removed so the thing that gets sent cannot be the
            # unprotected one.
            sealed = seal_packet(result.packet_path, passphrase) if passphrase else None
        except (PacketError, OSError, ValueError) as exc:
            self.app.call_from_thread(self._show_error, str(exc))
            return

        destination = "Send this file:  " if sealed else "Send this folder:  "
        lines = [
            f"Created packet '{result.manifest.packet_id}'.",
            "",
            f"{destination}{sealed or result.packet_path}",
            f"  {result.student_count} student(s), {result.copied_crops} answer image(s)",
        ]
        if sealed is not None:
            lines.append(
                "  protected with a passphrase — send it to the grader some "
                "other way than the message carrying this file"
            )
        if result.qa_file_path is not None:
            lines.append(f"  answer key included: {result.qa_file_path.name}")
        if result.skipped_crops:
            lines.append(
                f"  note: {len(result.skipped_crops)} answer image(s) could not "
                "travel; those questions carry their transcription only."
            )
        lines += ["", f"Keep this file:  {result.key_path}"]
        if result.manifest.redacted:
            lines.append(
                "It is the only thing that turns the codes back into your "
                "students, and merging their work back needs it. Do not send it "
                "with the packet."
            )
        else:
            lines.append("This copy is NOT anonymous: it carries real names and ids.")
        self.app.call_from_thread(self._show_result, "\n".join(lines))

    def _show_result(self, text: str) -> None:
        if not self.is_attached:
            return
        self.query_one("#packet-run", Button).disabled = False
        log = self.query_one("#packet-log", RichLog)
        log.clear()
        log.write(text)
        self.app.notify("Grader copy created.")

    def _show_error(self, message: str) -> None:
        if not self.is_attached:
            return
        self.query_one("#packet-run", Button).disabled = False
        log = self.query_one("#packet-log", RichLog)
        log.clear()
        log.write(f"Could not create the copy:\n\n{message}")
        self.app.notify("Grader copy failed — see the result pane.", severity="error")

    @on(Button.Pressed, "#packet-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class _ConflictRow(Static):
    """One conflicting answer, with the grader's decision attached to it.

    Focusable, and it owns its keys: ``o`` keeps ours, ``t`` takes theirs, and
    ``j``/``k`` (or the arrows) move — the same row grammar the consistency,
    similarity and claim modals use, so the muscle memory transfers.
    """

    can_focus = True

    def __init__(self, conflict: MergeConflict) -> None:
        self.conflict = conflict
        self.decision: ConflictPolicy = "ours"
        super().__init__("", classes="merge-conflict-row")

    def on_mount(self) -> None:
        self._refresh_label()

    def set_decision(self, decision: ConflictPolicy) -> None:
        self.decision = decision
        self._refresh_label()

    def _refresh_label(self) -> None:
        marker = "→ theirs" if self.decision == "theirs" else "  keep ours"
        note = " · no baseline" if self.conflict.reason == "no_baseline" else ""
        self.update(f"  [{marker:<9}] {self.conflict.describe()}{note}")
        self.set_class(self.decision == "theirs", "takes-theirs")

    def on_key(self, event: events.Key) -> None:
        # The focused row is first in the bubble path, so handling movement here
        # stops the enclosing VerticalScroll from turning j/k into scrolling.
        if event.key == "o":
            event.stop()
            self.set_decision("ours")
        elif event.key == "t":
            event.stop()
            self.set_decision("theirs")
        elif event.key in ("down", "j"):
            event.stop()
            self.screen.focus_relative_conflict(self, 1)
        elif event.key in ("up", "k"):
            event.stop()
            self.screen.focus_relative_conflict(self, -1)


class MergeScreen(ModalScreen[bool]):
    """Preview a returned grader copy, settle its conflicts, and merge it.

    Dismisses with **True when it wrote something**, so the app knows to re-read
    the cohort: a merge rewrites files the session already has open, and a stale
    cache would show old grades *and* trip the mtime guard on the next save.
    """

    DEFAULT_CSS = """
    MergeScreen {
        align: center middle;
    }

    #merge-dialog {
        width: 88%;
        max-width: 130;
        height: 86%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    /* Auto height + a screen-local .hidden, for the same reasons spelled out
       in GraderPacketScreen's CSS above. */
    #merge-passphrase-area {
        width: 100%;
        height: auto;
    }

    .hidden {
        display: none;
    }

    #merge-hint, #merge-summary, #merge-import-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #merge-summary {
        margin-top: 1;
    }

    MergeScreen .field-label {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    MergeScreen .path-row {
        height: auto;
    }

    MergeScreen .path-row Input {
        width: 1fr;
    }

    MergeScreen .path-row Button {
        min-width: 10;
        width: auto;
        margin-left: 1;
        margin-top: 1;
    }

    MergeScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    #merge-body {
        height: 1fr;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        padding: 0 1;
        scrollbar-size: 1 1;
    }

    MergeScreen .merge-section {
        width: 100%;
        height: auto;
        color: $text-muted;
        text-style: bold;
        margin-top: 1;
    }

    MergeScreen .merge-change-row {
        width: 100%;
        height: auto;
    }

    MergeScreen .merge-conflict-row {
        width: 100%;
        height: auto;
        color: $warning;
    }

    MergeScreen .merge-conflict-row.takes-theirs {
        color: $accent;
    }

    MergeScreen .merge-conflict-row:hover {
        background: $boost;
    }

    MergeScreen .merge-conflict-row:focus {
        background: $accent 20%;
        text-style: bold;
    }

    MergeScreen .merge-note {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #merge-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #merge-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(
        self,
        *,
        submissions_path: str = "",
        incoming_path: str = "",
        teacher_mode: bool = True,
    ) -> None:
        super().__init__()
        self._submissions_path = submissions_path
        self._initial_incoming = incoming_path
        # Anonymity-aware rather than teacher-gated, like the consistency and
        # similarity audits: the plan names students, so outside teacher mode it
        # names them by display id. Hiding the screen instead would withdraw it
        # from exactly the people most likely to be merging.
        self._teacher_mode = teacher_mode
        self._merged = False
        # Set by a successful preview; Apply refuses to run without them, so a
        # merge can never be written from a plan the grader never saw.
        self._plan: MergePlan | None = None
        self._ours: SideSnapshot | None = None
        self._key: HandoffKey | None = None
        self._manifest: PacketManifest | None = None
        self._incoming_root: Path | None = None
        # Where a sealed packet gets unpacked. Held on the screen rather than in
        # a `with` block because it has to survive from Preview until Apply;
        # `on_unmount` deletes it.
        self._workspace: tempfile.TemporaryDirectory | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="merge-dialog") as dialog:
            dialog.border_title = "▸ Merge a grader copy back"
            yield Static(
                "Takes the answers only they graded, keeps the ones only you "
                "graded, and asks about the rest. Nothing is written until you "
                "press Merge.",
                id="merge-hint",
            )
            yield _field_label(
                "Their copy", "the folder the other grader handed back"
            )
            with Horizontal(classes="path-row"):
                yield Input(
                    value=self._initial_incoming,
                    placeholder="Path to the returned folder or sealed file…",
                    id="merge-incoming",
                )
                yield Button("Browse", id="merge-browse", compact=True)
            # Revealed only when the chosen path turns out to be a sealed
            # packet. Detected from the file rather than asked as a question,
            # because whoever is merging did not necessarily create the packet
            # and has no reason to know how it was packed.
            with Vertical(id="merge-passphrase-area", classes="hidden"):
                yield _field_label(
                    "Passphrase", "this copy was sealed when it was handed out"
                )
                yield Input(password=True, id="merge-passphrase")
            yield Checkbox(
                "Also import their grading history",
                value=False,
                id="merge-import-history",
            )
            yield Static(
                "Brings the other grader's audit-trail entries across into yours. "
                "It costs the one-command undo: once their entries are here "
                "asserting the merged values, reversing this merge alone no "
                "longer takes those values back out.",
                id="merge-import-hint",
            )
            yield Static("", id="merge-summary")
            body = VerticalScroll(id="merge-body")
            body.border_title = "Plan"
            yield body
            with Horizontal(id="merge-buttons"):
                yield Button("Keep ours for all", id="merge-all-ours", compact=True)
                yield Button("Take theirs for all", id="merge-all-theirs", compact=True)
                yield Button("Preview", variant="primary", id="merge-preview", compact=True)
                yield Button("Merge", id="merge-apply", compact=True, disabled=True)
                yield Button("Close", id="merge-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#merge-incoming", Input).focus()
        self._sync_passphrase_row()

    def on_unmount(self) -> None:
        """Delete the unsealed copy when the dialog closes.

        A sealed packet is unpacked into a temporary directory that has to live
        from Preview until Apply, so it cannot be cleaned up by a `with` block
        around either one. Closing the dialog is the moment it is certainly
        finished with — and leaving a decrypted cohort on disk would undo the
        protection the packet was sealed with.
        """
        self._discard_workspace()

    def _discard_workspace(self) -> None:
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None

    # ------------------------------------------------------------------ #
    # Preview
    # ------------------------------------------------------------------ #

    @on(Button.Pressed, "#merge-browse")
    def _on_browse(self, event: Button.Pressed) -> None:
        event.stop()
        _browse_into(
            self,
            self.query_one("#merge-incoming", Input),
            title="Choose the returned grader copy",
            # "any": a returned copy is a folder, but a sealed one is a file.
            mode="any",
        )

    @on(Input.Changed, "#merge-incoming")
    def _on_incoming_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._sync_passphrase_row()

    def _sync_passphrase_row(self) -> None:
        """Show the passphrase box exactly when the chosen path needs one."""
        raw = self.query_one("#merge-incoming", Input).value.strip()
        sealed = bool(raw) and is_sealed_packet(Path(raw).expanduser())
        self.query_one("#merge-passphrase-area").set_class(not sealed, "hidden")

    @on(Button.Pressed, "#merge-preview")
    def _on_preview(self, event: Button.Pressed) -> None:
        event.stop()
        incoming = self.query_one("#merge-incoming", Input).value.strip()
        if not self._submissions_path or not incoming:
            self.app.notify("Point at the returned folder first.", severity="error")
            return
        passphrase = self.query_one("#merge-passphrase", Input).value
        if is_sealed_packet(Path(incoming).expanduser()) and not passphrase:
            self.app.notify(
                "This copy is sealed — enter the passphrase it was sealed with.",
                severity="error",
            )
            return
        self.query_one("#merge-preview", Button).disabled = True
        self.query_one("#merge-summary", Static).update("Comparing the two copies…")
        self.run_worker(
            lambda: self._preview(incoming, passphrase), thread=True, exclusive=True
        )

    def _preview(self, incoming: str, passphrase: str = "") -> None:
        """Worker thread: read both folders and plan the merge."""
        try:
            incoming_path = self._readable_incoming(Path(incoming).expanduser(), passphrase)
            key, manifest = locate_handoff_key(self._submissions_path, incoming_path)
            root = resolve_incoming_root(incoming_path)
            plan, ours, _theirs = build_merge_plan(
                self._submissions_path,
                root,
                handoff_key=key,
                manifest=manifest,
                anonymous=not self._teacher_mode,
            )
        except (MergeError, PacketError, OSError, ValueError) as exc:
            self.app.call_from_thread(self._show_preview_error, str(exc))
            return
        self.app.call_from_thread(self._show_plan, plan, ours, key, manifest, root)

    def _readable_incoming(self, path: Path, passphrase: str) -> Path:
        """Return a packet folder, unsealing into this dialog's temp dir first.

        Re-unsealed on every Preview rather than cached: the grader may have
        corrected the path or the passphrase, and reusing a previous unpacking
        would compare against the wrong copy.
        """
        if not is_sealed_packet(path):
            return path
        self._discard_workspace()
        self._workspace = tempfile.TemporaryDirectory(prefix="opengrader-merge-")
        return unseal_packet(path, Path(self._workspace.name) / "packet", passphrase)

    def _show_preview_error(self, message: str) -> None:
        if not self.is_attached:
            return
        self.query_one("#merge-preview", Button).disabled = False
        self.query_one("#merge-summary", Static).update(f"[b]Could not compare:[/b] {message}")
        self.app.notify("Merge preview failed.", severity="error")

    def _show_plan(
        self,
        plan: MergePlan,
        ours: SideSnapshot,
        key: HandoffKey | None,
        manifest: PacketManifest | None,
        incoming_root: Path,
    ) -> None:
        if not self.is_attached:
            return
        self._plan = plan
        self._ours = ours
        self._key = key
        self._manifest = manifest
        self._incoming_root = incoming_root

        self.query_one("#merge-preview", Button).disabled = False
        self.query_one("#merge-apply", Button).disabled = not plan
        self.query_one("#merge-summary", Static).update(self._summary_text(plan))

        body = self.query_one("#merge-body", VerticalScroll)
        body.remove_children()
        if not plan:
            body.mount(Label("The two copies already agree — nothing to merge.", classes="merge-note"))
            return

        if plan.changes:
            body.mount(Static(f"TAKING FROM THEIR COPY ({len(plan.changes)})", classes="merge-section"))
            for change in plan.changes:
                body.mount(Static(f"  {change.describe()}", classes="merge-change-row"))
        if plan.conflicts:
            body.mount(
                Static(
                    f"BOTH COPIES CHANGED THESE ({len(plan.conflicts)}) — "
                    "o = keep ours, t = take theirs",
                    classes="merge-section",
                )
            )
            for conflict in plan.conflicts:
                body.mount(_ConflictRow(conflict))
        for note in self._caveats(plan):
            body.mount(Label(note, classes="merge-note"))

        rows = list(self.query(_ConflictRow))
        if rows:
            # Focus the first thing needing a decision, so the modal is
            # immediately operable without reaching for the mouse.
            rows[0].focus()

    def _summary_text(self, plan: MergePlan) -> str:
        parts = [
            f"[b]{len(plan.changes)}[/b] to take",
            f"[b]{len(plan.conflicts)}[/b] to decide",
            f"{plan.kept_ours} kept from yours",
            f"{plan.already_agree} already agree",
        ]
        return " · ".join(parts)

    def _caveats(self, plan: MergePlan) -> list[str]:
        """What the merge could not account for — never left implicit.

        Shown inside the plan rather than as a notification, because "nothing to
        merge" must not be readable as "everything is accounted for" when a
        dozen submissions failed to match.
        """
        notes: list[str] = []
        if plan.unmatched_students:
            notes.append(
                f"{len(plan.unmatched_students)} submission(s) in their copy "
                "could not be matched to a student here."
            )
        if plan.unknown_questions:
            notes.append(
                f"{len(plan.unknown_questions)} answer(s) have no matching "
                "question in your copy and were skipped."
            )
        return notes

    # ------------------------------------------------------------------ #
    # Conflict decisions
    # ------------------------------------------------------------------ #

    def focus_relative_conflict(self, current: _ConflictRow, delta: int) -> None:
        rows = list(self.query(_ConflictRow))
        if current not in rows:
            return
        target = rows.index(current) + delta
        if 0 <= target < len(rows):
            rows[target].focus()
            rows[target].scroll_visible()

    @on(Button.Pressed, "#merge-all-ours")
    def _on_all_ours(self, event: Button.Pressed) -> None:
        event.stop()
        self._set_all("ours")

    @on(Button.Pressed, "#merge-all-theirs")
    def _on_all_theirs(self, event: Button.Pressed) -> None:
        event.stop()
        self._set_all("theirs")

    def _set_all(self, decision: ConflictPolicy) -> None:
        rows = list(self.query(_ConflictRow))
        for row in rows:
            row.set_decision(decision)
        if rows:
            self.app.notify(f"All {len(rows)} conflict(s) set to {decision}.")

    def _decisions(self) -> dict[str, ConflictPolicy]:
        return {
            row.conflict.conflict_id: row.decision for row in self.query(_ConflictRow)
        }

    # ------------------------------------------------------------------ #
    # Apply
    # ------------------------------------------------------------------ #

    @on(Button.Pressed, "#merge-apply")
    def _on_apply(self, event: Button.Pressed) -> None:
        event.stop()
        plan, ours = self._plan, self._ours
        if plan is None or ours is None:
            self.app.notify("Preview the merge first.", severity="error")
            return
        decisions = self._decisions()
        changes = list(plan.changes) + list(
            resolve_conflicts(plan.conflicts, policy="ours", decisions=decisions)
        )
        if not changes:
            self.app.notify("Nothing selected to merge.")
            return
        import_history = self.query_one("#merge-import-history", Checkbox).value
        self.query_one("#merge-apply", Button).disabled = True
        self.run_worker(
            lambda: self._apply(changes, ours, import_history),
            thread=True,
            exclusive=True,
        )

    def _apply(self, changes, ours: SideSnapshot, import_history: bool) -> None:  # noqa: ANN001
        """Worker thread: write the merged answers and report what happened."""
        label = (self._manifest.packet_id if self._manifest else "") or (
            self._incoming_root.name if self._incoming_root else "copy"
        )
        actor = merge_actor(label)
        journal = GradeJournal(
            resolve_history_dir(self._submissions_path), actor=actor
        )
        outcome = apply_merge_plan(
            changes, ours.paths_by_key, journal=journal, source_label=label
        )

        lines = [
            f"Merged {len(outcome.written)} change(s) into "
            f"{outcome.files_touched} student file(s).",
        ]
        undo_actors = [actor]
        if import_history and self._incoming_root is not None:
            imported = import_incoming_journal(
                self._incoming_root.parent
                if self._incoming_root.name == "submissions"
                else self._incoming_root,
                self._submissions_path,
                translate=alias_translation(self._key) if self._key else None,
                names_by_key={
                    member.student_key: member.student_name
                    for member in self._key.members
                }
                if self._key
                else None,
                source_label=label,
            )
            if imported.entries:
                lines.append(f"Imported {imported.entries} of their journal entries.")
                undo_actors.extend(imported.actors)
        if outcome.failed:
            lines.append(f"{len(outcome.failed)} change(s) could not be written:")
            lines += [f"  {change.describe()} — {error}" for change, error in outcome.failed]
        lines.append("")
        lines.append(
            "Undo with:  grade-restore apply --submissions <folder> "
            + " ".join(f"--without-actor {name}" for name in undo_actors)
            + " --yes"
        )
        self.app.call_from_thread(self._show_applied, "\n".join(lines), not outcome.failed)

    def _show_applied(self, text: str, succeeded: bool) -> None:
        if not self.is_attached:
            return
        body = self.query_one("#merge-body", VerticalScroll)
        body.remove_children()
        body.mount(Label(text, classes="merge-note"))
        self.query_one("#merge-summary", Static).update("Merged.")
        # The plan is now stale — the files it was diffed against have changed —
        # so Apply stays disabled until a fresh preview is run.
        self._plan = None
        self._merged = True
        if succeeded:
            self.app.notify("Grader copy merged.")
        else:
            self.app.notify("Merge finished with errors — see the plan pane.", severity="error")

    @on(Button.Pressed, "#merge-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(self._merged)

    def action_close(self) -> None:
        self.dismiss(self._merged)

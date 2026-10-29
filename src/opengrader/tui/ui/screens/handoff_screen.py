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
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons

def _field_label(text: str, detail: str = "") -> Static:
    markup = f"[b]{text}[/b]"
    if detail:
        markup = f"{markup}  [dim]{detail}[/dim]"
    return Static(markup, classes="field-label")


def _browse_into(screen: ModalScreen, target: Input, *, title: str, mode: str) -> None:
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


class GraderPacketScreen(OutlineButtons, ModalScreen[None]):
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
    }

    GraderPacketScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }
    #packet-output-area, #packet-passphrase-area {
        width: 100%;
        height: auto;
    }

    #packet-passphrase, #packet-passphrase-confirm {
        width: 1fr;
    }
    .hidden {
        display: none;
    }

    #packet-destination-hint {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
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
        return default_packet_output_dir(self._submissions_path or ".")

    def _output_area_classes(self) -> str:
        return "" if self._initial_output else "hidden"

    def _destination_hint(self) -> str:
        if self.is_mounted and self.query_one("#packet-custom-output", Checkbox).value:
            typed = self.query_one("#packet-output", Input).value.strip()
            if typed:
                return f"[dim]The copy will be created in {typed}[/dim]"
        return f"[dim]The copy will be created in {self._default_output()}[/dim]"

    def _resolved_output(self) -> Path:
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
                    "and student ids are replaced by codes, and the scanned assignments "
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
                        yield Button("Browse", id="packet-browse", classes=button_roles.OPEN,  compact=True)
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
        """Empty string means no passphrase; None means invalid input."""
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
        try:
            result = build_packet(
                self._submissions_path,
                output,
                grader=grader,
                qa_file_path=self._qa_file_path or None,
                redact=redact,
            )
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


# Public name for screens that merge something other than a grader copy (the
# workspace write-back); they subclass it to relabel ours/theirs.
ConflictRow = _ConflictRow


class MergeScreen(OutlineButtons, ModalScreen[bool]):
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
        self._teacher_mode = teacher_mode
        self._merged = False
        self._plan: MergePlan | None = None
        self._ours: SideSnapshot | None = None
        self._key: HandoffKey | None = None
        self._manifest: PacketManifest | None = None
        self._incoming_root: Path | None = None
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
                yield Button("Browse", id="merge-browse", classes=button_roles.OPEN,  compact=True)
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
        self._discard_workspace()

    def _discard_workspace(self) -> None:
        if self._workspace is not None:
            self._workspace.cleanup()
            self._workspace = None


    @on(Button.Pressed, "#merge-browse")
    def _on_browse(self, event: Button.Pressed) -> None:
        event.stop()
        _browse_into(
            self,
            self.query_one("#merge-incoming", Input),
            title="Choose the returned grader copy",
            mode="any",
        )

    @on(Input.Changed, "#merge-incoming")
    def _on_incoming_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._sync_passphrase_row()

    def _sync_passphrase_row(self) -> None:
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
            "Undo with:  opengrader grade-restore apply --submissions <folder> "
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

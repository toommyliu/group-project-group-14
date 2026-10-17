"""The AI assistant tool on the classic Tools page: `opengrader agent` with buttons.

The Tools card asks for one class folder and offers only the jobs that fit
it (`core.agent.class_state`). Each job opens one dialog here:

* `AnswerKeyAssistantScreen` — the assistant writes `qa.md`, or fills the
  blank answers of a drafted one, in a folder Opengrader makes beside the
  class folder (`prepare_key_folder`), with no student work in it; Use this
  key copies a key that loads back into the class folder.
* `AssistantScreen` — grading (`purpose="grading"`) or drafting partial
  credit (`purpose="rubrics"`), in a redacted copy (`prepare_agent_packet`):
  where to open the assistant and what to tell it, or how to connect it over
  MCP, where it has no file access at all; the fixes it suggests
  (`SuggestionsScreen`); Merge back through the ordinary merge dialog
  (`MergeScreen`, a three-way merge that never overwrites the teacher's
  grade); and Delete copy.

There is no "make a folder" step to remember: the first press of the job's
main button makes it, and a folder or copy made earlier is picked up again.
"""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from pathlib import Path

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, RichLog, Select, Static

from opengrader.core.agent import (
    AgentError,
    accept_suggestion,
    check_key_file,
    client_instructions,
    default_agent_output_dir,
    delete_packet,
    dismiss_suggestion,
    list_agent_packets,
    list_suggestions,
    load_tasks,
    packet_status,
    prepare_agent_packet,
    use_key,
)
from opengrader.core.agent.answer_key import KEY_FILENAME, default_key_folder, prepare_key_folder
from opengrader.core.agent.common import (
    AGENT_MARKER_FILENAME,
    KIND_ANSWER_KEY,
    PURPOSE_GRADING,
    PURPOSE_RUBRICS,
    packet_purpose,
    read_marker,
)
from opengrader.core.handoff.packet import PacketError
from opengrader.tui import clipboard
from opengrader.tui.external_viewer import ExternalViewerError, open_folder
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons
from opengrader.tui.ui.screens.handoff_screen import MergeScreen, _browse_into

# What the teacher says to the assistant once it is open in the folder. The
# folder's AGENTS.md carries the rules; this only points at it.
STARTER_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: grade the answers "
    "that are still open, then give me a short summary."
)
KEY_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: do the task it gives "
    "you on qa.md, check it, then tell me what you were unsure of."
)
RUBRIC_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: draft partial-credit "
    "rubrics from the answers, then give me a short summary."
)

_CSS = """
{name} {{
    align: center middle;
}}

{name} > Vertical {{
    width: 88%;
    max-width: 120;
    height: 88%;
    background: $surface;
    border: round $accent;
    border-title-align: left;
    border-title-color: $accent;
    border-title-style: bold;
    padding: 1 2;
}}

{name} .assistant-top {{
    height: auto;
    max-height: 40%;
}}

{name} .assistant-hint {{
    width: 100%;
    height: auto;
    color: $text-muted;
    margin-bottom: 1;
}}

{name} Select, {name} .path-row {{
    width: 100%;
    height: auto;
    margin-bottom: 1;
}}

{name} .path-row Input {{
    width: 1fr;
}}

{name} .path-row Button {{
    width: auto;
    margin-left: 1;
}}

{name} RichLog {{
    height: 1fr;
    border: round $surface-lighten-2;
    border-title-align: left;
    border-title-color: $text-muted;
    background: transparent;
    padding: 0 1;
}}

{name} .assistant-buttons {{
    height: auto;
    align-horizontal: right;
    margin-top: 1;
}}

{name} .assistant-buttons Button {{
    margin-left: 1;
}}
"""


def open_instructions(folder: Path, prompt: str, *, mcp: bool = False) -> list[str]:
    """Where to start each kind of assistant so that it works in `folder`."""
    quoted = shlex.quote(str(folder))
    lines = [
        "Open your AI assistant IN this folder, never in your class folder:",
        f"  {folder}",
        "",
        f"  Claude Code:  cd {quoted} && claude",
        f"  Codex:        cd {quoted} && codex",
        f"  Gemini CLI:   cd {quoted} && gemini",
        "  Cursor / VS Code: open that folder (File → Open Folder).",
    ]
    if mcp:
        lines += [
            "  Claude Desktop, ChatGPT and other chat apps: press Copy MCP setup",
            "                and paste it into the app. This is the safest way:",
            "                the assistant then has no file access at all.",
        ]
    return lines + ["", "Then say:", f"  {prompt}"]


def _show_folder(screen: ModalScreen, folder: Path) -> None:
    try:
        open_folder(folder)
    except ExternalViewerError as exc:
        screen.app.notify(str(exc), severity="error")


def _log(screen: ModalScreen, lines: Sequence[str]) -> None:
    log = screen.query_one(RichLog)
    log.clear()
    for line in lines:
        log.write(line)


class AssistantScreen(OutlineButtons, ModalScreen[None]):
    DEFAULT_CSS = _CSS.format(name="AssistantScreen")
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, submissions_path: str, qa_file_path: str, purpose: str = PURPOSE_GRADING) -> None:
        super().__init__()
        self._submissions = submissions_path
        self._qa_file = qa_file_path
        self._purpose = purpose
        self._packets: list[Path] = []
        self._delete_armed = False

    @property
    def _rubrics(self) -> bool:
        return self._purpose == PURPOSE_RUBRICS

    def compose(self) -> ComposeResult:
        with Vertical() as dialog:
            dialog.border_title = (
                "▸ AI assistant — drafting rubrics" if self._rubrics else "▸ AI assistant — grading"
            )
            with VerticalScroll(classes="assistant-top"):
                if self._rubrics:
                    yield Static(
                        "Your assistant reads every answer to the questions that have no "
                        "partial credit yet, groups them by the mistakes that keep coming "
                        "back, and suggests partial-credit options for each. It grades "
                        "nothing: you accept or dismiss each draft (Suggestions…). It works "
                        "in a copy where every student is an og-… code: no names, IDs or "
                        "scans, and no answer image that Tesseract found the student's name in.",
                        classes="assistant-hint",
                    )
                else:
                    yield Static(
                        "Your assistant (Claude, ChatGPT, Codex, …) reads handwriting, grades "
                        "the written answers and points out mistakes in the answer key, in a "
                        "copy where every student is an og-… code: no names, IDs or scans, "
                        "only the answers still to grade, and no answer image that Tesseract "
                        "found the student's name in. Its grades come back as AI grades for "
                        "you to confirm (v, then y, while grading).",
                        classes="assistant-hint",
                    )
                submissions = Path(self._submissions).resolve()
                yield Static(
                    f"[b]Class[/b] [dim]{submissions.parent.name}/{submissions.name}[/dim]   "
                    f"[b]Answer key[/b] [dim]{Path(self._qa_file).name if self._qa_file else '(none chosen)'}[/dim]   "
                    f"[b]Copies go to[/b] [dim]{default_agent_output_dir(self._submissions).name}/ "
                    "beside the class folder[/dim]",
                    classes="assistant-hint",
                )
                yield Static(
                    "Use the account your school approved if it has one; on a personal "
                    "plan, turn off training on your data in the assistant's settings first.",
                    classes="assistant-hint",
                )
            yield Select[str]([], prompt="No copy made yet", id="assistant-packet", allow_blank=True)
            log = RichLog(id="assistant-log", wrap=True, markup=False, auto_scroll=False)
            log.border_title = "Copy for your assistant"
            yield log
            with Horizontal(classes="assistant-buttons"):
                yield Button("Make a copy", id="assistant-prepare", classes=button_roles.LAUNCH, compact=True)
                yield Button("Refresh", id="assistant-refresh", compact=True, disabled=True)
                yield Button("Suggestions…", id="assistant-suggestions", classes=button_roles.OPEN, compact=True, disabled=True)
                yield Button("Merge back…", id="assistant-merge", classes=button_roles.OPEN, compact=True, disabled=True)
                yield Button("Delete copy", id="assistant-delete", compact=True, disabled=True)
            with Horizontal(classes="assistant-buttons"):
                yield Button("Copy folder path", id="assistant-copy-path", compact=True, disabled=True)
                yield Button("Show folder", id="assistant-show-folder", compact=True, disabled=True)
                yield Button("Copy prompt", id="assistant-copy-prompt", compact=True, disabled=True)
                yield Button("Copy MCP setup", id="assistant-copy-mcp", compact=True, disabled=True)
                yield Button("Close", id="assistant-close", compact=True)

    def on_mount(self) -> None:
        self.query_one("#assistant-merge", Button).display = not self._rubrics
        self._reload_packets()
        if not self._packets:
            _log(
                self,
                [
                    "Press Make a copy. It takes a moment (every answer image is read "
                    "for the student's name first) and changes nothing in your class "
                    "folder except recording the key it needs to merge the copy back.",
                ],
            )

    # -- which copy ---------------------------------------------------------

    def _reload_packets(self, select: Path | None = None) -> None:
        try:
            self._packets = [
                path for path in list_agent_packets(self._submissions) if packet_purpose(path) == self._purpose
            ]
        except (OSError, ValueError):
            self._packets = []
        picker = self.query_one("#assistant-packet", Select)
        picker.set_options([(path.name, str(path)) for path in self._packets])
        wanted = select or (self._packets[0] if self._packets else None)
        if wanted is not None:
            picker.value = str(wanted)
        self._sync_buttons()

    def _current(self) -> Path | None:
        value = self.query_one("#assistant-packet", Select).value
        return Path(value) if isinstance(value, str) and value else None

    def _sync_buttons(self) -> None:
        missing = self._current() is None
        for button_id in (
            "assistant-copy-path",
            "assistant-show-folder",
            "assistant-copy-prompt",
            "assistant-copy-mcp",
            "assistant-refresh",
            "assistant-suggestions",
            "assistant-merge",
            "assistant-delete",
        ):
            self.query_one(f"#{button_id}", Button).disabled = missing
        self._disarm_delete()

    @on(Select.Changed, "#assistant-packet")
    def _on_packet_chosen(self, event: Select.Changed) -> None:
        event.stop()
        self._sync_buttons()
        packet = self._current()
        if packet is not None:
            self._show_packet(packet)

    def _show_packet(self, packet: Path, *, intro: Sequence[str] = ()) -> None:
        try:
            status = packet_status(packet)
            open_fixes = sum(1 for item in list_suggestions(packet) if not item.decision)
        except (OSError, ValueError, AgentError) as exc:
            _log(self, [f"Could not read {packet}: {exc}"])
            return
        lines = list(intro)
        if self._rubrics:
            questions = sorted({task.question_id for task in load_tasks(packet)})
            lines.append(
                f"{status.students} student(s); rubrics to draft for {', '.join(questions) or 'no question'}."
            )
        else:
            lines.append(
                f"{status.students} student(s): {status.ungraded} answer(s) still to grade, "
                f"{status.graded_by_agent} suggested by your assistant, "
                f"{status.graded} already graded by you or the rules (never changed there)."
            )
        if open_fixes:
            lines.append(
                f"Your assistant suggested {open_fixes} fix(es) to the answer key: "
                "press Suggestions… to accept or dismiss them."
            )
        prompt = RUBRIC_PROMPT if self._rubrics else STARTER_PROMPT
        after = "Press Suggestions… when it is done." if self._rubrics else "Press Merge back when it is done."
        lines += ["", *open_instructions(packet, prompt, mcp=True), "", after]
        _log(self, lines)

    # -- the steps ------------------------------------------------------------

    @on(Button.Pressed, "#assistant-prepare")
    def _on_prepare(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._qa_file:
            self.app.notify("Choose the answer key first (on the Tools card).", severity="error")
            return
        event.button.disabled = True
        _log(self, ["Making the copy and checking every answer image for the student's name…"])
        self._prepare()

    @work(thread=True, exclusive=True, group="assistant-prepare")
    def _prepare(self) -> None:
        try:
            packet = prepare_agent_packet(self._submissions, self._qa_file, purpose=self._purpose)
        except (AgentError, PacketError, OSError, ValueError) as exc:
            self.app.call_from_thread(self._prepare_failed, str(exc))
            return
        intro = ["Made a new copy. Names, IDs, scans and name/ID crops were left out."]
        if packet.left_out:
            intro.append(f"{packet.left_out} answer(s) it does not need stayed here.")
        intro.append(f"Checked {packet.images_checked} answer image(s) for the student's name or ID.")
        if packet.images_withheld:
            intro.append(
                f"{packet.images_withheld} image(s) may show one, or could not be read, and stayed "
                "here: grade those answers yourself."
            )
        if packet.scrubbed:
            intro.append(f"A name or ID was removed from {packet.scrubbed} answer(s) or comment(s).")
        if packet.unknown_version:
            intro.append(
                f"{packet.unknown_version} paper(s) have no known version and are not in it: "
                "set it in the review queue (R) and make another copy."
            )
        intro.append("")
        self.app.call_from_thread(self._prepared, packet.path, intro)

    def _prepared(self, packet: Path, intro: list[str]) -> None:
        if not self.is_attached:
            return
        self.query_one("#assistant-prepare", Button).disabled = False
        picker = self.query_one("#assistant-packet", Select)
        with picker.prevent(Select.Changed):
            self._reload_packets(select=packet)
        self._show_packet(packet, intro=intro)
        self.app.notify("Copy made — open your assistant in it.")

    def _prepare_failed(self, message: str) -> None:
        if not self.is_attached:
            return
        self.query_one("#assistant-prepare", Button).disabled = False
        _log(self, ["No copy was made:", "", message])
        self.app.notify("No copy was made — see the dialog.", severity="error")

    @on(Button.Pressed, "#assistant-copy-path")
    def _on_copy_path(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is not None:
            clipboard.copy_text(self.app, str(packet))
            self.app.notify("Folder path copied — paste it after cd, or into Open Folder.")

    @on(Button.Pressed, "#assistant-copy-prompt")
    def _on_copy_prompt(self, event: Button.Pressed) -> None:
        event.stop()
        clipboard.copy_text(self.app, RUBRIC_PROMPT if self._rubrics else STARTER_PROMPT)
        self.app.notify("Prompt copied — paste it into your assistant.")

    @on(Button.Pressed, "#assistant-show-folder")
    def _on_show_folder(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is not None:
            _show_folder(self, packet)

    @on(Button.Pressed, "#assistant-copy-mcp")
    def _on_copy_mcp(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is None:
            return
        text = client_instructions(packet)
        clipboard.copy_text(self.app, text)
        _log(self, ["Copied. Paste the part for your app:", "", *text.splitlines()])
        self.app.notify("MCP setup copied.")

    @on(Button.Pressed, "#assistant-refresh")
    def _on_refresh(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is not None:
            self._show_packet(packet)

    @on(Button.Pressed, "#assistant-suggestions")
    def _on_suggestions(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is None:
            return

        def closed(_result: None) -> None:
            if self.is_attached and self._current() is not None:
                self._show_packet(self._current())

        self.app.push_screen(
            SuggestionsScreen(packet=packet, qa_file_path=self._qa_file, submissions_path=self._submissions),
            closed,
        )

    @on(Button.Pressed, "#assistant-merge")
    def _on_merge(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is None:
            return

        def merged(done: bool | None) -> None:
            if not self.is_attached or self._current() is None:
                return
            intro = (
                ["Merged. Once you have checked the grades, press Delete copy: nothing", "in it is needed any more.", ""]
                if done
                else []
            )
            self._show_packet(self._current(), intro=intro)

        self.app.push_screen(
            MergeScreen(submissions_path=self._submissions, incoming_path=str(packet)), merged
        )

    @on(Button.Pressed, "#assistant-delete")
    def _on_delete(self, event: Button.Pressed) -> None:
        event.stop()
        packet = self._current()
        if packet is None:
            return
        if not self._delete_armed:
            # Deleting cannot be undone: a second press, on a relabelled button.
            self._delete_armed = True
            event.button.label = "Really delete?"
            self.app.notify("Press Really delete? to delete the copy. Merge it back first.")
            return
        try:
            delete_packet(packet, self._submissions)
        except (AgentError, OSError) as exc:
            self.app.notify(f"Not deleted: {exc}", severity="error")
            return
        self._reload_packets()
        _log(self, [f"Deleted {packet}."])
        self.app.notify("Copy deleted.")

    def _disarm_delete(self) -> None:
        self._delete_armed = False
        self.query_one("#assistant-delete", Button).label = "Delete copy"

    @on(Button.Pressed, "#assistant-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class SuggestionsScreen(OutlineButtons, ModalScreen[None]):
    """The assistant's suggested fixes to the answer key, to accept or dismiss."""

    DEFAULT_CSS = _CSS.format(name="SuggestionsScreen") + """
    SuggestionsScreen .suggestion {
        height: auto;
        margin-bottom: 1;
    }
    SuggestionsScreen .suggestion Static {
        width: 1fr;
    }
    SuggestionsScreen .suggestion Button {
        margin-left: 1;
    }
    #suggestion-list {
        height: 1fr;
    }
    SuggestionsScreen RichLog {
        height: 6;
    }
    """
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, packet: Path, qa_file_path: str, submissions_path: str) -> None:
        super().__init__()
        self._packet = packet
        self._qa_file = qa_file_path
        self._submissions = submissions_path

    def compose(self) -> ComposeResult:
        with Vertical() as dialog:
            dialog.border_title = "▸ The assistant's suggested fixes"
            yield Static(
                "Accept writes the fix into your answer key (only that question) and "
                "into the assistant's copy, so it grades the rest with the corrected "
                "key. A rubric change is refused if a grade you already saved uses an "
                "option it would drop. Dismiss records that you said no.",
                classes="assistant-hint",
            )
            yield VerticalScroll(id="suggestion-list")
            log = RichLog(wrap=True, markup=False, auto_scroll=True)
            log.border_title = "Result"
            yield log
            with Horizontal(classes="assistant-buttons"):
                yield Button("Close", id="suggestions-close", compact=True)

    def on_mount(self) -> None:
        self._render_list()

    def _render_list(self) -> None:
        holder = self.query_one("#suggestion-list", VerticalScroll)
        holder.remove_children()
        found = list_suggestions(self._packet)
        if not found:
            holder.mount(Static("No suggestions yet.", classes="assistant-hint"))
            return
        for item in found:
            version = f" (version {item.version})" if item.version else ""
            text = f"[b]{item.id}[/b] {item.kind} · {item.question}{version}: {item.message}"
            if item.fixable:
                text += f"\n[dim]{item.describe_fix()}[/dim]"
            if item.decision:
                text += f"\n[dim]{item.decision}[/dim]"
            buttons = []
            if not item.decision:
                buttons = [
                    Button("Accept", id=f"accept-{item.id}", classes=button_roles.LAUNCH, compact=True, disabled=not item.fixable),
                    Button("Dismiss", id=f"dismiss-{item.id}", compact=True),
                ]
            holder.mount(Horizontal(Static(text), *buttons, classes="suggestion"))

    @on(Button.Pressed)
    def _on_button(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        if button_id == "suggestions-close":
            event.stop()
            self.dismiss(None)
            return
        action, _, suggestion_id = button_id.partition("-")
        if action not in ("accept", "dismiss") or not suggestion_id:
            return
        event.stop()
        log = self.query_one(RichLog)
        try:
            if action == "accept":
                if not self._qa_file:
                    raise AgentError("choose the class's answer key on the Tools card first")
                done = accept_suggestion(
                    self._packet, suggestion_id, qa_file=self._qa_file, submissions=self._submissions
                )
                log.write(f"{suggestion_id}: {done}.")
            else:
                dismiss_suggestion(self._packet, suggestion_id)
                log.write(f"{suggestion_id} dismissed.")
        except (AgentError, OSError, ValueError) as exc:
            log.write(f"{suggestion_id} not applied: {exc}")
        self._render_list()

    def action_close(self) -> None:
        self.dismiss(None)


def key_folders(class_folder: Path) -> list[Path]:
    """Answer-key folders made for this class earlier, newest first."""
    base = default_key_folder(class_folder)
    found = []
    for candidate in base.parent.glob(f"{base.name}*"):
        marker = candidate / ".opengrader" / AGENT_MARKER_FILENAME
        try:
            if marker.is_file() and read_marker(candidate).get("kind") == KIND_ANSWER_KEY:
                found.append(candidate)
        except (OSError, ValueError):
            continue
    return sorted(found, key=lambda path: path.stat().st_mtime, reverse=True)


class AnswerKeyAssistantScreen(OutlineButtons, ModalScreen[None]):
    """Your assistant writes or fills the class's qa.md; no student data involved."""

    DEFAULT_CSS = _CSS.format(name="AnswerKeyAssistantScreen")
    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, class_folder: str, qa_file: str = "", submissions: str = "") -> None:
        super().__init__()
        self._class = Path(class_folder)
        self._qa_file = Path(qa_file) if qa_file else None
        self._submissions = submissions
        self._destination = self._qa_file or self._class / KEY_FILENAME
        self._workspace: Path | None = None
        self._replace_armed = False

    def compose(self) -> ComposeResult:
        with Vertical() as dialog:
            dialog.border_title = "▸ AI assistant — the answer key"
            with VerticalScroll(classes="assistant-top"):
                if self._qa_file is not None:
                    task = (
                        f"Your assistant fills the blank answers in {self._qa_file.name}, from the "
                        "questions themselves or from your own answer key if you add it below."
                    )
                else:
                    task = (
                        "Your assistant writes qa.md, the answer key Opengrader grades with, from "
                        "your own answer key (Word, PDF, notes): choose it below."
                    )
                yield Static(
                    task + " It works in its own folder beside the class folder, with no student "
                    "work in it, and checks the key before you use it.",
                    classes="assistant-hint",
                )
                yield Static("[b]Your own answer key[/b]  [dim]optional when the class already has qa.md[/dim]")
                with Horizontal(classes="path-row"):
                    yield Input(placeholder="Path to key.docx / key.pdf / notes.md…", id="key-source")
                    yield Button("Browse", id="key-source-browse", classes=button_roles.OPEN, compact=True)
                yield Static(
                    f"[b]The finished key goes to[/b]  [dim]{self._destination.parent.name}/{self._destination.name}[/dim]",
                    classes="assistant-hint",
                )
            log = RichLog(wrap=True, markup=False, auto_scroll=False)
            log.border_title = "Your assistant's folder"
            yield log
            with Horizontal(classes="assistant-buttons"):
                yield Button("Give it to my assistant", id="key-prepare", classes=button_roles.LAUNCH, compact=True)
                yield Button("Check the key", id="key-check", compact=True, disabled=True)
                yield Button("Use this key", id="key-use", classes=button_roles.LAUNCH, compact=True, disabled=True)
            with Horizontal(classes="assistant-buttons"):
                yield Button("Copy folder path", id="key-copy-path", compact=True, disabled=True)
                yield Button("Show folder", id="key-show-folder", compact=True, disabled=True)
                yield Button("Copy prompt", id="key-copy-prompt", compact=True, disabled=True)
                yield Button("Close", id="key-close", compact=True)

    def on_mount(self) -> None:
        earlier = key_folders(self._class)
        if earlier:
            self._set_workspace(earlier[0], [f"Picking up the folder made earlier: {earlier[0].name}."])
            return
        _log(self, ["Press Give it to my assistant. Opengrader makes its folder beside the class folder."])

    def _set_workspace(self, workspace: Path, intro: Sequence[str]) -> None:
        self._workspace = workspace
        for button_id in ("key-check", "key-use", "key-copy-path", "key-show-folder", "key-copy-prompt"):
            self.query_one(f"#{button_id}", Button).disabled = False
        _log(self, [*intro, "", *open_instructions(workspace, KEY_PROMPT), "", "Press Check the key, then Use this key, when it is done."])

    @on(Button.Pressed, "#key-source-browse")
    def _on_browse_source(self, event: Button.Pressed) -> None:
        event.stop()
        _browse_into(self, self.query_one("#key-source", Input), title="Choose your answer key", mode="file")

    @on(Button.Pressed, "#key-prepare")
    def _on_prepare(self, event: Button.Pressed) -> None:
        event.stop()
        raw = self.query_one("#key-source", Input).value.strip().strip("'\"")
        if self._workspace is not None and not raw:
            self._set_workspace(self._workspace, [f"Your assistant's folder is {self._workspace.name}."])
            return
        try:
            workspace = prepare_key_folder(
                qa_file=self._qa_file,
                source=Path(raw).expanduser() if raw else None,
                class_folder=self._class,
                submissions=self._submissions or None,
            )
        except (AgentError, OSError) as exc:
            _log(self, ["Nothing was given to the assistant:", "", str(exc)])
            return
        self._set_workspace(workspace.path, [f"Made {workspace.path.name}, beside the class folder."])

    @on(Button.Pressed, "#key-check")
    def _on_check(self, event: Button.Pressed) -> None:
        event.stop()
        if self._workspace is None:
            return
        key = self._workspace / KEY_FILENAME
        if not key.is_file():
            _log(self, [f"{key.name} is not there yet: the assistant has not written it."])
            return
        result = check_key_file(key)
        verdict = "It loads." if result.ok else "It does not load yet."
        _log(self, [verdict, *result.lines()] if result.lines() else [verdict, "No warnings."])

    @on(Button.Pressed, "#key-use")
    def _on_use(self, event: Button.Pressed) -> None:
        event.stop()
        if self._workspace is None:
            return
        replace = self._replace_armed
        try:
            result = use_key(self._workspace, self._destination, replace=replace)
        except AgentError as exc:
            if self._destination.exists() and not replace and "replace it on purpose" in str(exc):
                self._replace_armed = True
                event.button.label = "Replace it?"
                _log(self, [f"{self._destination.name} changed since the assistant started. Press Replace it? to overwrite it."])
            else:
                _log(self, [str(exc)])
            return
        self._replace_armed = False
        event.button.label = "Use this key"
        lines = [f"Copied the answer key to {self._destination}."]
        lines += [f"  warning: {warning}" for warning in result.warnings]
        _log(self, lines)
        self.app.notify("Answer key copied into the class folder.")

    @on(Button.Pressed, "#key-copy-path")
    def _on_copy_path(self, event: Button.Pressed) -> None:
        event.stop()
        if self._workspace is not None:
            clipboard.copy_text(self.app, str(self._workspace))
            self.app.notify("Folder path copied — paste it after cd, or into Open Folder.")

    @on(Button.Pressed, "#key-show-folder")
    def _on_show_folder(self, event: Button.Pressed) -> None:
        event.stop()
        if self._workspace is not None:
            _show_folder(self, self._workspace)

    @on(Button.Pressed, "#key-copy-prompt")
    def _on_copy_prompt(self, event: Button.Pressed) -> None:
        event.stop()
        clipboard.copy_text(self.app, KEY_PROMPT)
        self.app.notify("Prompt copied — paste it into your assistant.")

    @on(Button.Pressed, "#key-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

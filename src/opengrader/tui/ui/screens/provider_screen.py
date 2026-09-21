"""Add, inspect, test and remove AI providers from the TUI (§7.5).

The TUI face of `opengrader-providers`. Both go through the same helpers in
`core/ai_config/manifests.py`, which validate a manifest before it is written,
never overwrite one unasked, and describe a provider the same way everywhere.

This screen does not pick the active provider. That is still AI Setup's job,
and AI Setup does not know this screen exists. A provider added here shows up
in AI Setup's dropdown only because the registry says it is there. If that
ever needs an edit to `ai_settings_screen.py`, the registry contract has broken
somewhere (§7.9, Phase 4's exit criterion).

Anything that would reach a cloud host or delete a file needs a second press.
The first press states what will happen, and changing the selection disarms
it. This is not the L1 consent modal, which is Phase 5's. It only makes sure
one stray keypress cannot send anything.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Select, Static

from opengrader.core import ai_config
from opengrader.core.ai_config import manifests as ai_manifests


class ProviderScreen(ModalScreen[bool]):
    """Dismisses with True when the set of registered providers changed."""

    DEFAULT_CSS = """
    ProviderScreen {
        align: center middle;
    }

    #prov-dialog {
        width: 86%;
        max-width: 124;
        height: 88%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #prov-hint, #prov-problems, #prov-details {
        width: 100%;
        height: auto;
    }

    #prov-hint, #prov-problems {
        color: $text-muted;
    }

    #prov-details {
        margin-top: 1;
    }

    .prov-row {
        height: auto;
        margin-top: 1;
    }

    .prov-row Select {
        width: 1fr;
    }

    .prov-row Button {
        margin-left: 1;
    }

    #prov-log {
        height: 1fr;
        min-height: 5;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #prov-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #prov-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self) -> None:
        super().__init__()
        self._changed = False
        # (action, provider name) awaiting its confirming second press.
        self._armed: tuple[str, str] | None = None

    # --- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        examples = ai_manifests.example_names()
        with Vertical(id="prov-dialog") as dialog:
            dialog.border_title = "▸ AI providers"
            yield Static(
                "Opengrader runs its AI on this computer by default. To use a "
                "service your department runs or one you pay for, add a "
                "provider here: a small file that says where the service is "
                "and which models to ask for. It never holds your key, only "
                "the name of the variable the key is in. Choose which "
                "provider to use in AI setup.\n"
                f"Provider files live in {ai_config.manifests_dir()}",
                id="prov-hint",
            )
            with Horizontal(classes="prov-row"):
                yield Select(
                    self._provider_options(),
                    value=ai_config.DEFAULT_PROVIDER,
                    allow_blank=False,
                    id="prov-select",
                )
                yield Button("Test", id="prov-test", compact=True)
                yield Button("Remove", id="prov-remove", variant="error", compact=True)
            yield Static("", id="prov-details")
            yield Static("", id="prov-problems")
            with Horizontal(classes="prov-row"):
                yield Select(
                    [(name, name) for name in examples],
                    prompt="Start from an example…",
                    id="prov-example",
                )
                yield Button(
                    "Add example",
                    id="prov-add-example",
                    variant="primary",
                    compact=True,
                    disabled=not examples,
                )
            log = RichLog(id="prov-log", wrap=True, auto_scroll=True, markup=False)
            log.border_title = "Result"
            yield log
            with Horizontal(id="prov-buttons"):
                yield Button("Re-read provider files", id="prov-reload", compact=True)
                yield Button("Close", id="prov-close", compact=True)

    def on_mount(self) -> None:
        self._show_selected()
        self._show_problems()

    # --- state -------------------------------------------------------------

    @staticmethod
    def _provider_options() -> list[tuple[str, str]]:
        options = []
        for spec in ai_config.registered_providers():
            suffix = "  (cloud)" if spec.sends_data_off_machine else ""
            options.append((f"{spec.label}{suffix}", spec.name))
        return options

    def _selected(self) -> ai_config.ProviderSpec | None:
        value = self.query_one("#prov-select", Select).value
        if not isinstance(value, str):
            return None
        for spec in ai_config.registered_providers():
            if spec.name == value:
                return spec
        return None

    def _show_selected(self) -> None:
        spec = self._selected()
        details = self.query_one("#prov-details", Static)
        remove = self.query_one("#prov-remove", Button)
        if spec is None:
            details.update("")
            remove.disabled = True
            return
        rows = ai_manifests.describe_provider(spec)
        width = max(len(label) for label, _ in rows) + 2
        lines = [f"{label + ':':<{width}}{value}" for label, value in rows]
        details.update("\n".join(lines))
        remove.disabled = spec.source != "user"

    def _show_problems(self) -> None:
        problems = ai_config.manifest_problems()
        text = ""
        if problems:
            text = "Skipped provider files:\n" + "\n".join(f"  • {p}" for p in problems)
        self.query_one("#prov-problems", Static).update(text)

    def _refresh_providers(self, *, select: str | None = None) -> None:
        picker = self.query_one("#prov-select", Select)
        options = self._provider_options()
        names = {name for _label, name in options}
        keep = select if select in names else picker.value
        picker.set_options(options)
        picker.value = keep if keep in names else ai_config.DEFAULT_PROVIDER
        self._armed = None
        self._show_selected()
        self._show_problems()

    def _log(self, *lines: str, clear: bool = True) -> None:
        log = self.query_one("#prov-log", RichLog)
        if clear:
            log.clear()
        for line in lines:
            log.write(line)

    def _arm(self, action: str, name: str) -> bool:
        """True if this press confirms an earlier one; otherwise arms it."""
        if self._armed == (action, name):
            self._armed = None
            return True
        self._armed = (action, name)
        return False

    # --- events ------------------------------------------------------------

    @on(Select.Changed, "#prov-select")
    def _on_select(self, event: Select.Changed) -> None:
        event.stop()
        self._armed = None
        self._show_selected()

    @on(Button.Pressed, "#prov-add-example")
    def _on_add_example(self, event: Button.Pressed) -> None:
        event.stop()
        choice = self.query_one("#prov-example", Select).value
        if not isinstance(choice, str):
            self._log("Pick an example first.")
            return
        try:
            text = ai_manifests.example_text(choice)
            dest, spec = ai_manifests.install_manifest(text)
        except ai_manifests.ManifestExistsError as exc:
            self._log(f"{exc} Remove it first if you want to start over from the example.")
            return
        except ai_manifests.ManifestError as exc:
            self._log(str(exc))
            return
        ai_config.reload_manifests()
        self._changed = True
        self._refresh_providers(select=spec.name)
        lines = [f"Added {spec.label}: wrote {dest}."]
        if spec.sends_data_off_machine:
            lines.append(
                f"This is a cloud provider. Choosing it in AI setup sends "
                f"student work to {spec.default_host}."
            )
        if spec.api_key_env:
            lines.append(
                f"Set {spec.api_key_env} in the environment Opengrader starts "
                "from, then use Test."
            )
        lines.append(
            "Edit the file to change its host or models, then press "
            "'Re-read provider files'."
        )
        self._log(*lines)

    @on(Button.Pressed, "#prov-remove")
    def _on_remove(self, event: Button.Pressed) -> None:
        event.stop()
        spec = self._selected()
        if spec is None or spec.source != "user":
            return
        if not self._arm("remove", spec.name):
            self._log(
                f"Press Remove again to delete {spec.manifest_path}. "
                "This cannot be undone."
            )
            return
        try:
            path = ai_manifests.remove_manifest(spec)
        except ai_manifests.ManifestError as exc:
            self._log(str(exc))
            return
        ai_config.reload_manifests()
        self._changed = True
        self._refresh_providers(select=ai_config.DEFAULT_PROVIDER)
        lines = [f"Removed {spec.label} ({path})."]
        if ai_config.load_settings().provider == spec.name:
            lines.append(
                f"It was the provider chosen in AI setup, so Opengrader falls "
                f"back to {ai_config.OLLAMA.label} until you choose another."
            )
        self._log(*lines)

    @on(Button.Pressed, "#prov-reload")
    def _on_reload(self, event: Button.Pressed) -> None:
        event.stop()
        before = {spec.name for spec in ai_config.registered_providers()}
        problems = ai_config.reload_manifests()
        after = {spec.name for spec in ai_config.registered_providers()}
        if before != after:
            self._changed = True
        self._refresh_providers()
        added, gone = sorted(after - before), sorted(before - after)
        lines = ["Re-read the provider files."]
        if added:
            lines.append(f"New: {', '.join(added)}.")
        if gone:
            lines.append(f"No longer present: {', '.join(gone)}.")
        if problems:
            lines.append(f"{len(problems)} file(s) skipped; the reasons are listed above.")
        self._log(*lines)

    @on(Button.Pressed, "#prov-test")
    def _on_test(self, event: Button.Pressed) -> None:
        event.stop()
        spec = self._selected()
        if spec is None:
            return
        if spec.sends_data_off_machine and not self._arm("test", spec.name):
            self._log(
                f"Test sends one short prompt, with no student data in it, to "
                f"{spec.default_host}. Press Test again to send it."
            )
            return
        self._armed = None
        for button in self.query(Button):
            button.disabled = True
        self._log(f"Testing {spec.label}…")
        self.run_worker(lambda: self._test(spec.name), thread=True, exclusive=True)

    def _test(self, name: str) -> None:
        resolved = ai_config.for_provider(name)
        lines: list[str] = []
        try:
            ai_config.ensure_ready(resolved, purpose="text")
        except ai_config.AIUnavailableError as exc:
            lines.append(str(exc))
            self.app.call_from_thread(self._finish_test, lines)
            return
        measurement = ai_config.measure_generate_latency(
            resolved, role="grading", prompt=ai_manifests.CONNECTION_TEST_PROMPT
        )
        lines.append(measurement.describe())
        if measurement.ok:
            lines.append(f"{resolved.provider.label} is reachable and answering.")
        elif resolved.provider.setup_hint:
            lines.append(resolved.provider.setup_hint)
        self.app.call_from_thread(self._finish_test, lines)

    def _finish_test(self, lines: list[str]) -> None:
        if not self.is_attached:
            return
        for button in self.query(Button):
            button.disabled = False
        self._show_selected()
        self._log(*lines)

    @on(Button.Pressed, "#prov-close")
    def _on_close(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(self._changed)

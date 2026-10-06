from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Collapsible, OptionList, Select, Static
from textual.widgets.option_list import Option

from opengrader.core import app_config
from opengrader.core.app_config import keystore
from opengrader.core.app_config import manifests as ai_manifests
from opengrader.core.app_config import setup as ai_setup
from opengrader.tui.ui.screens.cloud_consent_screen import CredentialConsentScreen
from opengrader.tui.ui.screens.provider_setup_screen import ProviderSetupScreen


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

    /* A provider's details and file path can run to many lines, and the
       Advanced card opens downwards: this part scrolls, so Close is never
       pushed off the screen. */
    #prov-body {
        height: 1fr;
    }

    #prov-list-title {
        margin-top: 1;
        text-style: bold;
    }

    #prov-list {
        height: auto;
        max-height: 9;
    }

    #prov-actions {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #prov-actions Button {
        margin-left: 1;
    }

    #prov-hint, #prov-problems, #prov-details {
        width: 100%;
        height: auto;
    }

    #prov-hint, #prov-problems, #prov-setup-hint {
        color: $text-muted;
    }

    #prov-setup-hint {
        width: 1fr;
        height: auto;
    }

    #prov-advanced {
        margin-top: 1;
    }

    #prov-files-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #prov-reload-row {
        align-horizontal: right;
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
        width: 100%;
        height: auto;
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
        self._armed: tuple[str, str] | None = None
        self._log_lines: list[str] = []
        # What each local provider said it has installed: name -> (models,
        # why not). A name that is absent has not answered yet.
        self._local: dict[str, tuple[tuple[str, ...], str]] = {}

    def compose(self) -> ComposeResult:
        examples = ai_manifests.example_names()
        with Vertical(id="prov-dialog") as dialog:
            dialog.border_title = "▸ AI providers"
            # Everything above Close scrolls as one piece, so that Close
            # stays where it is when Advanced is opened.
            with VerticalScroll(id="prov-body"):
                yield Static(
                    "Opengrader runs its AI on this computer by default. To use a "
                    "service your department runs or one you pay for, add a "
                    "provider here: a small file that says where the service is "
                    "and which models to ask for. It never holds your key — that "
                    "is kept in this computer's password store, or in an "
                    "environment variable. Choose which provider to use in AI "
                    "setup.",
                    id="prov-hint",
                )
                with Horizontal(classes="prov-row"):
                    # The words first and the button at the right edge, like
                    # every other row of this dialog.
                    yield Static(
                        "Add a service: pick it from a list, paste its key, test it.",
                        id="prov-setup-hint",
                    )
                    yield Button(
                        "Set up a provider…",
                        id="prov-setup",
                        variant="primary",
                        compact=True,
                    )
                # Every provider at a glance — where it runs and whether it
                # is ready — rather than one at a time behind a dropdown.
                yield Static("Your providers", id="prov-list-title")
                yield OptionList(id="prov-list")
                with Horizontal(id="prov-actions"):
                    yield Button("Test", id="prov-test", compact=True)
                    yield Button("Forget key", id="prov-forget-key", compact=True)
                    yield Button("Remove", id="prov-remove", variant="error", compact=True)
                # What the last button press did, right under the buttons
                # that cause it. Hidden until there is something to say.
                log = Static("", id="prov-log", markup=False)
                log.border_title = "Result"
                yield log
                yield Static("", id="prov-details")
                yield Static("", id="prov-problems")
                # Everything for someone who edits provider files by hand, folded
                # away: 'Set up a provider' above does all of it for everyone else.
                with Collapsible(
                    title="Advanced — provider files, for editing by hand",
                    id="prov-advanced",
                    collapsed=True,
                ):
                    yield Static(
                        f"Provider files live in {app_config.manifests_dir()}\n"
                        "Start one from an example, or edit a file there, then "
                        "press 'Re-read provider files'.",
                        id="prov-files-hint",
                    )
                    yield from self._compose_advanced(examples)
            with Horizontal(id="prov-buttons"):
                yield Button("Close", id="prov-close", compact=True)

    @staticmethod
    def _compose_advanced(examples: tuple[str, ...]) -> ComposeResult:
        with Horizontal(classes="prov-row"):
            # An example that signs in with a cloud account instead of a key
            # is marked, since it needs setup outside Opengrader.
            yield Select(
                [
                    (
                        f"{name} (advanced)"
                        if ai_setup.is_advanced_example(name)
                        else name,
                        name,
                    )
                    for name in examples
                ],
                prompt="Start from an example file…",
                id="prov-example",
            )
            yield Button(
                "Add example",
                id="prov-add-example",
                compact=True,
                disabled=not examples,
            )
        with Horizontal(classes="prov-row", id="prov-reload-row"):
            yield Button("Re-read provider files", id="prov-reload", compact=True)

    def on_mount(self) -> None:
        self.query_one("#prov-log", Static).display = False
        self._refresh_providers(select=app_config.DEFAULT_PROVIDER)

    def _row(self, spec: app_config.ProviderSpec, width: int, in_use: str) -> str:
        status = ai_setup.provider_status(
            spec, local=self._local.get(spec.name), in_use=spec.name == in_use
        )
        return f"{spec.label:<{width}}  {status}"

    def _rows(self) -> list[tuple[str, str]]:
        """(provider name, the line the list shows for it), Ollama first."""
        specs = app_config.registered_providers()
        width = max((len(spec.label) for spec in specs), default=0)
        in_use = app_config.resolve().provider.name
        return [(spec.name, self._row(spec, width, in_use)) for spec in specs]

    def _fill_list(self, select: str) -> None:
        """Rebuild the list from the registry and put the highlight on `select`."""
        rows = self._rows()
        listing = self.query_one("#prov-list", OptionList)
        listing.clear_options()
        listing.add_options([Option(text, id=name) for name, text in rows])
        names = [name for name, _text in rows]
        listing.highlighted = names.index(select) if select in names else 0

    def _update_rows(self) -> None:
        """Rewrite each row's status where it stands.

        Not a rebuild: that would move the highlight, and moving the
        highlight cancels a Remove or Forget key waiting for its second press.
        """
        listing = self.query_one("#prov-list", OptionList)
        shown = {
            listing.get_option_at_index(index).id
            for index in range(listing.option_count)
        }
        for name, text in self._rows():
            if name in shown:
                listing.replace_option_prompt(name, text)

    def select_provider(self, name: str) -> None:
        """Move the highlight to a provider, as clicking its row does."""
        listing = self.query_one("#prov-list", OptionList)
        for index in range(listing.option_count):
            if listing.get_option_at_index(index).id == name:
                listing.highlighted = index
                return

    def _selected(self) -> app_config.ProviderSpec | None:
        listing = self.query_one("#prov-list", OptionList)
        if listing.highlighted is None:
            return None
        value = listing.get_option_at_index(listing.highlighted).id
        for spec in app_config.registered_providers():
            if spec.name == value:
                return spec
        return None

    def _probe_local(self) -> None:
        """Ask each provider on this computer what it has installed, off the UI thread.

        Cloud providers are never asked here — `local_models` answers None
        for them without sending anything — because a request to one carries
        the API key. A group of its own, so Test (which is exclusive in the
        default group) and this do not cancel each other.
        """
        names = [
            spec.name
            for spec in app_config.registered_providers()
            if not spec.sends_data_off_machine
        ]
        if names:
            self.run_worker(
                lambda: self._local_worker(names),
                thread=True,
                exclusive=True,
                group="prov-local",
            )

    def _local_worker(self, names: list[str]) -> None:
        for name in names:
            found = ai_setup.local_models(name)
            if found is not None:
                self.app.call_from_thread(self._show_local, name, found)

    def _show_local(self, name: str, found: tuple[tuple[str, ...], str]) -> None:
        if not self.is_attached:
            return
        self._local[name] = found
        self._update_rows()
        self._show_selected()

    def _show_selected(self) -> None:
        spec = self._selected()
        details = self.query_one("#prov-details", Static)
        remove = self.query_one("#prov-remove", Button)
        forget = self.query_one("#prov-forget-key", Button)
        if spec is None:
            details.update("")
            remove.disabled = True
            forget.disabled = True
            return
        # Only a key Opengrader itself saved can be forgotten here; one in an
        # environment variable is not ours to remove.
        forget.disabled = self._saved_key(spec) is None
        rows = ai_manifests.describe_provider(spec)
        found = self._local.get(spec.name)
        if found is not None:
            models, why_not = found
            rows.append(
                (
                    "installed",
                    ", ".join(models)
                    or (f"could not ask it: {why_not}" if why_not else "no models yet"),
                )
            )
        width = max(len(label) for label, _ in rows) + 2
        lines = [f"{label + ':':<{width}}{value}" for label, value in rows]
        details.update("\n".join(lines))
        remove.disabled = spec.source != "user"

    @staticmethod
    def _saved_key(spec: app_config.ProviderSpec) -> str | None:
        if not spec.api_key_env:
            return None
        return keystore.lookup(keystore.account_for(spec))

    def _show_problems(self) -> None:
        problems = app_config.manifest_problems()
        text = ""
        if problems:
            text = "Skipped provider files:\n" + "\n".join(f"  • {p}" for p in problems)
        self.query_one("#prov-problems", Static).update(text)

    def _refresh_providers(self, *, select: str | None = None) -> None:
        current = self._selected()
        names = {spec.name for spec in app_config.registered_providers()}
        keep = select if select in names else (current.name if current else "")
        self._fill_list(keep if keep in names else app_config.DEFAULT_PROVIDER)
        self._armed = None
        self._show_selected()
        self._show_problems()
        self._probe_local()

    def _log(self, *lines: str, clear: bool = True) -> None:
        log = self.query_one("#prov-log", Static)
        if clear:
            self._log_lines = []
        self._log_lines.extend(lines)
        log.update("\n".join(self._log_lines))
        log.display = True
        log.scroll_visible(animate=False)

    def _arm(self, action: str, name: str) -> bool:
        """True if this press confirms an earlier one; otherwise arms it."""
        if self._armed == (action, name):
            self._armed = None
            return True
        self._armed = (action, name)
        return False

    @on(OptionList.OptionHighlighted, "#prov-list")
    def _on_select(self, event: OptionList.OptionHighlighted) -> None:
        event.stop()
        self._armed = None
        self._show_selected()

    @on(Collapsible.Expanded, "#prov-advanced")
    def _on_advanced_opened(self, event: Collapsible.Expanded) -> None:
        event.stop()
        # The card opens downwards, usually past the bottom of the scrolling
        # part, and it is the last thing in it: go to the end, or it looks
        # like nothing happened. scroll_end waits for the new layout itself.
        self.query_one("#prov-body", VerticalScroll).scroll_end(animate=False)

    @on(Button.Pressed, "#prov-setup")
    def _on_setup(self, event: Button.Pressed) -> None:
        event.stop()
        self._armed = None
        spec = self._selected()
        preselect = spec.name if spec is not None and spec.source == "user" else ""

        def _done(name: str | None) -> None:
            if not name:
                return
            self._changed = True
            self._refresh_providers(select=name)
            self._log(
                f"{name} is set up. Choose it in AI setup when you want to use it."
            )

        self.app.push_screen(ProviderSetupScreen(preselect=preselect), _done)

    @on(Button.Pressed, "#prov-forget-key")
    def _on_forget_key(self, event: Button.Pressed) -> None:
        event.stop()
        spec = self._selected()
        if spec is None or self._saved_key(spec) is None:
            return
        store = keystore.status().name
        if not self._arm("forget", spec.name):
            self._log(
                f"Press Forget key again to delete {spec.label}'s key from "
                f"{store}. The key itself still works until you revoke it on "
                "the service's website."
            )
            return
        try:
            keystore.delete(keystore.account_for(spec))
        except keystore.KeyStoreError as exc:
            self._log(f"The key was not removed: {exc}")
            return
        app_config.clear_credential_cache()
        self._changed = True
        self._update_rows()
        self._show_selected()
        self._log(f"Removed {spec.label}'s key from {store}.")

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
        app_config.reload_manifests()
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
                f"It needs a key: use 'Set up a provider…' to paste one, or "
                f"set {spec.api_key_env} in the environment Opengrader starts "
                "from. Then use Test."
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
        lines = [f"Removed {spec.label} ({path})."]
        # A key saved for a provider that no longer exists would sit in the
        # password store for good, with nothing left on screen to remove it.
        if spec.api_key_env:
            try:
                if keystore.delete(keystore.account_for(spec)):
                    lines.append(
                        f"Its saved key was removed from {keystore.status().name}."
                    )
            except keystore.KeyStoreError as exc:
                lines.append(f"Its saved key could not be removed: {exc}")
            app_config.clear_credential_cache()
        app_config.reload_manifests()
        self._changed = True
        self._refresh_providers(select=app_config.DEFAULT_PROVIDER)
        if app_config.load_settings().provider == spec.name:
            lines.append(
                f"It was the provider chosen in AI setup, so Opengrader falls "
                f"back to {app_config.OLLAMA.label} until you choose another."
            )
        self._log(*lines)

    @on(Button.Pressed, "#prov-reload")
    def _on_reload(self, event: Button.Pressed) -> None:
        event.stop()
        before = {spec.name for spec in app_config.registered_providers()}
        problems = app_config.reload_manifests()
        after = {spec.name for spec in app_config.registered_providers()}
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
        if spec.credential_command and not app_config.consent.has_credential_consent(
            app_config.load_settings(), spec
        ):
            # The literal command must be approved before it can run —
            # needs a worker, since showing that modal and waiting for it
            # can't happen from a plain button handler.
            self.run_worker(
                self._gate_credential_then_test(spec), exclusive=True
            )
            return
        self._start_test(spec)

    async def _gate_credential_then_test(self, spec: app_config.ProviderSpec) -> None:
        accepted = await self.app.push_screen_wait(CredentialConsentScreen(spec))
        if not accepted:
            self._log("Test cancelled — the credential command was not approved.")
            return
        settings = app_config.consent.grant_credential_consent(
            app_config.load_settings(), spec
        )
        try:
            app_config.save_settings(settings)
        except OSError as exc:
            self._log(f"Could not save credential consent: {exc}")
            return
        self._start_test(spec)

    def _start_test(self, spec: app_config.ProviderSpec) -> None:
        for button in self.query(Button):
            button.disabled = True
        self._log(f"Testing {spec.label}…")
        self.run_worker(lambda: self._test(spec.name), thread=True, exclusive=True)

    def _test(self, name: str) -> None:
        result = ai_setup.probe_provider(name)
        self.app.call_from_thread(self._finish_test, list(result.lines))

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

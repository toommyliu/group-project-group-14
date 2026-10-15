"""Guided setup for an AI provider: pick a service, paste its key, test it.

For someone who has never seen a provider file and should not have to. Three
steps in one dialog:

1. **Choose** the service from a list. The provider file is written from the
   shipped example — nothing to fill in.
2. **Key**: where to get one, a box to paste it into, and a test. The key
   goes to the operating system's password store (`core.app_config.keystore`),
   never into an Opengrader file.
3. **Done**: the result, and — when the service lists its models — a
   dropdown for each instead of a model id to type.

A service that is not in the list takes one more step, between 1 and 2:
**Other** hands the user a prompt for whatever AI assistant they already use,
which interviews them and writes the provider file, and a box to paste the
assistant's reply into. The reply is validated and written to the providers
folder for them.

What the steps say comes from `core.app_config.setup`; this module lays it
out. Choosing the provider for real grading is still AI setup's job, because
that is where the consent to send student work is asked for.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    RadioButton,
    RadioSet,
    Select,
    Static,
    TextArea,
)

from opengrader.core import app_config
from opengrader.core.app_config import keystore
from opengrader.core.app_config import manifests as ai_manifests
from opengrader.core.app_config import setup as ai_setup
from opengrader.tui.clipboard import copy_text
from opengrader.tui.ui import button_roles
from opengrader.tui.ui.button_roles import OutlineButtons

STEP_CHOOSE = "setup-step-choose"
STEP_KEY = "setup-step-key"
STEP_OTHER = "setup-step-other"
STEP_DONE = "setup-step-done"

# Where the prompt is also written when the clipboard could not be confirmed.
PROMPT_FILENAME = "provider-prompt.txt"


class ProviderSetupScreen(OutlineButtons, ModalScreen[str | None]):
    """Dismisses with the name of the provider that was added or changed, else None."""

    DEFAULT_CSS = """
    ProviderSetupScreen {
        align: center middle;
    }

    #setup-dialog {
        width: 84%;
        max-width: 110;
        height: 88%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #setup-progress {
        color: $text-muted;
        height: auto;
    }

    #setup-steps {
        height: 1fr;
        margin-top: 1;
    }

    /* Direct children only: a RadioSet is itself a VerticalScroll and a
       Select is built from Statics, and neither may inherit these. */
    #setup-steps > VerticalScroll {
        height: 100%;
    }

    #setup-steps > VerticalScroll > Static, #setup-models > Static {
        width: 100%;
        height: auto;
    }

    #setup-models {
        height: auto;
    }

    #setup-choices {
        width: 100%;
        height: auto;
        margin-top: 1;
    }

    #setup-choice-detail, #setup-key-store-note, #setup-models-note, #setup-next-step,
    #setup-prompt-note {
        margin-top: 1;
        color: $text-muted;
    }

    #setup-other-buttons {
        height: auto;
        margin-top: 1;
    }

    #setup-other-buttons Button {
        margin-right: 1;
    }

    #setup-reply {
        height: 12;
        margin-top: 1;
    }

    #setup-key-input, #setup-open-page {
        margin-top: 1;
    }

    .setup-model-label {
        margin-top: 1;
    }

    #setup-status {
        height: auto;
        max-height: 8;
        margin-top: 1;
    }

    #setup-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #setup-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, *, preselect: str = "") -> None:
        super().__init__()
        self._choices = ai_setup.setup_choices()
        self._preselect = preselect
        self._step = STEP_CHOOSE
        # The provider this run is setting up, once step 1 has settled it.
        self._name = ""
        # Set as soon as anything on disk or in the key store changed, so the
        # providers screen refreshes even if the user then cancels.
        self._changed = ""
        self._busy = False
        self._listed_models: tuple[str, ...] = ()
        # The steps this run goes through; settled when step 1 is answered.
        self._path: tuple[str, ...] = (STEP_CHOOSE, STEP_KEY, STEP_DONE)
        # The name a pasted file would overwrite, once the user has been told.
        self._replace_armed = ""
        self._last_error = ""

    # ------------------------------------------------------------------ layout

    def compose(self) -> ComposeResult:
        with Vertical(id="setup-dialog") as dialog:
            dialog.border_title = "▸ Set up an AI provider"
            yield Static("", id="setup-progress")
            with ContentSwitcher(initial=STEP_CHOOSE, id="setup-steps"):
                with VerticalScroll(id=STEP_CHOOSE):
                    yield Static(
                        "Which service do you have an account with? Opengrader "
                        "fills in everything it needs to know about the one "
                        "you pick."
                    )
                    with RadioSet(id="setup-choices"):
                        for index, choice in enumerate(self._choices):
                            yield RadioButton(
                                choice.title,
                                value=self._starts_selected(index),
                                id=f"setup-choice-{index}",
                            )
                        yield RadioButton(
                            "My service is not in this list",
                            value=not self._choices,
                            id="setup-choice-other",
                        )
                    yield Static("", id="setup-choice-detail")
                with VerticalScroll(id=STEP_OTHER):
                    yield Static(
                        "An AI assistant you already use (ChatGPT, Claude, "
                        "Gemini, Copilot…) can write the settings for your "
                        "service:\n"
                        "1. Press Copy the prompt, and paste it into the assistant.\n"
                        "2. Answer its questions. If you were sent setup "
                        "instructions for your service, paste those in too.\n"
                        "3. Copy the file it gives you and paste it in the box below.\n"
                        "\n"
                        "Never give the assistant your key or password. "
                        "Opengrader asks for the key itself, on the next step."
                    )
                    with Horizontal(id="setup-other-buttons"):
                        yield Button("Copy the prompt", id="setup-copy-prompt", compact=True)
                        yield Button("Copy the error", id="setup-copy-error", compact=True)
                    yield Static("", id="setup-prompt-note")
                    yield TextArea(id="setup-reply", show_line_numbers=False)
                with VerticalScroll(id=STEP_KEY):
                    yield Static("", id="setup-key-intro")
                    yield Button("Open the key page", id="setup-open-page", classes=button_roles.OPEN,  compact=True)
                    yield Input(
                        placeholder="Paste the key here",
                        password=True,
                        id="setup-key-input",
                    )
                    yield Static("", id="setup-key-store-note")
                with VerticalScroll(id=STEP_DONE):
                    yield Static("", id="setup-result")
                    with Vertical(id="setup-models"):
                        yield Static(
                            "Model that reads handwriting", classes="setup-model-label"
                        )
                        yield Select([], allow_blank=True, id="setup-vision-model")
                        yield Static(
                            "Model that suggests grades", classes="setup-model-label"
                        )
                        yield Select([], allow_blank=True, id="setup-text-model")
                    yield Static("", id="setup-models-note")
                    yield Static("", id="setup-next-step")
            yield Static("", id="setup-status")
            with Horizontal(id="setup-buttons"):
                yield Button("Back", id="setup-back", compact=True)
                yield Button("Cancel", id="setup-cancel", compact=True)
                yield Button("Continue", id="setup-primary", variant="primary", compact=True)

    def _starts_selected(self, index: int) -> bool:
        if self._preselect:
            return self._choices[index].name == self._preselect
        return index == 0

    def on_mount(self) -> None:
        self.query_one("#setup-models", Vertical).display = False
        self.query_one("#setup-copy-error", Button).display = False
        if not app_config.cloud_allowed():
            self._say(
                f"{app_config.ALLOW_CLOUD_ENV}=0 is set on this computer, so "
                "services that are not on this computer are turned off. Ask "
                "whoever manages it."
            )
        self._show_step(STEP_CHOOSE)
        self._show_choice_detail()

    # ----------------------------------------------------------------- helpers

    def _say(self, *lines: str) -> None:
        self.query_one("#setup-status", Static).update("\n".join(lines))

    def _selected_choice(self) -> ai_setup.SetupChoice | None:
        pressed = self.query_one("#setup-choices", RadioSet).pressed_index
        if 0 <= pressed < len(self._choices):
            return self._choices[pressed]
        return None

    def _other_selected(self) -> bool:
        return self.query_one("#setup-choice-other", RadioButton).value

    def _spec(self) -> app_config.ProviderSpec | None:
        for spec in app_config.registered_providers():
            if spec.name == self._name and spec.source == "user":
                return spec
        return None

    def _show_step(self, step: str) -> None:
        self._step = step
        self.query_one("#setup-steps", ContentSwitcher).current = step
        number = self._path.index(step) + 1 if step in self._path else 1
        title = {
            STEP_CHOOSE: "choose the service",
            STEP_OTHER: "describe your service",
            STEP_KEY: "give it your key",
            STEP_DONE: "finished",
        }[step]
        self.query_one("#setup-progress", Static).update(
            f"Step {number} of {len(self._path)} — {title}"
        )
        primary = self.query_one("#setup-primary", Button)
        primary.label = {
            STEP_CHOOSE: "Continue",
            STEP_OTHER: "Add this provider",
            STEP_KEY: "Save key and test",
            STEP_DONE: "Finish",
        }[step]
        self.query_one("#setup-back", Button).display = step in (STEP_KEY, STEP_OTHER)
        self.query_one("#setup-cancel", Button).display = step != STEP_DONE
        self._set_busy(False)

    def _set_busy(self, busy: bool) -> None:
        self._busy = busy
        blocked = busy or (
            self._step == STEP_CHOOSE and not app_config.cloud_allowed()
        )
        self.query_one("#setup-primary", Button).disabled = blocked
        self.query_one("#setup-back", Button).disabled = busy

    # ------------------------------------------------------------ step 1: choose

    def _show_choice_detail(self) -> None:
        choice = self._selected_choice()
        detail = self.query_one("#setup-choice-detail", Static)
        if choice is None:
            detail.update(
                "For a service your school or department runs, or any other "
                "one. An AI assistant you already use asks you a few "
                "questions and writes the settings; you paste its answer here."
                if self._other_selected()
                else ""
            )
            return
        lines = [choice.summary] if choice.summary else []
        if choice.unavailable:
            lines.append(choice.unavailable)
        elif choice.advanced:
            lines.append(
                "Advanced: this one signs in with a cloud account instead of "
                "a key you paste, so part of its setup happens outside "
                "Opengrader."
            )
        if choice.installed:
            lines.append(
                "Already added on this computer — continue to set or replace its key."
                if not choice.advanced
                else "Already added on this computer."
            )
        detail.update("\n".join(lines))

    @on(RadioSet.Changed, "#setup-choices")
    def _on_choice_changed(self, event: RadioSet.Changed) -> None:
        event.stop()
        self._show_choice_detail()

    def _choose(self) -> None:
        if self._other_selected():
            self._path = (STEP_CHOOSE, STEP_OTHER, STEP_KEY, STEP_DONE)
            self._name = ""
            self._say("")
            self._show_step(STEP_OTHER)
            return
        choice = self._selected_choice()
        if choice is None:
            self._say("Pick a service first.")
            return
        self._path = (
            (STEP_CHOOSE, STEP_DONE) if choice.advanced else (STEP_CHOOSE, STEP_KEY, STEP_DONE)
        )
        if choice.unavailable:
            self._say(choice.unavailable)
            return
        self._name = choice.name
        # Asked of the registry now, not of `choice.installed`: after Back,
        # the service this screen added a moment ago is on disk already.
        if self._spec() is None:
            try:
                ai_manifests.install_manifest(ai_manifests.example_text(choice.name))
            except ai_manifests.ManifestExistsError as exc:
                # A file with this name is there but was not loaded: it is one
                # of the "skipped provider files", and overwriting someone's
                # file to get past that is not this screen's call.
                self._say(
                    f"{exc} Opengrader could not read that file, so it was "
                    "left alone. Close this and look under 'Skipped provider "
                    "files', or use Remove, then try again."
                )
                return
            except ai_manifests.ManifestError as exc:
                self._say(str(exc))
                return
            app_config.reload_manifests()
            self._changed = choice.name
        spec = self._spec()
        if spec is None:
            problems = app_config.manifest_problems()
            self._say(
                f"{choice.label} was added but could not be loaded.",
                *problems[:2],
            )
            return
        self._say("")
        if spec.api_key_env:
            self._enter_key_step(spec, choice)
        else:
            self._enter_done_step(spec, tested=False)

    # ------------------------------------------- step 2 (unlisted service): other

    @on(Button.Pressed, "#setup-copy-prompt")
    def _on_copy_prompt(self, event: Button.Pressed) -> None:
        event.stop()
        prompt = ai_setup.assistant_prompt()
        note = self.query_one("#setup-prompt-note", Static)
        if copy_text(self.app, prompt):
            note.update("Copied. Paste it into your AI assistant.")
            return
        # Nothing confirmed the copy (some terminals ignore the request), so
        # the prompt is also put somewhere the user can open and copy from.
        path = app_config.settings_path().parent / PROMPT_FILENAME
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(prompt, encoding="utf-8")
        except OSError as exc:
            note.update(
                "The prompt was sent to the clipboard, but this terminal may "
                f"not have accepted it, and it could not be saved either: {exc}"
            )
            return
        note.update(
            "The prompt was sent to the clipboard. If pasting gives you "
            f"something else, open this file and copy its contents instead:\n{path}"
        )

    @on(Button.Pressed, "#setup-copy-error")
    def _on_copy_error(self, event: Button.Pressed) -> None:
        event.stop()
        if not self._last_error:
            return
        copy_text(
            self.app,
            "Opengrader did not accept that file. It said:\n\n"
            f"{self._last_error}\n\n"
            "Please correct the file and give me the whole file again in one "
            "code block.",
        )
        self.query_one("#setup-prompt-note", Static).update(
            "Copied. Paste it to the assistant and it will correct the file."
        )

    def _add_reply(self) -> None:
        reply = self.query_one("#setup-reply", TextArea).text
        try:
            # Read first without writing, to learn the name it would take.
            wanted = ai_manifests.parse_text(ai_setup.extract_manifest(reply)).name
            replace = self._replace_armed == wanted
            _path, spec = ai_setup.install_reply(reply, replace=replace)
        except ai_manifests.ManifestExistsError:
            self._replace_armed = wanted
            self._say(
                f"There is already a provider called {wanted!r} on this "
                "computer. Press Add this provider again to replace it with "
                "this file, or ask the assistant to use a different name."
            )
            return
        except ai_manifests.ManifestError as exc:
            self._replace_armed = ""
            self._last_error = str(exc)
            self.query_one("#setup-copy-error", Button).display = True
            self._say(
                "That file cannot be used yet:",
                str(exc),
                "Press Copy the error, paste it to the assistant, then paste "
                "its corrected file here.",
            )
            return
        self._replace_armed = ""
        self._last_error = ""
        self.query_one("#setup-copy-error", Button).display = False
        app_config.reload_manifests()
        # A replaced file may send somewhere new; what was resolved for the
        # old one (including "no key") must not be reused.
        app_config.clear_credential_cache()
        self._name = spec.name
        self._changed = spec.name
        loaded = self._spec()
        if loaded is None:
            self._say(
                f"{spec.label} was saved but could not be loaded.",
                *app_config.manifest_problems()[:2],
            )
            return
        self._say("")
        if loaded.api_key_env:
            self._enter_key_step(loaded, ai_setup.choice_for(loaded))
        else:
            self._path = (STEP_CHOOSE, STEP_OTHER, STEP_DONE)
            self._enter_done_step(loaded, tested=False)

    # --------------------------------------------------------------- step 2: key

    def _enter_key_step(
        self, spec: app_config.ProviderSpec, choice: ai_setup.SetupChoice
    ) -> None:
        store = keystore.status()
        source = app_config.api_key_source(spec)
        if choice.key_url:
            intro = [
                f"1. Open {choice.key_url} and sign in, or create an account.",
                "2. Create a new API key there and copy it.",
                "3. Paste it in the box below.",
                "",
                ai_setup.SUBSCRIPTION_NOTE,
            ]
        else:
            # No public key page is known — a department's own gateway, say —
            # so the file's own hint is the best instruction there is.
            intro = [
                spec.setup_hint
                or f"Get a key for {spec.label} from whoever runs the service.",
                "Then paste it in the box below.",
            ]
        intro += [
            "",
            f"Pressing the button sends one short test message, with no "
            f"student work in it, to {app_config.consent.hostname_of(spec.default_host)}.",
        ]
        self.query_one("#setup-key-intro", Static).update("\n".join(intro))
        self.query_one("#setup-open-page", Button).display = bool(choice.key_url)

        box = self.query_one("#setup-key-input", Input)
        box.value = ""
        box.disabled = not store.available
        if not store.available:
            note = (
                f"A pasted key cannot be kept safely on this computer: {store.reason} "
                f"You can still use {spec.label} by setting the {spec.api_key_env} "
                "environment variable before starting Opengrader."
            )
            if source == "environment":
                note += f" {spec.api_key_env} is set now, so you can test it."
        elif source == "environment":
            note = (
                f"A key is already set in the {spec.api_key_env} environment "
                "variable, and it is used first. Leave the box empty to test "
                f"that one, or paste a key to keep in {store.name} as well."
            )
        elif source == "keystore":
            note = (
                f"A key is already saved in {store.name}. Leave the box empty "
                "to test it, or paste a new key to replace it."
            )
        else:
            note = (
                f"The key is kept in {store.name} on this computer, under "
                "your own login — never in an Opengrader file."
            )
        self.query_one("#setup-key-store-note", Static).update(note)
        self._show_step(STEP_KEY)
        if store.available:
            box.focus()

    @on(Button.Pressed, "#setup-open-page")
    def _on_open_page(self, event: Button.Pressed) -> None:
        event.stop()
        choice = self._choice_for_name()
        if choice is None or not choice.key_url:
            return
        self.app.open_url(choice.key_url)
        self._say(
            f"Opening {choice.key_url} in your browser. If nothing opens, "
            "type that address into a browser yourself."
        )

    def _choice_for_name(self) -> ai_setup.SetupChoice | None:
        for choice in self._choices:
            if choice.name == self._name:
                return choice
        # A service added from a pasted file is not in the list built when
        # this screen opened.
        spec = self._spec()
        return ai_setup.choice_for(spec) if spec is not None else None

    @on(Input.Submitted, "#setup-key-input")
    def _on_key_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        if not self._busy:
            self._save_key_and_test()

    def _save_key_and_test(self) -> None:
        spec = self._spec()
        if spec is None:
            return
        box = self.query_one("#setup-key-input", Input)
        pasted = box.value.strip()
        if not pasted and not app_config.api_key_source(spec):
            self._say(
                "Paste the key into the box first."
                if not box.disabled
                else f"Set {spec.api_key_env} before starting Opengrader, then come back."
            )
            return
        # The box is emptied at once: the key should be on screen (as dots)
        # and in this widget for no longer than it takes to hand it over.
        box.value = ""
        self._set_busy(True)
        self._say("Saving the key…" if pasted else f"Testing {spec.label}…")
        self.run_worker(
            lambda: self._key_worker(spec, pasted), thread=True, exclusive=True
        )

    def _key_worker(self, spec: app_config.ProviderSpec, pasted: str) -> None:
        if pasted:
            try:
                keystore.store(
                    keystore.account_for(spec),
                    pasted,
                    label=f"Opengrader: {spec.label} API key",
                )
            except keystore.KeyStoreError as exc:
                self.app.call_from_thread(
                    self._key_failed, f"The key was not saved: {exc}"
                )
                return
            # Recorded here, not when the test comes back: the key is in the
            # store from this moment even if the dialog is closed mid-test.
            self._changed = spec.name
            # Anything resolved before this moment answered "no key".
            app_config.clear_credential_cache()
            self.app.call_from_thread(self._say, f"Key saved. Testing {spec.label}…")
        result = ai_setup.probe_provider(spec.name)
        self.app.call_from_thread(self._key_tested, result)

    def _key_failed(self, message: str) -> None:
        if not self.is_attached:
            return
        self._set_busy(False)
        self._say(message)

    def _key_tested(self, result: ai_setup.ProbeResult) -> None:
        if not self.is_attached:
            return
        self._set_busy(False)
        if not result.ok:
            self._say(*result.lines)
            return
        spec = self._spec()
        if spec is None:
            return
        self._say("")
        self._enter_done_step(spec, tested=True)

    # -------------------------------------------------------------- step 3: done

    def _enter_done_step(self, spec: app_config.ProviderSpec, *, tested: bool) -> None:
        result = self.query_one("#setup-result", Static)
        if tested:
            result.update(f"✓ {spec.label} is connected and answering.")
        else:
            lines = [f"{spec.label} was added."]
            if spec.setup_hint and spec.sends_data_off_machine:
                lines += ["", "To sign in:", spec.setup_hint]
            elif spec.setup_hint:
                lines += ["", spec.setup_hint]
            lines += [
                "",
                "When that is done, select it on the providers screen and press Test.",
            ]
            result.update("\n".join(lines))
        self.query_one("#setup-next-step", Static).update(
            f"Last step, when you want to use it: open AI setup and choose "
            f"{spec.label}. You are asked to confirm there before any student "
            "work is sent."
        )
        self._show_step(STEP_DONE)
        if tested and spec.supports_model_listing:
            self.query_one("#setup-models-note", Static).update(
                "Looking up the models this service offers…"
            )
            self.run_worker(
                lambda: self._models_worker(spec.name), thread=True, exclusive=True
            )
        else:
            self.query_one("#setup-models-note", Static).update(
                f"Models: {spec.default_vision_model} reads handwriting, "
                f"{spec.default_text_model} suggests grades."
            )

    def _models_worker(self, name: str) -> None:
        models, error = ai_setup.list_models(name)
        self.app.call_from_thread(self._show_models, name, models, error)

    def _show_models(self, name: str, models: tuple[str, ...], error: str) -> None:
        if not self.is_attached or name != self._name:
            return
        spec = self._spec()
        note = self.query_one("#setup-models-note", Static)
        if spec is None:
            return
        if not models:
            note.update(
                f"Models: {spec.default_vision_model} reads handwriting, "
                f"{spec.default_text_model} suggests grades."
                + (f" (The list of other models could not be read: {error})" if error else "")
            )
            return
        self._listed_models = models
        for select_id, current in (
            ("#setup-vision-model", spec.default_vision_model),
            ("#setup-text-model", spec.default_text_model),
        ):
            # The model the file already asks for is always offered, even if
            # the service left it out of its list, so opening this screen
            # never changes a working choice by itself.
            names = models if current in models else (current, *models)
            picker = self.query_one(select_id, Select)
            picker.set_options([(model, model) for model in names])
            picker.value = current
        self.query_one("#setup-models", Vertical).display = True
        note.update(
            "The suggested models are already chosen. Change one only if you "
            "know you want a different model — the handwriting model must be "
            "one that can read images."
        )

    def _finish(self) -> None:
        spec = self._spec()
        if spec is not None and self._listed_models:
            vision = self.query_one("#setup-vision-model", Select).value
            text = self.query_one("#setup-text-model", Select).value
            vision = vision if isinstance(vision, str) else spec.default_vision_model
            text = text if isinstance(text, str) else spec.default_text_model
            if (vision, text) != (spec.default_vision_model, spec.default_text_model):
                try:
                    ai_manifests.set_manifest_models(
                        spec, vision_model=vision, text_model=text
                    )
                except ai_manifests.ManifestError as exc:
                    self._say(f"The models were not changed: {exc}")
                    return
                app_config.reload_manifests()
                self._changed = spec.name
        self.dismiss(self._changed or None)

    # ----------------------------------------------------------------- buttons

    @on(Button.Pressed, "#setup-primary")
    def _on_primary(self, event: Button.Pressed) -> None:
        event.stop()
        if self._busy:
            return
        if self._step == STEP_CHOOSE:
            self._choose()
        elif self._step == STEP_OTHER:
            self._add_reply()
        elif self._step == STEP_KEY:
            self._save_key_and_test()
        else:
            self._finish()

    @on(Button.Pressed, "#setup-back")
    def _on_back(self, event: Button.Pressed) -> None:
        event.stop()
        if self._busy:
            return
        self.query_one("#setup-key-input", Input).value = ""
        self._say("")
        position = self._path.index(self._step) if self._step in self._path else 0
        self._show_step(self._path[max(position - 1, 0)])

    @on(Button.Pressed, "#setup-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    def action_close(self) -> None:
        self.dismiss(self._changed or None)

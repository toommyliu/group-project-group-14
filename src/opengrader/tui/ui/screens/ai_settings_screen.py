from __future__ import annotations

import sys
import time
from dataclasses import replace
from typing import Callable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

from opengrader.core import app_config
from opengrader.core.app_config import (
    AISettings,
    ResolvedAI,
    consent,
    keystore,
    local_ai,
)
from opengrader.core.homebrew import HOMEBREW_URL, find_homebrew
from opengrader.core.ocr.math_ocr import (
    DEFAULT_MATH_BACKEND,
    MATH_BACKEND_NAMES,
    describe_backend,
)
from opengrader.tui.ui.screens.cloud_consent_screen import (
    CloudConsentScreen,
    CredentialConsentScreen,
)
from opengrader.tui.ui.button_roles import OutlineButtons

_MATH_BACKEND_LABELS = {
    "vlm": "Vision model — answers that mix writing and maths",
    "pix2tex": "pix2tex — answers that are equations",
}
_MATH_BACKEND_CHOICES = [
    (_MATH_BACKEND_LABELS[name], name) for name in MATH_BACKEND_NAMES
]

# What the "Set up local AI" button says. It checks first and names what is
# missing; the two expensive steps then need a second, labelled press.
_PREPARE_LABEL = "Set up local AI"
_PREPARE_DOWNLOAD_LABEL = "Download models"
_PREPARE_INSTALL_LABEL = "Install and download"
_PREPARE_STOP_LABEL = "Stop"

# A download reports many times a second; the screen needs a few.
_PROGRESS_INTERVAL_SECONDS = 0.25


class AISettingsScreen(OutlineButtons, ModalScreen[AISettings | None]):
    DEFAULT_CSS = """
    AISettingsScreen {
        align: center middle;
    }

    #ai-dialog {
        width: 84%;
        max-width: 104;
        height: 86%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }
    #ai-form {
        height: 1fr;
        scrollbar-size: 1 1;
    }

    #ai-hint, .ai-note {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    .ai-label {
        width: 100%;
        height: auto;
        margin-top: 1;
        color: $text;
    }

    #ai-provider-summary {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    #ai-key-row {
        height: auto;
    }

    AISettingsScreen Checkbox {
        background: transparent;
        border: none;
        padding: 0;
        margin-top: 1;
    }

    AISettingsScreen Checkbox:focus {
        background: $boost;
    }

    #ai-log {
        height: 8;
        margin-top: 1;
        border: round $surface-lighten-2;
        border-title-align: left;
        border-title-color: $text-muted;
        background: transparent;
        padding: 0 1;
    }

    #ai-prepare-status {
        width: 100%;
        height: auto;
        color: $accent;
    }

    #ai-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #ai-buttons Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "close", "Close")]

    def __init__(self, settings: AISettings | None = None) -> None:
        super().__init__()
        self._settings = settings if settings is not None else app_config.load_settings()
        self._timeout_recommendation: float | None = None
        # escalate_only is None until the user explicitly flips the checkbox
        # in this session. While it stays None, the checkbox only *displays*
        # the provider's effective default (on for cloud, off for local) —
        # collect() keeps writing None so a later provider change keeps
        # following whichever default is current, instead of pinning
        # whatever happened to be on screen when Save was pressed.
        self._escalate_touched = False
        # Set while "Set up local AI" is working; setting it stops the work.
        self._prepare_cancel: local_ai.Cancel | None = None
        # The (server, models) an install or download was offered for. The
        # next press goes ahead only if the form still says the same.
        self._prepare_offer: tuple[str, tuple[str, ...]] | None = None


    @staticmethod
    def _label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="ai-label")

    @staticmethod
    def _hidden_unless(shown: bool) -> str:
        return "" if shown else "hidden"

    @staticmethod
    def _effective_escalate_only(settings: AISettings, current: ResolvedAI) -> bool:
        """What the checkbox should show: the stored choice, or the live default."""
        if settings.escalate_only is not None:
            return settings.escalate_only
        return current.kind == "cloud"

    def compose(self) -> ComposeResult:
        current = app_config.resolve(self._settings)
        provider = current.provider

        with Vertical(id="ai-dialog") as dialog:
            dialog.border_title = "▸ AI setup"
            yield Static(
                "Set up the AI once here and every part of Opengrader uses it — "
                "reading handwriting during Extract, suggesting grades for "
                "written answers, the one-shot pipeline, and the same commands "
                "run from a terminal. The AI is always optional: leave it "
                "unconfigured and everything still works without it.",
                id="ai-hint",
            )

            with VerticalScroll(id="ai-form"):
                # First, because it is the switch that makes the rest work
                # without anyone starting a server or fetching a model by hand.
                yield Checkbox(
                    "Start the AI automatically when needed",
                    value=self._settings.auto_start,
                    id="ai-auto-start",
                )
                yield Static(
                    "Starts the local AI server and downloads a missing model "
                    "instead of stopping a run to ask. Turn it off if you would "
                    "rather start it yourself. To do both now and watch the "
                    "download, press Set up local AI below — it fetches only "
                    "the models the switches here need.",
                    classes="ai-note",
                )

                yield self._label("What the AI does here")
                yield Static(
                    "These switches are what every tool reads — including "
                    "submission-extractor, automated-grading and "
                    "opengrader-run started from a plain terminal — so this "
                    "is where the AI is turned on, not a per-run question "
                    "any more.",
                    classes="ai-note",
                )

                yield Checkbox(
                    "Read handwriting on scans",
                    value=self._settings.read_handwriting,
                    id="ai-read-handwriting",
                )
                yield Static(
                    "Fills each answer's transcription from the answer "
                    "image, which is what Extract needs on a scanned exam. "
                    "It is also the last resort for page-number footers "
                    "during Split, and for reading a scanned answer key.",
                    classes="ai-note",
                )
                with Vertical(
                    id="ai-handwriting-options",
                    classes=self._hidden_unless(self._settings.read_handwriting),
                ):
                    yield Checkbox(
                        "Only re-read what the text recognition read badly",
                        value=self._effective_escalate_only(self._settings, current),
                        id="ai-escalate-only",
                    )
                    yield Static(
                        "The fast local OCR reads every answer first and a "
                        "model call is spent only where it read poorly or "
                        "not at all — much faster on an exam whose answers "
                        "are mostly short and legible, and it cannot lose "
                        "an answer, since a model that reads nothing leaves "
                        "the OCR text standing.",
                        classes="ai-note",
                    )
                    yield Checkbox(
                        "Transcribe handwritten maths as LaTeX",
                        value=self._settings.transcribe_math,
                        id="ai-transcribe-math",
                    )
                    yield Static(
                        "Fractions, exponents and integrals survive instead "
                        "of being flattened to plain text, and the grade "
                        "screen renders them. The \"Maths reader\" setting "
                        "below chooses which model does it.",
                        classes="ai-note",
                    )

                yield Checkbox(
                    "Suggest grades for written answers",
                    value=self._settings.grade_written,
                    id="ai-grade-written",
                )
                yield Static(
                    "The model only picks options from your rubric and "
                    "never invents a score; every suggestion is tagged for "
                    "you to confirm or change.",
                    classes="ai-note",
                )
                with Vertical(
                    id="ai-grading-options",
                    classes=self._hidden_unless(self._settings.grade_written),
                ):
                    yield Checkbox(
                        "Only use suggestions I have measured",
                        value=self._settings.require_calibration,
                        id="ai-require-calibration",
                    )
                    yield Static(
                        "With this on, grading refuses to write AI "
                        "suggestions until you have run \"Calibrate AI "
                        "grading\" in Helper Tools against work you already "
                        "marked and accepted its recommendation — so the "
                        "confidence below is a measurement rather than a "
                        "guess.",
                        classes="ai-note",
                    )

                yield Checkbox(
                    "Grade what it can as soon as extraction finishes",
                    value=self._settings.auto_grade,
                    id="ai-auto-grade",
                )
                yield Static(
                    "The one-shot pipeline scores every answer a rubric "
                    "can settle on its own the moment extraction is done, "
                    "leaving only the genuinely uncertain ones for you. "
                    "Turn it off to stop after extraction. On by default.",
                    classes="ai-note",
                )

                yield Checkbox(
                    "Let the local AI read student names and IDs (never "
                    "sent to a cloud service)",
                    value=self._settings.read_identity_locally,
                    id="ai-read-identity-locally",
                )
                yield Static(
                    "A local model reads the Name and Student ID boxes so "
                    "more of them come through automatically, even while a "
                    "cloud provider is the active AI — that reading always "
                    "stays on this computer. Turn it off and those boxes are "
                    "read by Tesseract's text recognition only; anything it "
                    "cannot read clearly is left blank for the review queue "
                    "instead of a guess.",
                    classes="ai-note",
                )

                yield self._label("Where the AI runs")
                yield Select(
                    [(spec.label, spec.name) for spec in app_config.registered_providers()],
                    value=provider.name,
                    allow_blank=False,
                    id="ai-provider",
                )
                yield Static(
                    self._provider_summary(current),
                    id="ai-provider-summary",
                )

                yield self._label(
                    "Handwriting model",
                    f"blank = recommended ({provider.default_vision_model})",
                )
                yield Input(
                    value=self._settings.vision_model,
                    placeholder=provider.default_vision_model,
                    id="ai-vision-model",
                )
                yield Static(
                    "Reads what a student wrote out of each answer image, so "
                    "the words end up in the submission alongside the picture. "
                    "This is the model Extract uses.",
                    classes="ai-note",
                )

                yield self._label(
                    "Grading model",
                    f"blank = recommended ({provider.default_text_model})",
                )
                yield Input(
                    value=self._settings.text_model,
                    placeholder=provider.default_text_model,
                    id="ai-text-model",
                )
                yield Static(
                    "Reads a written answer that has already been transcribed "
                    "and picks options from your rubric. It never invents a "
                    "score — the points come from the rubric you wrote — and "
                    "every suggestion is tagged for you to confirm or change.",
                    classes="ai-note",
                )

                yield self._label(
                    "Server address", f"blank = {provider.default_host}"
                )
                yield Input(
                    value=self._settings.host,
                    placeholder=provider.default_host,
                    id="ai-host",
                )

                yield self._label(
                    "How long to wait for the AI",
                    f"blank = {app_config.DEFAULT_TIMEOUT_SECONDS:g}s",
                )
                yield Input(
                    value=(
                        ""
                        if self._settings.timeout_seconds is None
                        else f"{self._settings.timeout_seconds:g}"
                    ),
                    placeholder=(
                        f"e.g. {app_config.DEFAULT_TIMEOUT_SECONDS:g} — raise "
                        "it if extraction on this computer times out"
                    ),
                    id="ai-timeout",
                )
                yield Static(
                    "This is how long one request may take before "
                    "Opengrader gives up on it. The symptom of it being too "
                    "low is answers coming back empty, not an error. Test "
                    "connection below can tell you whether it's enough, "
                    "rather than you having to guess.",
                    classes="ai-note",
                )

                with Vertical(id="ai-key-row", classes=self._key_row_classes(provider)):
                    yield self._label("API key", "read from the environment")
                    yield Static(
                        self._key_status(current),
                        id="ai-key-status",
                        classes="ai-note",
                    )

                yield self._label(
                    "Maths reader", "which model turns handwritten maths into LaTeX"
                )
                yield Select(
                    _MATH_BACKEND_CHOICES,
                    value=current.math_backend
                    if current.math_backend in MATH_BACKEND_NAMES
                    else DEFAULT_MATH_BACKEND,
                    allow_blank=False,
                    id="ai-math-backend",
                )
                yield Static(
                    describe_backend(current.math_backend).detail,
                    id="ai-math-hint",
                    classes="ai-note",
                )

                yield self._label(
                    "Minimum confidence for a grade suggestion",
                    f"0-1; blank = {app_config.DEFAULT_MIN_CONFIDENCE:g}",
                )
                yield Input(
                    value=(
                        ""
                        if self._settings.min_confidence is None
                        else f"{self._settings.min_confidence:g}"
                    ),
                    placeholder=f"e.g. {app_config.DEFAULT_MIN_CONFIDENCE:g}",
                    id="ai-min-confidence",
                )
                yield Static(
                    "Suggestions the model is less sure of than this are left "
                    "for you to grade by hand. Don't guess it — grade some "
                    "written answers yourself, then run \"Calibrate AI "
                    "grading\" in Helper Tools to measure how often this model "
                    "agrees with you and fill this in from the evidence.",
                    classes="ai-note",
                )

            log = RichLog(id="ai-log", wrap=True, auto_scroll=False, markup=False)
            log.border_title = "Connection test"
            yield log
            progress = Static("", id="ai-prepare-status", markup=False)
            progress.display = False
            yield progress

            with Horizontal(id="ai-buttons"):
                yield Button("Test connection", id="ai-test", compact=True)
                prepare = Button(_PREPARE_LABEL, id="ai-prepare", compact=True)
                # Starting a server and downloading models is something only
                # an AI on this computer needs.
                prepare.display = current.kind == "local"
                yield prepare
                yield Button(
                    "Use suggested limit",
                    id="ai-apply-timeout",
                    compact=True,
                    classes="hidden",
                    disabled=True,
                )
                yield Button("Save", variant="primary", id="ai-save", compact=True)
                yield Button("Cancel", id="ai-cancel", compact=True)

    @staticmethod
    def _key_row_classes(provider: app_config.ProviderSpec) -> str:
        return "" if provider.requires_api_key else "hidden"

    @staticmethod
    def _provider_summary(current: ResolvedAI) -> str:
        summary = current.provider.summary
        if current.sends_data_off_machine:
            summary = (
                f"{summary}  Answers and handwriting are sent to this service."
            )
        return f"[dim]{summary}[/dim]"

    @staticmethod
    def _key_status(current: ResolvedAI) -> str:
        env = current.provider.api_key_env
        if not env:
            return ""
        source = app_config.api_key_source(current.provider)
        if source == "keystore":
            return f"Saved in {keystore.status().name}."
        if current.api_key:
            return f"Found in {env}."
        return (
            "Not set. Paste one under AI providers → Set up a provider (it "
            "is kept in this computer's password store, never in an "
            f"Opengrader file), or put it in the {env} environment variable."
        )


    def collect(self) -> AISettings | None:
        raw_confidence = self.query_one("#ai-min-confidence", Input).value.strip()
        confidence: float | None = None
        if raw_confidence:
            try:
                confidence = float(raw_confidence)
            except ValueError:
                self.app.notify(
                    "Minimum confidence must be a number between 0 and 1.",
                    severity="error",
                )
                return None
            if not 0.0 <= confidence <= 1.0:
                self.app.notify(
                    "Minimum confidence must be between 0 and 1.", severity="error"
                )
                return None

        raw_timeout = self.query_one("#ai-timeout", Input).value.strip()
        timeout_seconds: float | None = None
        if raw_timeout:
            timeout_seconds = app_config.parse_timeout_seconds(raw_timeout)
            if timeout_seconds is None:
                self.app.notify(
                    "Timeout must be a number of seconds greater than 0.",
                    severity="error",
                )
                return None

        min_confidence_measured = (
            self._settings.min_confidence_measured
            if confidence == self._settings.min_confidence
            else False
        )

        # Only record an explicit True/False once the user has actually
        # touched the checkbox; otherwise keep None so resolve() keeps
        # choosing the default for whichever provider ends up saved.
        escalate_only = (
            self.query_one("#ai-escalate-only", Checkbox).value
            if self._escalate_touched
            else self._settings.escalate_only
        )

        math_backend = str(self.query_one("#ai-math-backend", Select).value)
        return AISettings(
            provider=str(self.query_one("#ai-provider", Select).value),
            host=self.query_one("#ai-host", Input).value.strip(),
            vision_model=self.query_one("#ai-vision-model", Input).value.strip(),
            text_model=self.query_one("#ai-text-model", Input).value.strip(),
            math_backend="" if math_backend == DEFAULT_MATH_BACKEND else math_backend,
            timeout_seconds=timeout_seconds,
            min_confidence=confidence,
            auto_start=self.query_one("#ai-auto-start", Checkbox).value,
            read_handwriting=self.query_one("#ai-read-handwriting", Checkbox).value,
            escalate_only=escalate_only,
            transcribe_math=self.query_one("#ai-transcribe-math", Checkbox).value,
            grade_written=self.query_one("#ai-grade-written", Checkbox).value,
            auto_grade=self.query_one("#ai-auto-grade", Checkbox).value,
            require_calibration=self.query_one(
                "#ai-require-calibration", Checkbox
            ).value,
            min_confidence_measured=min_confidence_measured,
            read_identity_locally=self.query_one(
                "#ai-read-identity-locally", Checkbox
            ).value,
            # Carry over any cloud/credential consent already granted in this
            # session (the provider picker below can grant one immediately,
            # before Save is pressed) — otherwise this fresh AISettings would
            # silently drop it.
            consents=self._settings.consents,
        )


    @on(Checkbox.Changed, "#ai-read-handwriting")
    def _on_read_handwriting_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#ai-handwriting-options").set_class(
            not event.value, "hidden"
        )

    @on(Checkbox.Changed, "#ai-grade-written")
    def _on_grade_written_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self.query_one("#ai-grading-options").set_class(not event.value, "hidden")

    @on(Checkbox.Changed, "#ai-escalate-only")
    def _on_escalate_only_changed(self, event: Checkbox.Changed) -> None:
        event.stop()
        self._escalate_touched = True

    def _apply_provider_preview(
        self, provider: app_config.ProviderSpec, preview: ResolvedAI
    ) -> None:
        """Refresh every widget that previews the chosen provider — never a commitment."""
        self.query_one("#ai-vision-model", Input).placeholder = (
            provider.default_vision_model
        )
        self.query_one("#ai-text-model", Input).placeholder = provider.default_text_model
        self.query_one("#ai-host", Input).placeholder = provider.default_host
        self.query_one("#ai-provider-summary", Static).update(
            self._provider_summary(preview)
        )
        self.query_one("#ai-key-row").set_class(
            not provider.requires_api_key, "hidden"
        )
        self.query_one("#ai-key-status", Static).update(self._key_status(preview))
        if self._prepare_cancel is None:
            self.query_one("#ai-prepare", Button).display = preview.kind == "local"
        if self._settings.escalate_only is None:
            # Still following the default — let it track the newly chosen
            # provider's kind. This is a display refresh only: prevent()
            # keeps it from being mistaken for the user touching the box.
            checkbox = self.query_one("#ai-escalate-only", Checkbox)
            with checkbox.prevent(Checkbox.Changed):
                checkbox.value = preview.kind == "cloud"

    @on(Select.Changed, "#ai-provider")
    def _on_provider_changed(self, event: Select.Changed) -> None:
        event.stop()
        previous_provider = self._settings.provider
        chosen = str(event.value)
        if chosen == previous_provider:
            # The initial value the Select mounts with (compose() already
            # rendered the matching preview), or reselecting what is already
            # active — either way, nothing changed and there is nothing to
            # gate: an already-saved cloud provider still gets consent
            # enforced at Save, without a modal appearing just from opening
            # AI setup to look at something else.
            return
        provider = app_config.get_provider(chosen)
        preview = app_config.resolve(replace(self._settings, provider=provider.name))
        self._apply_provider_preview(provider, preview)

        if preview.cloud_consent_required:
            # Showing the modal needs a worker (push_screen_wait only works
            # from inside one); the preview above already happened, so the
            # dropdown and its summary are honest even while this is pending.
            self.run_worker(
                self._gate_cloud_consent(preview, previous_provider),
                exclusive=True,
                group="ai-provider-consent",
            )

    async def _gate_cloud_consent(
        self, preview: ResolvedAI, previous_provider: str
    ) -> None:
        """Choosing a cloud provider must pass CloudConsentScreen before it sticks.

        Accepting records the consent immediately (so it survives even if the
        dialog is later cancelled without pressing Save); declining reverts
        the dropdown to the provider that was active before this choice.
        """
        accepted = await self.app.push_screen_wait(CloudConsentScreen(preview))
        if accepted:
            updated = consent.grant_cloud_consent(self._settings, preview)
            try:
                app_config.save_settings(updated)
            except OSError as exc:
                self.app.notify(f"Could not save cloud consent: {exc}", severity="error")
                accepted = False
            else:
                self._settings = updated
        if not accepted:
            select = self.query_one("#ai-provider", Select)
            # prevent(): reverting the value should not re-run this same
            # gate (previous_provider was, by definition, already accepted).
            # Restore the preview by hand instead, since nothing will fire to
            # do it automatically.
            with select.prevent(Select.Changed):
                select.value = previous_provider
            provider = app_config.get_provider(previous_provider)
            restored = app_config.resolve(
                replace(self._settings, provider=previous_provider)
            )
            self._apply_provider_preview(provider, restored)

    @on(Select.Changed, "#ai-math-backend")
    def _on_math_backend_changed(self, event: Select.Changed) -> None:
        event.stop()
        self.query_one("#ai-math-hint", Static).update(
            describe_backend(str(event.value)).detail
        )


    @on(Button.Pressed, "#ai-test")
    def _on_test(self, event: Button.Pressed) -> None:
        event.stop()
        pending = self.collect()
        if pending is None:
            return
        resolved = app_config.resolve(pending)
        if resolved.credential_consent_required:
            # A worker, because approving needs a modal and this button
            # handler is not one itself.
            self.run_worker(
                self._gate_credential_consent(pending, resolved),
                exclusive=True,
                group="ai-test-flow",
            )
            return
        self._start_test(resolved)

    async def _gate_credential_consent(
        self, pending: AISettings, resolved: ResolvedAI
    ) -> None:
        accepted = await self.app.push_screen_wait(
            CredentialConsentScreen(resolved.provider)
        )
        if not accepted:
            self.app.notify(
                "Test cancelled — the credential command was not approved.",
                severity="warning",
            )
            return
        pending = consent.grant_credential_consent(pending, resolved.provider)
        try:
            app_config.save_settings(pending)
        except OSError as exc:
            self.app.notify(f"Could not save credential consent: {exc}", severity="error")
            return
        self._settings = pending
        self._start_test(app_config.resolve(pending))

    def _start_test(self, resolved: ResolvedAI) -> None:
        for button in self.query(Button):
            button.disabled = True
        log = self.query_one("#ai-log", RichLog)
        log.border_title = "Connection test"
        log.auto_scroll = False
        log.clear()
        log.write("Testing…")
        self.query_one("#ai-prepare-status", Static).display = False
        self.run_worker(lambda: self._test(resolved), thread=True, exclusive=True)

    def _test(self, current: ResolvedAI) -> None:
        lines: list[str] = list(current.problems())
        try:
            available = app_config.available_models(current)
        except Exception as exc:  # noqa: BLE001
            lines.append(f"Could not reach {current.host}: {exc}")
            lines.append(current.provider.setup_hint)
            self.app.call_from_thread(self._show, lines, None)
            return

        if not current.provider.supports_model_listing:
            lines.append(
                f"{current.provider.label} does not list its models, so the "
                "timing check below is what proves the connection."
            )
        elif available:
            lines.append(f"Connected to {current.host}.")
            lines.append(f"Models installed: {', '.join(available)}")
        else:
            lines.append(f"Connected to {current.host}.")
            lines.append("No models installed yet.")
        for role, name in (
            ("Handwriting", current.vision_model),
            ("Grading", current.text_model),
        ):
            if available and name not in available:
                if current.provider.kind == "local":
                    lines.append(
                        f"{role} model {name!r} is not installed. "
                        f"Install it with: ollama pull {name}"
                    )
                else:
                    lines.append(
                        f"{role} model {name!r} was not offered by this service."
                    )
            else:
                lines.append(f"{role} model: {name} — ready.")

        recommendation: float | None = None
        if available and current.vision_model not in available:
            lines.append(
                "Skipping the timing check: the handwriting model isn't "
                "installed here, so a call to it would just fail slowly."
            )
        else:
            measurement = app_config.measure_generate_latency(
                current, role="handwriting"
            )
            lines.append(measurement.describe())
            if measurement.ok:
                recommendation = app_config.recommend_timeout(
                    measurement.seconds, current.timeout_seconds
                )
                if recommendation is not None:
                    lines.append(
                        "That is most of your limit. A limit of "
                        f"{recommendation:g} s would leave room for a "
                        "longer answer."
                    )
        self.app.call_from_thread(self._show, lines, recommendation)

    def _show(self, lines: list[str], recommendation: float | None) -> None:
        if not self.is_attached:
            return
        self._timeout_recommendation = recommendation
        for button in self.query(Button):
            button.disabled = False
        apply_button = self.query_one("#ai-apply-timeout", Button)
        apply_button.set_class(recommendation is None, "hidden")
        apply_button.disabled = recommendation is None
        log = self.query_one("#ai-log", RichLog)
        log.clear()
        for line in lines:
            log.write(line)

    @on(Button.Pressed, "#ai-apply-timeout")
    def _on_apply_timeout(self, event: Button.Pressed) -> None:
        event.stop()
        if self._timeout_recommendation is None:
            return
        value = f"{self._timeout_recommendation:g}"
        self.query_one("#ai-timeout", Input).value = value
        self.app.notify(f"Filled in {value} s — press Save to keep it.")

    # -- Set up local AI ----------------------------------------------------

    @on(Button.Pressed, "#ai-prepare")
    def _on_prepare(self, event: Button.Pressed) -> None:
        event.stop()
        if self._prepare_cancel is not None:
            # The same button is Stop while the work runs.
            event.button.disabled = True
            self._prepare_cancel.set()
            return
        pending = self.collect()
        if pending is None:
            return
        resolved = app_config.resolve(pending)
        if resolved.kind != "local":
            self.app.notify(
                f"{resolved.provider.label} at {resolved.host} is not on this "
                "computer, so there is nothing here to start or download.",
                severity="warning",
            )
            return
        models, skipped = self._models_in_use(resolved)
        log = self.query_one("#ai-log", RichLog)
        log.border_title = "Local AI setup"
        log.auto_scroll = True
        log.clear()
        self.query_one("#ai-prepare-status", Static).display = False
        if not models:
            # Nothing ticked needs a model, so there is nothing to start or
            # fetch — and saying so beats downloading on a guess.
            self._prepare_offer = None
            event.button.label = _PREPARE_LABEL
            log.write(
                "No switch that uses a local model is ticked, so there is "
                "nothing to download."
            )
            for line in skipped:
                log.write(line)
            return
        offer = (resolved.host, models)
        go_ahead = self._prepare_offer == offer
        self._prepare_offer = None

        cancel = local_ai.Cancel()
        self._prepare_cancel = cancel
        for button in self.query(Button):
            button.disabled = button.id != "ai-prepare"
        event.button.label = _PREPARE_STOP_LABEL
        for line in skipped:
            log.write(line)
        log.write("Checking the AI on this computer…")
        self.run_worker(
            lambda: self._prepare(resolved, models, go_ahead, cancel),
            thread=True,
            exclusive=True,
            group="ai-prepare",
        )

    def _prepare(
        self,
        resolved: ResolvedAI,
        models: tuple[str, ...],
        go_ahead: bool,
        cancel: local_ai.Cancel,
    ) -> None:
        last_shown = 0.0

        def on_progress(progress: local_ai.PullProgress) -> None:
            nonlocal last_shown
            now = time.monotonic()
            if now - last_shown < _PROGRESS_INTERVAL_SECONDS:
                return
            last_shown = now
            self._from_worker(self._show_prepare_progress, progress)

        def emit(line: str) -> None:
            self._from_worker(self._prepare_line, line)

        try:
            status = local_ai.prepare(
                resolved.host,
                models,
                emit=emit,
                on_progress=on_progress,
                cancel=cancel,
                install=go_ahead,
                download=go_ahead,
            )
        except local_ai.OllamaNotInstalled:
            self._from_worker(self._prepare_not_installed, resolved, models)
        except local_ai.LocalAICancelled:
            self._from_worker(self._prepare_stopped)
        except Exception as exc:  # noqa: BLE001 - whatever went wrong belongs on screen
            self._from_worker(self._prepare_failed, str(exc))
        else:
            self._from_worker(self._prepare_done, resolved, status)

    @staticmethod
    def _models_in_use(resolved: ResolvedAI) -> tuple[tuple[str, ...], list[str]]:
        """The models the ticked switches need, and a line for each one left out.

        A model is several gigabytes, so one is only fetched for a job that
        is switched on: the handwriting model for reading scans or names, the
        grading model for suggesting grades. Works from the form as it
        stands, ticked-but-unsaved included.
        """
        wanted: list[str] = []
        left_out: list[tuple[str, str]] = []
        if resolved.read_handwriting or resolved.read_identity_locally:
            wanted.append(resolved.vision_model)
        else:
            left_out.append(
                (
                    resolved.vision_model,
                    f"Leaving out the handwriting model ({resolved.vision_model}): "
                    "'Read handwriting on scans' and 'Let the local AI read "
                    "student names and IDs' are both off.",
                )
            )
        if resolved.grade_written:
            wanted.append(resolved.text_model)
        else:
            left_out.append(
                (
                    resolved.text_model,
                    f"Leaving out the grading model ({resolved.text_model}): "
                    "'Suggest grades for written answers' is off. Tick it and "
                    "press again if you want it — drafting a rubric with AI "
                    "and Calibrate AI grading use it too.",
                )
            )
        models = local_ai.distinct_models(wanted)
        # One model named for both jobs is fetched if either job is on, so
        # it is not also reported as left out.
        skipped = [
            line
            for model, line in left_out
            if not any(local_ai.same_model(model, kept) for kept in models)
        ]
        return models, skipped

    def _from_worker(self, callback: Callable[..., None], *args: object) -> None:
        # Closed mid-download: the screen is gone and nobody is left to tell.
        if self.is_attached:
            self.app.call_from_thread(callback, *args)

    def _prepare_line(self, line: str) -> None:
        if self.is_attached:
            self.query_one("#ai-prepare-status", Static).display = False
            self.query_one("#ai-log", RichLog).write(line)

    def _show_prepare_progress(self, progress: local_ai.PullProgress) -> None:
        if not self.is_attached or self._prepare_cancel is None:
            return
        status = self.query_one("#ai-prepare-status", Static)
        status.update(f"{progress.model} — {progress.describe()}")
        status.display = True

    @staticmethod
    def _with_roles(resolved: ResolvedAI, models: tuple[str, ...]) -> str:
        """Model names with what each is for: "a (handwriting), b (grading)"."""
        described = []
        for model in models:
            roles = [
                role
                for role, name in (
                    ("handwriting", resolved.vision_model),
                    ("grading", resolved.text_model),
                )
                if local_ai.same_model(name, model)
            ]
            described.append(f"{model} ({' and '.join(roles)})" if roles else model)
        return ", ".join(described)

    def _end_prepare(self, label: str = _PREPARE_LABEL) -> RichLog | None:
        """Put the buttons back; returns the log, or None when the screen has gone."""
        self._prepare_cancel = None
        if not self.is_attached:
            return None
        for button in self.query(Button):
            button.disabled = False
        self.query_one("#ai-apply-timeout", Button).disabled = (
            self._timeout_recommendation is None
        )
        self.query_one("#ai-prepare", Button).label = label
        self.query_one("#ai-prepare-status", Static).display = False
        return self.query_one("#ai-log", RichLog)

    def _prepare_done(self, resolved: ResolvedAI, status: local_ai.LocalAIStatus) -> None:
        if not status.missing:
            log = self._end_prepare()
            if log is None:
                return
            log.write("")
            log.write(
                "The local AI is ready: the server is answering and "
                f"{self._with_roles(resolved, status.wanted)} "
                f"{'is' if len(status.wanted) == 1 else 'are'} installed."
            )
            log.write("Test connection shows how quickly it answers on this computer.")
            self.app.notify("The local AI is ready.")
            return
        log = self._end_prepare(_PREPARE_DOWNLOAD_LABEL)
        if log is None:
            return
        self._prepare_offer = (status.host, status.wanted)
        log.write("")
        log.write(f"Not downloaded yet: {self._with_roles(resolved, status.missing)}.")
        log.write(
            f"Press {_PREPARE_DOWNLOAD_LABEL} to fetch "
            f"{'it' if len(status.missing) == 1 else 'them'}. A model is "
            "several GB and comes over the internet; the exact size is shown "
            "as soon as the download starts. It can be stopped and carried on "
            "later."
        )

    def _prepare_not_installed(
        self, resolved: ResolvedAI, models: tuple[str, ...]
    ) -> None:
        brew = find_homebrew()
        log = self._end_prepare(_PREPARE_INSTALL_LABEL if brew else _PREPARE_LABEL)
        if log is None:
            return
        log.write("")
        log.write(
            "Ollama, the program that runs the AI on this computer, is not "
            "installed."
        )
        if brew:
            self._prepare_offer = (resolved.host, models)
            log.write(
                "Homebrew is installed, so Opengrader can do this for you: "
                f"press {_PREPARE_INSTALL_LABEL}. It installs Ollama, starts "
                f"it and downloads {self._with_roles(resolved, models)} — "
                f"several GB{' each' if len(models) > 1 else ''}, over the "
                "internet."
            )
            return
        log.write(
            f"Install it from {local_ai.OLLAMA_DOWNLOAD_URL}, then press "
            f"{_PREPARE_LABEL} again."
        )
        if sys.platform == "darwin":
            log.write(
                f"With Homebrew ({HOMEBREW_URL}) on this Mac, Opengrader can "
                "install Ollama for you instead."
            )

    def _prepare_stopped(self) -> None:
        log = self._end_prepare()
        if log is None:
            return
        log.write("")
        log.write(
            f"Stopped. What was downloaded so far is kept: press "
            f"{_PREPARE_LABEL} to carry on from there."
        )

    def _prepare_failed(self, message: str) -> None:
        log = self._end_prepare()
        if log is None:
            return
        log.write("")
        log.write(message)
        self.app.notify("The local AI is not ready — see the log.", severity="error")

    def on_unmount(self) -> None:
        # A worker thread cannot be cancelled; ending what it waits on (the
        # download's connection, a Homebrew install) is what lets it, and the
        # app, finish.
        if self._prepare_cancel is not None:
            self._prepare_cancel.set()

    @on(Button.Pressed, "#ai-save")
    def _on_save(self, event: Button.Pressed) -> None:
        event.stop()
        pending = self.collect()
        if pending is None:
            return
        resolved = app_config.resolve(pending)
        if resolved.cloud_consent_required:
            # Belt and braces: the provider picker above already gates this
            # for a plain provider switch, so this only fires when something
            # else — the host, dialect, key variable — changed enough that
            # the consent on file no longer matches what is about to be
            # saved. Needs a worker to show the modal and wait for it.
            self.run_worker(
                self._save_after_cloud_consent(pending, resolved),
                exclusive=True,
                group="ai-save-flow",
            )
            return
        self._finish_save(pending)

    async def _save_after_cloud_consent(
        self, pending: AISettings, resolved: ResolvedAI
    ) -> None:
        accepted = await self.app.push_screen_wait(CloudConsentScreen(resolved))
        if not accepted:
            self.app.notify("Not saved — cloud consent was not given.", severity="warning")
            return
        pending = consent.grant_cloud_consent(pending, resolved)
        self._finish_save(pending)

    def _finish_save(self, pending: AISettings) -> None:
        try:
            destination = app_config.save_settings(pending)
        except OSError as exc:
            self.app.notify(f"Could not save AI settings: {exc}", severity="error")
            return
        self.app.notify(f"AI settings saved to {destination}.")
        self.dismiss(pending)

    @on(Button.Pressed, "#ai-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

from __future__ import annotations

from dataclasses import replace

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, RichLog, Select, Static

from opengrader.core import ai_config
from opengrader.core.ai_config import AISettings, ResolvedAI
from opengrader.core.ocr.math_ocr import (
    DEFAULT_MATH_BACKEND,
    MATH_BACKEND_NAMES,
    describe_backend,
)

_MATH_BACKEND_LABELS = {
    "vlm": "Vision model — answers that mix writing and maths",
    "pix2tex": "pix2tex — answers that are equations",
}
_MATH_BACKEND_CHOICES = [
    (_MATH_BACKEND_LABELS[name], name) for name in MATH_BACKEND_NAMES
]


class AISettingsScreen(ModalScreen[AISettings | None]):
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
        self._settings = settings if settings is not None else ai_config.load_settings()
        self._timeout_recommendation: float | None = None


    @staticmethod
    def _label(text: str, detail: str = "") -> Static:
        markup = f"[b]{text}[/b]"
        if detail:
            markup = f"{markup}  [dim]{detail}[/dim]"
        return Static(markup, classes="ai-label")

    @staticmethod
    def _hidden_unless(shown: bool) -> str:
        return "" if shown else "hidden"

    def compose(self) -> ComposeResult:
        current = ai_config.resolve(self._settings)
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
                        value=self._settings.escalate_only,
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

                yield self._label("Where the AI runs")
                yield Select(
                    [(spec.label, spec.name) for spec in ai_config.registered_providers()],
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
                    f"blank = {ai_config.DEFAULT_TIMEOUT_SECONDS:g}s",
                )
                yield Input(
                    value=(
                        ""
                        if self._settings.timeout_seconds is None
                        else f"{self._settings.timeout_seconds:g}"
                    ),
                    placeholder=(
                        f"e.g. {ai_config.DEFAULT_TIMEOUT_SECONDS:g} — raise "
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
                    f"0-1; blank = {ai_config.DEFAULT_MIN_CONFIDENCE:g}",
                )
                yield Input(
                    value=(
                        ""
                        if self._settings.min_confidence is None
                        else f"{self._settings.min_confidence:g}"
                    ),
                    placeholder=f"e.g. {ai_config.DEFAULT_MIN_CONFIDENCE:g}",
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

                yield Checkbox(
                    "Start the AI automatically when needed",
                    value=self._settings.auto_start,
                    id="ai-auto-start",
                )
                yield Static(
                    "Starts the local AI server and downloads a missing model "
                    "instead of stopping a run to ask. Turn it off if you would "
                    "rather start it yourself.",
                    classes="ai-note",
                )

            log = RichLog(id="ai-log", wrap=True, auto_scroll=False, markup=False)
            log.border_title = "Connection test"
            yield log

            with Horizontal(id="ai-buttons"):
                yield Button("Test connection", id="ai-test", compact=True)
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
    def _key_row_classes(provider: ai_config.ProviderSpec) -> str:
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
        if current.api_key:
            return f"Found in {env}."
        return (
            f"Not set. Opengrader never stores a key — put it in the {env} "
            "environment variable and it is picked up from there."
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
            timeout_seconds = ai_config.parse_timeout_seconds(raw_timeout)
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
            escalate_only=self.query_one("#ai-escalate-only", Checkbox).value,
            transcribe_math=self.query_one("#ai-transcribe-math", Checkbox).value,
            grade_written=self.query_one("#ai-grade-written", Checkbox).value,
            auto_grade=self.query_one("#ai-auto-grade", Checkbox).value,
            require_calibration=self.query_one(
                "#ai-require-calibration", Checkbox
            ).value,
            min_confidence_measured=min_confidence_measured,
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

    @on(Select.Changed, "#ai-provider")
    def _on_provider_changed(self, event: Select.Changed) -> None:
        event.stop()
        provider = ai_config.get_provider(str(event.value))
        preview = ai_config.resolve(replace(self._settings, provider=provider.name))

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
        for button in self.query(Button):
            button.disabled = True
        log = self.query_one("#ai-log", RichLog)
        log.clear()
        log.write("Testing…")
        self.run_worker(
            lambda: self._test(ai_config.resolve(pending)), thread=True, exclusive=True
        )

    def _test(self, current: ResolvedAI) -> None:
        lines: list[str] = list(current.problems())
        try:
            available = ai_config.available_models(current)
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
            measurement = ai_config.measure_generate_latency(
                current, role="handwriting"
            )
            lines.append(measurement.describe())
            if measurement.ok:
                recommendation = ai_config.recommend_timeout(
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

    @on(Button.Pressed, "#ai-save")
    def _on_save(self, event: Button.Pressed) -> None:
        event.stop()
        pending = self.collect()
        if pending is None:
            return
        try:
            destination = ai_config.save_settings(pending)
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

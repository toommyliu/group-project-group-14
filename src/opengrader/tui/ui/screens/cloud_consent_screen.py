"""Consent modals for sending student work to a cloud AI provider.

Every word a person reads here comes from `core.app_config.consent` — this
module only lays it out. Nothing here calls `save_settings` on its own
initiative except after the corresponding `grant_*_consent` call succeeds, so
a screen dismissed with `False`/`None` (Cancel or Escape) never writes
anything.

`CloudConsentScreen` is shown before a cloud provider choice is accepted, so
the user reads what leaves the machine before it does.
`CredentialConsentScreen` is shown before a provider's manifest is allowed to
run a command to fetch its API key, so the user reads the literal command
first. `CloudPreflightConfirmScreen` is a third, separate confirmation: not
about the provider in general, but about the actual scale of one pipeline run
about to send something (how many images, how many students, roughly what it
will cost).

Names and student IDs are never sent to a cloud provider — there is no
setting or override that changes that. Opengrader reads them on this
computer only.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.core.app_config import ProviderSpec, ResolvedAI, consent
from opengrader.core.app_config.consent import ConsentText
from opengrader.tui.ui.button_roles import OutlineButtons


def _format_argv(argv: tuple[str, ...]) -> str:
    return consent.format_argv(argv)


class _ConsentDialog(OutlineButtons, ModalScreen[bool]):
    """Renders a `ConsentText` and dismisses with True (accept) or False (cancel)."""

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    _ConsentDialog {
        align: center middle;
    }

    .consent-dialog {
        width: 84%;
        max-width: 104;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    .consent-title {
        width: 100%;
        height: auto;
        text-style: bold;
        margin-bottom: 1;
    }

    .consent-paragraph, .consent-subhead, .consent-item, .consent-argv {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    .consent-subhead {
        text-style: bold;
        margin-bottom: 0;
    }

    .consent-item {
        color: $text-warning;
        margin-bottom: 0;
    }

    .consent-argv {
        background: $surface-darken-1;
        color: $text;
        padding: 0 1;
    }

    .consent-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    .consent-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, text: ConsentText, *, dialog_id: str) -> None:
        super().__init__()
        self._text = text
        self._dialog_id = dialog_id

    def compose(self) -> ComposeResult:
        with VerticalScroll(id=self._dialog_id, classes="consent-dialog") as dialog:
            dialog.border_title = f"▸ {self._text.title}"
            yield Static(self._text.title, classes="consent-title", markup=False)
            for paragraph in self._text.paragraphs:
                yield Static(paragraph, classes="consent-paragraph", markup=False)
            if self._text.leaves_machine:
                yield Static("What leaves this computer:", classes="consent-subhead")
                for item in self._text.leaves_machine:
                    yield Static(f"- {item}", classes="consent-item", markup=False)
            if self._text.argv:
                yield Static("Command that will run:", classes="consent-subhead")
                yield Static(_format_argv(self._text.argv), classes="consent-argv", markup=False)
            with Horizontal(classes="consent-buttons"):
                yield Button("Cancel", id="consent-cancel")
                yield Button(self._text.accept_label, id="consent-accept", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#consent-cancel", Button).focus()

    @on(Button.Pressed, "#consent-accept")
    def _on_accept(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(True)

    @on(Button.Pressed, "#consent-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class CloudConsentScreen(_ConsentDialog):
    """Shown before a cloud provider choice takes effect, in the user's own words."""

    def __init__(self, resolved: ResolvedAI) -> None:
        super().__init__(consent.cloud_consent_text(resolved), dialog_id="cloud-consent-dialog")


class CredentialConsentScreen(_ConsentDialog):
    """The literal argv a manifest's `credential_command` will run, shown first."""

    def __init__(self, spec: ProviderSpec) -> None:
        super().__init__(
            consent.credential_consent_text(spec), dialog_id="credential-consent-dialog"
        )


class CloudPreflightConfirmScreen(OutlineButtons, ModalScreen[bool]):
    """Confirms the actual scale of one run before it sends anything.

    This is separate from `CloudConsentScreen`: that one is given once, when a
    cloud provider is chosen in general. This one shows the real count for the
    run about to happen — how many images, from how many students, and the
    estimated cost. For the pipeline it comes from
    `core.cloud_preflight.plan_cloud_egress` and confirming re-runs the request
    with `send_to_cloud_confirmed=True`; for the Extractor and Answer key tools
    it comes from the CLI's `CloudConfirmationNeeded` and confirming re-runs
    the CLI with `--yes-send-to-cloud`. Cancelling sends nothing.
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    CloudPreflightConfirmScreen {
        align: center middle;
    }

    #cloud-preflight-dialog {
        width: 84%;
        max-width: 104;
        height: auto;
        max-height: 90%;
        background: $surface;
        border: round $warning;
        border-title-align: left;
        border-title-color: $warning;
        border-title-style: bold;
        padding: 1 2;
    }

    #cloud-preflight-statement {
        width: 100%;
        height: auto;
        margin-bottom: 1;
    }

    #cloud-preflight-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }

    #cloud-preflight-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, statement: str, host: str) -> None:
        super().__init__()
        self._statement = statement
        self._host = host

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="cloud-preflight-dialog") as dialog:
            dialog.border_title = f"▸ Send this run to {consent.hostname_of(self._host)}?"
            yield Static(self._statement, id="cloud-preflight-statement", markup=False)
            with Horizontal(id="cloud-preflight-buttons"):
                yield Button("Cancel", id="consent-cancel")
                yield Button("Send now", id="consent-accept", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#consent-cancel", Button).focus()

    @on(Button.Pressed, "#consent-accept")
    def _on_accept(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(True)

    @on(Button.Pressed, "#consent-cancel")
    def _on_cancel(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)

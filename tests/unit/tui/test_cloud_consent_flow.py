"""End-to-end coverage of the cloud-consent gates in AI Setup and the pipeline.

Every one of these drives the real screens headless with Textual's pilot —
no network call is ever allowed to actually happen; a test that needs one
patches it, and the credential test patches `subprocess.run` to fail loudly
if it is ever reached, which is how "Cancel runs nothing" is checked.

The cloud provider used throughout is registered through a hand-written
manifest file dropped into the isolated config dir (see
`tests/unit/tui/test_provider_screen.py` and
`tests/unit/core/submissions/test_extraction_through_a_manifest_provider.py`
for the same pattern) rather than `app_config.register_provider()`, so this
also exercises the manifest-loading path a real user would go through.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace

import pytest
from textual.app import App
from textual.widgets import Button, Checkbox, Input, Select, Static

import opengrader.tui.ui.screens.setup_screen as setup_screen
from opengrader.core import app_config
from opengrader.core.app_config import AISettings, ProviderSpec, manifests as ai_manifests
from opengrader.core.cloud_preflight import CloudConfirmationNeeded, CloudPreflight
from opengrader.core.pipeline import PipelineReport, StageOutcome, StageStatus
from opengrader.tui.tui import TuiApp
from opengrader.tui.ui.screens.ai_settings_screen import AISettingsScreen
from opengrader.tui.ui.screens.setup_screen import SetupScreen
from opengrader.tui.ui.widgets.ai_egress_banner import AIEgressBanner

from .fakes import DEFAULT_IMAGE_PATH

_SIZE = (130, 48)

_CREDENTIAL_MARKER = "credential-flow-test-marker"


class _Host(App):
    """Hosts a single modal screen, recording what it is dismissed with."""

    def __init__(self, screen: AISettingsScreen) -> None:
        super().__init__()
        self._screen = screen
        self.result: AISettings | None = None
        self.dismissed = False

    def on_mount(self) -> None:
        def _record(value: AISettings | None) -> None:
            self.result = value
            self.dismissed = True

        self.push_screen(self._screen, _record)


async def _settle(pilot, predicate, limit: int = 150) -> None:  # noqa: ANN001
    for _ in range(limit):
        await pilot.pause()
        await asyncio.sleep(0.01)
        if predicate():
            return


def _has(app, selector: str) -> bool:  # noqa: ANN001
    try:
        app.screen.query_one(selector)
        return True
    except Exception:  # noqa: BLE001 - "not there yet" is exactly what we're polling for
        return False


@pytest.fixture(autouse=True)
def _clean_registry():
    """Manifest providers live in a module-level registry; keep it tidy per test.

    Same pattern as `test_provider_screen.py`'s fixture of the same name: the
    isolated config dir (see `tests/conftest.py`) is already fresh per test,
    but `app_config._REGISTRY` is a process-wide dict, so a manifest one test
    installs must be dropped again before the next test's `reload_manifests()`.
    """
    app_config.reload_manifests()
    try:
        yield
    finally:
        for path in app_config.manifests_dir().glob("*.toml"):
            path.unlink()
        app_config.reload_manifests()


@pytest.fixture
def cloud_provider() -> ProviderSpec:
    """A cloud provider with a `credential_command`, installed as a manifest.

    One provider covers every scenario below: plain cloud-consent gating
    (selecting it, saving it) as well as the credential-consent gate, which
    only fires when `credential_command` is set.
    """
    text = ai_manifests.render_manifest(
        name="cloud-flow-test",
        label="Cloud Flow Test",
        dialect="openai-chat",
        host="https://api.cloud-flow-test.example/v1",
        vision_model="test-vision-1",
        text_model="test-text-1",
        credential_command=(
            sys.executable,
            "-c",
            f"print('{_CREDENTIAL_MARKER}')",
        ),
    )
    _dest, spec = ai_manifests.install_manifest(text)
    app_config.reload_manifests()
    return spec


# ---------------------------------------------------------------------------
# 1. Cloud consent on selection
# ---------------------------------------------------------------------------


def test_selecting_a_cloud_provider_shows_consent_and_cancel_reverts_and_saves_nothing(
    cloud_provider: ProviderSpec,
) -> None:
    async def run() -> tuple[str, bool, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            select = screen.query_one("#ai-provider", Select)
            assert select.value == app_config.DEFAULT_PROVIDER

            select.value = cloud_provider.name
            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog"))
            shown = _has(app, "#cloud-consent-dialog")

            await pilot.click("#consent-cancel")
            await pilot.pause()
            reverted = str(select.value)
        return reverted, shown, app_config.settings_path().read_text() if app_config.settings_path().exists() else ""

    reverted, shown, written = asyncio.run(run())
    assert shown is True
    assert reverted == app_config.DEFAULT_PROVIDER
    assert written == ""
    assert not app_config.load_settings().consents


def test_confirming_cloud_consent_on_selection_saves_a_consent_record(
    cloud_provider: ProviderSpec,
) -> None:
    async def run() -> str:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            select = screen.query_one("#ai-provider", Select)
            select.value = cloud_provider.name
            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog"))
            await pilot.click("#consent-accept")
            await pilot.pause()
            accepted_value = str(select.value)
        return accepted_value

    accepted_value = asyncio.run(run())
    assert accepted_value == cloud_provider.name

    saved = app_config.load_settings()
    matching = [
        record
        for record in saved.consents
        if record.provider == cloud_provider.name and record.kind == "cloud"
    ]
    assert len(matching) == 1


def test_save_with_a_hand_typed_public_host_on_ollama_requires_consent() -> None:
    async def run() -> tuple[bool, bool, str]:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            screen = app.screen
            screen.query_one("#ai-host", Input).value = "https://public.example.org"
            await pilot.pause()
            await pilot.click("#ai-save")

            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog") or app.dismissed)
            gated_before_cancel = _has(app, "#cloud-consent-dialog")
            await pilot.click("#consent-cancel")
            await _settle(pilot, lambda: app.dismissed or True, limit=5)
            written_after_cancel = app_config.settings_path().exists()

            # Now go through it and confirm.
            await pilot.click("#ai-save")
            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog"))
            await pilot.click("#consent-accept")
            await _settle(pilot, lambda: app.dismissed)
        return gated_before_cancel, written_after_cancel, app.result.host if app.result else ""

    gated, written_after_cancel, saved_host = asyncio.run(run())
    assert gated is True
    assert written_after_cancel is False
    assert saved_host == "https://public.example.org"

    reloaded = app_config.load_settings()
    assert reloaded.host == "https://public.example.org"
    assert any(r.provider == "ollama" and r.kind == "cloud" for r in reloaded.consents)


# ---------------------------------------------------------------------------
# 2. credential_command consent
# ---------------------------------------------------------------------------


def test_credential_screen_shows_the_literal_argv_and_cancel_runs_nothing(
    cloud_provider: ProviderSpec, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args, **_kwargs):
        raise AssertionError("subprocess.run must not be called when consent is cancelled")

    monkeypatch.setattr("subprocess.run", _boom)

    notices: list[str] = []

    async def run() -> str:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            app.notify = lambda message, **kwargs: notices.append(str(message))  # type: ignore[method-assign]
            screen = app.screen
            screen.query_one("#ai-provider", Select).value = cloud_provider.name
            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog"))
            await pilot.click("#consent-accept")
            await pilot.pause()

            await pilot.click("#ai-test")
            await _settle(pilot, lambda: _has(app, "#credential-consent-dialog"))
            argv_text = str(
                app.screen.query_one(".consent-argv", Static).render()
            )

            await pilot.click("#consent-cancel")
            await _settle(
                pilot, lambda: not _has(app, "#credential-consent-dialog")
            )
        return argv_text

    argv_text = asyncio.run(run())
    assert _CREDENTIAL_MARKER in argv_text
    assert "-c" in argv_text
    assert any("not approved" in n.lower() for n in notices)
    assert not app_config.load_settings().consents or all(
        r.kind != "credential" for r in app_config.load_settings().consents
    )


# ---------------------------------------------------------------------------
# 3. Banner
# ---------------------------------------------------------------------------


def test_banner_shows_for_cloud_hides_for_local_and_updates_after_ai_setup(
    cloud_provider: ProviderSpec,
) -> None:
    async def run() -> tuple[bool, bool, str, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(130, 48)) as pilot:
            await pilot.pause()
            banner = app.query_one("#ai-egress-banner", AIEgressBanner)
            visible_before = banner.has_class("-visible")

            setup = app.get_setup_screen()
            setup._open_ai_settings()
            await pilot.pause()
            app.screen.query_one("#ai-provider", Select).value = cloud_provider.name
            await _settle(pilot, lambda: _has(app, "#cloud-consent-dialog"))
            await pilot.click("#consent-accept")
            await pilot.pause()
            await pilot.click("#ai-save")
            await _settle(pilot, lambda: not isinstance(app.screen, AISettingsScreen))

            visible_after_cloud = banner.has_class("-visible")
            text_after_cloud = str(banner.render())

            setup._open_ai_settings()
            await pilot.pause()
            app.screen.query_one("#ai-provider", Select).value = app_config.DEFAULT_PROVIDER
            await pilot.pause()
            await pilot.click("#ai-save")
            await _settle(pilot, lambda: not isinstance(app.screen, AISettingsScreen))

            visible_after_local = banner.has_class("-visible")
        return visible_before, visible_after_cloud, text_after_cloud, visible_after_local

    before, after_cloud, text_cloud, after_local = asyncio.run(run())
    assert before is False
    assert after_cloud is True
    assert "cloud-flow-test.example" in text_cloud
    assert after_local is False


# ---------------------------------------------------------------------------
# 4. escalate_only stays None
# ---------------------------------------------------------------------------


def test_escalate_only_stays_none_after_open_and_save_with_no_change() -> None:
    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return app.result

    result = asyncio.run(run())
    assert result is not None
    assert result.escalate_only is None
    assert app_config.load_settings().escalate_only is None


# ---------------------------------------------------------------------------
# 5. read-identity-locally checkbox
# ---------------------------------------------------------------------------


def test_read_identity_locally_checkbox_defaults_on_and_round_trips_off() -> None:
    async def run() -> AISettings | None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            checkbox = app.screen.query_one("#ai-read-identity-locally", Checkbox)
            default_value = checkbox.value
            checkbox.value = False
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()
        return default_value, app.result

    default_value, result = asyncio.run(run())
    assert default_value is True
    assert result is not None
    assert result.read_identity_locally is False
    assert app_config.load_settings().read_identity_locally is False


def test_read_identity_locally_untouched_keeps_the_default_and_is_not_written() -> None:
    async def run() -> None:
        app = _Host(AISettingsScreen())
        async with app.run_test(size=_SIZE) as pilot:
            await pilot.pause()
            await pilot.click("#ai-save")
            await pilot.pause()

    asyncio.run(run())
    raw = json.loads(app_config.settings_path().read_text(encoding="utf-8"))
    assert "read_identity_locally" not in raw
    assert app_config.load_settings().read_identity_locally is True


# ---------------------------------------------------------------------------
# 6. Pipeline pre-run confirmation
# ---------------------------------------------------------------------------


def _blocked_report(request, preflight: CloudPreflight) -> PipelineReport:
    outcome = StageOutcome(
        stage="extract",
        status=StageStatus.BLOCKED,
        message="cloud confirmation needed",
        detail="cloud-preflight:\n" + preflight.statement(),
    )
    return PipelineReport(
        stages=(outcome,),
        submissions_dir=request.submissions_dir,
        qa_file=request.qa_file,
        cloud_preflight=preflight,
    )


def _ok_report(request) -> PipelineReport:
    return PipelineReport(
        stages=(StageOutcome(stage="extract", status=StageStatus.RAN, message="ok"),),
        submissions_dir=request.submissions_dir,
        qa_file=request.qa_file,
    )


def test_pipeline_confirmation_cancel_leaves_a_status_line_and_does_not_rerun(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    preflight = CloudPreflight(
        provider_label="Cloud Flow Test",
        host="https://api.cloud-flow-test.example/v1",
        counts={"answer-image": 4},
        students=2,
        estimated_cost=0.12,
        cost_basis="a conservative estimate",
    )
    calls: list = []

    def fake_run_pipeline(request, *, progress=None):
        calls.append(request)
        return _blocked_report(request, preflight)

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    async def run() -> str:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
            statement = str(
                app.screen.query_one("#cloud-preflight-statement", Static).render()
            )
            await pilot.click("#consent-cancel")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: not _has(app, "#cloud-preflight-dialog"))
            status = str(app.query_one("#pipe-progress-status", Static).render())
        return statement, status

    statement, status = asyncio.run(run())
    assert preflight.statement() == statement
    assert "declined" in status.lower()
    assert len(calls) == 1
    assert calls[0].send_to_cloud_confirmed is False


def test_pipeline_confirmation_accept_reruns_with_the_confirmed_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    preflight = CloudPreflight(
        provider_label="Cloud Flow Test",
        host="https://api.cloud-flow-test.example/v1",
        counts={"answer-image": 4},
        students=2,
        estimated_cost=0.12,
        cost_basis="a conservative estimate",
    )
    calls: list = []

    def fake_run_pipeline(request, *, progress=None):
        calls.append(request)
        if not request.send_to_cloud_confirmed:
            return _blocked_report(request, preflight)
        return _ok_report(request)

    monkeypatch.setattr(setup_screen, "run_pipeline", fake_run_pipeline)

    async def run() -> None:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            screen = app.query_one(SetupScreen)
            app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
            app.query_one("#pipe-out", Input).value = str(tmp_path / "cohort")
            screen._run_pipeline()
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
            await pilot.click("#consent-accept")
            await app.workers.wait_for_complete()
            await _settle(pilot, lambda: screen.extraction_in_progress is False)

    asyncio.run(run())
    assert len(calls) == 2
    assert calls[0].send_to_cloud_confirmed is False
    assert calls[1].send_to_cloud_confirmed is True
    # The confirmed re-run is the same request, only the flag differs.
    assert replace(calls[1], send_to_cloud_confirmed=False) == calls[0]


# ---------------------------------------------------------------------------
# The Extractor and Answer key tools run their CLI in-process. They must never
# reach the CLI's input() prompt (it would hang the worker thread); the CLI
# stops first and the TUI asks with the same modal as the pipeline.
# ---------------------------------------------------------------------------

_TOOL_PREFLIGHT = CloudPreflight(
    provider_label="Cloud Flow Test",
    host="https://api.cloud-flow-test.example/v1",
    uncounted={"answer-image": "one answer image per answer found in 2 submission PDFs"},
    students=2,
)


def _fake_tool_cli(calls: list):  # noqa: ANN202
    """A stand-in CLI that refuses a cloud send until --yes-send-to-cloud."""

    def fake(argv, **kwargs):  # noqa: ANN001, ANN003
        calls.append((list(argv), kwargs))
        if "--yes-send-to-cloud" not in argv:
            raise CloudConfirmationNeeded(_TOOL_PREFLIGHT, "Consent wording.")

    return fake


def _prepared_class(app, tmp_path) -> None:  # noqa: ANN001
    """Point the pipeline card at a class folder that already has an extraction."""
    submission = tmp_path / "class" / "submissions" / "s1" / "s1.submission.md"
    submission.parent.mkdir(parents=True)
    submission.write_text("", encoding="utf-8")
    app.query_one("#pipe-scan", Input).value = str(tmp_path / "scan.pdf")
    app.query_one("#pipe-out", Input).value = str(tmp_path / "class")


async def _answer_modal(app, pilot, button: str) -> str:  # noqa: ANN001
    await app.workers.wait_for_complete()
    await _settle(pilot, lambda: _has(app, "#cloud-preflight-dialog"))
    statement = str(app.screen.query_one("#cloud-preflight-statement", Static).render())
    await pilot.click(button)
    await _settle(pilot, lambda: not _has(app, "#cloud-preflight-dialog"))
    await app.workers.wait_for_complete()
    await pilot.pause()
    return statement


@pytest.mark.parametrize("button", ["#consent-accept", "#consent-cancel"])
def test_the_re_read_asks_in_a_modal_and_reruns_only_when_confirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path, button: str
) -> None:
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_extractor_cli", _fake_tool_cli(calls))
    notices: list[str] = []

    async def run() -> tuple[str, bool, bool]:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda message, **_k: notices.append(message))
            screen = app.query_one(SetupScreen)
            _prepared_class(app, tmp_path)
            screen._run_refine()
            statement = await _answer_modal(app, pilot, button)
            return (
                statement,
                app.query_one("#run-refine", Button).disabled,
                screen.extraction_in_progress,
            )

    statement, button_disabled, in_progress = asyncio.run(run())
    assert _TOOL_PREFLIGHT.statement() in statement
    assert "Consent wording." in statement
    assert all(kwargs["prompt_cloud_send"] is False for _argv, kwargs in calls)
    assert "--yes-send-to-cloud" not in calls[0][0]
    assert "--refine" in calls[0][0]
    if button == "#consent-accept":
        assert len(calls) == 2
        assert calls[1][0] == [*calls[0][0], "--yes-send-to-cloud"]
        assert notices[-1] == "Re-read complete."
    else:
        assert len(calls) == 1
        assert "declined" in notices[-1]
    assert button_disabled is False
    assert in_progress is False


@pytest.mark.parametrize("button", ["#consent-accept", "#consent-cancel"])
def test_answer_key_asks_in_a_modal_and_reruns_only_when_confirmed(
    monkeypatch: pytest.MonkeyPatch, tmp_path, button: str
) -> None:
    calls: list = []
    monkeypatch.setattr(setup_screen, "run_answer_key_cli", _fake_tool_cli(calls))
    notices: list[str] = []

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda message, **_k: notices.append(message))
            screen = app.query_one(SetupScreen)
            app.query_one("#ak-input", Input).value = str(tmp_path / "key.pdf")
            app.query_one("#ak-output", Input).value = str(tmp_path / "qa.md")
            screen._run_answer_key()
            await _answer_modal(app, pilot, button)
            return app.query_one("#run-answer-key", Button).disabled

    button_disabled = asyncio.run(run())
    assert all(kwargs["prompt_cloud_send"] is False for _argv, kwargs in calls)
    if button == "#consent-accept":
        assert len(calls) == 2
        assert calls[1][0] == [*calls[0][0], "--yes-send-to-cloud"]
        assert notices[-1] == "Answer key created."
    else:
        assert len(calls) == 1
        assert "declined" in notices[-1]
    assert button_disabled is False


def test_a_re_read_refusal_resets_the_form_instead_of_escaping_the_worker(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    # SystemExit is not an Exception: it used to slip past the worker's handler.
    def refuse(argv, **kwargs):  # noqa: ANN001, ANN003
        sys.exit(2)

    monkeypatch.setattr(setup_screen, "run_extractor_cli", refuse)
    notices: list[str] = []

    async def run() -> bool:
        app = TuiApp(default_image_path=DEFAULT_IMAGE_PATH)
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            monkeypatch.setattr(app, "notify", lambda message, **_k: notices.append(message))
            _prepared_class(app, tmp_path)
            app.query_one(SetupScreen)._run_refine()
            await app.workers.wait_for_complete()
            await pilot.pause()
            return app.query_one("#run-refine", Button).disabled

    assert asyncio.run(run()) is False
    assert notices[-1] == "Re-read failed (exit 2)."

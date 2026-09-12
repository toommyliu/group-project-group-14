import subprocess
import sys
from pathlib import Path

import pytest

from opengrader.tui.external_viewer import (
    ENV_VIEWER,
    ExternalViewerError,
    describe_external_viewer,
    open_in_external_viewer,
    viewer_command,
)


@pytest.fixture()
def image(tmp_path: Path) -> Path:
    path = tmp_path / "q1.png"
    path.write_bytes(b"not really a png")
    return path


def test_platform_launcher_is_used_by_default(monkeypatch, image: Path) -> None:
    monkeypatch.delenv(ENV_VIEWER, raising=False)

    command = viewer_command(image)

    expected = {"darwin": "open", "win32": str(image)}.get(sys.platform, "xdg-open")
    assert command[0] == expected
    assert command[-1] == str(image)


def test_configured_viewer_overrides_the_platform_launcher(
    monkeypatch, image: Path
) -> None:
    monkeypatch.setenv(ENV_VIEWER, "feh --scale-down")

    assert viewer_command(image) == ["feh", "--scale-down", str(image)]


def test_placeholder_positions_the_path(monkeypatch, image: Path) -> None:
    monkeypatch.setenv(ENV_VIEWER, "myviewer {} --fullscreen")

    assert viewer_command(image) == ["myviewer", str(image), "--fullscreen"]


def test_empty_viewer_setting_is_an_error(monkeypatch, image: Path) -> None:
    monkeypatch.setenv(ENV_VIEWER, "   '' ")

    with pytest.raises(ExternalViewerError):
        viewer_command(image)


def test_missing_image_reports_the_path(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv(ENV_VIEWER, raising=False)

    with pytest.raises(ExternalViewerError) as excinfo:
        open_in_external_viewer(tmp_path / "gone.png")

    assert "gone.png" in str(excinfo.value)


def test_viewer_is_launched_detached_and_silenced(monkeypatch, image: Path) -> None:
    monkeypatch.setenv(ENV_VIEWER, "myviewer")
    calls: list[tuple[list[str], dict]] = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return object()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    opened = open_in_external_viewer(image)

    assert opened == "q1.png"
    command, kwargs = calls[0]
    assert command == ["myviewer", str(image.resolve())]
    assert kwargs["stdout"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.DEVNULL
    assert kwargs["stdin"] is subprocess.DEVNULL
    if sys.platform != "win32":
        assert kwargs["start_new_session"] is True


def test_missing_launcher_becomes_an_actionable_error(
    monkeypatch, image: Path
) -> None:
    monkeypatch.setenv(ENV_VIEWER, "definitely-not-installed")

    def fake_popen(command, **kwargs):
        raise FileNotFoundError(command[0])

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    with pytest.raises(ExternalViewerError) as excinfo:
        open_in_external_viewer(image)

    message = str(excinfo.value)
    assert "definitely-not-installed" in message
    assert ENV_VIEWER in message


def test_describe_names_the_configured_viewer(monkeypatch) -> None:
    monkeypatch.setenv(ENV_VIEWER, "eog")

    assert describe_external_viewer().startswith("eog")

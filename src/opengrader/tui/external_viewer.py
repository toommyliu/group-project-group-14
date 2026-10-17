from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

# {} substitutes the file path; without it, the path is appended.
ENV_VIEWER = "OPENGRADER_IMAGE_VIEWER"

_PLACEHOLDER = "{}"


class ExternalViewerError(RuntimeError):
    pass


def _platform_launcher() -> list[str] | None:
    if sys.platform == "darwin":
        return ["open"]
    if sys.platform == "win32":
        return []
    return ["xdg-open"]


def viewer_command(path: str | Path) -> list[str]:
    configured = os.environ.get(ENV_VIEWER, "").strip()
    if configured:
        parts = shlex.split(configured)
        if not parts or not parts[0]:
            raise ExternalViewerError(
                f"{ENV_VIEWER} is set but contains no viewer command"
            )
        if any(_PLACEHOLDER in part for part in parts):
            return [part.replace(_PLACEHOLDER, str(path)) for part in parts]
        return [*parts, str(path)]

    launcher = _platform_launcher()
    if not launcher:
        return [str(path)]
    return [*launcher, str(path)]


def describe_external_viewer() -> str:
    configured = os.environ.get(ENV_VIEWER, "").strip()
    if configured:
        return f"{configured} (from {ENV_VIEWER})"
    if sys.platform == "win32":
        return "the file's default Windows application"
    launcher = _platform_launcher()
    program = launcher[0] if launcher else ""
    resolved = shutil.which(program) if program else None
    if resolved:
        return f"{resolved} (override with {ENV_VIEWER})"
    return f"{program} - NOT FOUND on PATH (set {ENV_VIEWER} to a viewer command)"


def open_in_external_viewer(path: str | Path) -> str:
    target = Path(path)
    if not target.exists():
        raise ExternalViewerError(f"image not found: {target}")

    resolved = target.resolve()

    configured = os.environ.get(ENV_VIEWER, "").strip()
    if sys.platform == "win32" and not configured:
        try:
            os.startfile(str(resolved))  # type: ignore[attr-defined]  # noqa: S606
        except OSError as exc:
            raise ExternalViewerError(f"could not open {target.name}: {exc}") from exc
        return target.name

    command = viewer_command(resolved)
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True

    try:
        subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]  # noqa: S603
    except (OSError, ValueError) as exc:
        raise ExternalViewerError(
            f"could not launch {command[0]!r}: {exc}. "
            f"Set {ENV_VIEWER} to an image-viewer command."
        ) from exc

    return target.name


def text_editor_command(path: str | Path) -> list[str]:
    """The computer's plain-text editor on a file, whatever its extension.

    An answer key is a `.md` file, and a computer with no Markdown program
    has nothing to open one with on a double-click. These need none: TextEdit
    (`open -t`), Notepad, and on Linux whatever handles plain text.
    """
    if sys.platform == "darwin":
        return ["open", "-t", str(path)]
    if sys.platform == "win32":
        return ["notepad", str(path)]
    return ["xdg-open", str(path)]


def open_in_text_editor(path: str | Path) -> str:
    target = Path(path)
    if not target.exists():
        raise ExternalViewerError(f"file not found: {target}")
    command = text_editor_command(target.resolve())
    popen_kwargs: dict[str, object] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **popen_kwargs)  # type: ignore[arg-type]  # noqa: S603
    except (OSError, ValueError) as exc:
        raise ExternalViewerError(f"could not launch {command[0]!r}: {exc}") from exc
    return target.name


def open_folder(path: str | Path) -> str:
    """Show a folder in the computer's file manager (Finder, Explorer, …).

    So a teacher can drag it into an assistant app, or open it from there,
    without typing its path.
    """
    target = Path(path)
    if not target.is_dir():
        raise ExternalViewerError(f"folder not found: {target}")
    if sys.platform == "win32":
        try:
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
        except OSError as exc:
            raise ExternalViewerError(f"could not open {target.name}: {exc}") from exc
        return target.name
    command = ["open", str(target)] if sys.platform == "darwin" else ["xdg-open", str(target)]
    try:
        subprocess.Popen(  # noqa: S603
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise ExternalViewerError(f"could not launch {command[0]!r}: {exc}") from exc
    return target.name

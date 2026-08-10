"""Open an answer crop in the operating system's image viewer.

The escape hatch for everything terminal graphics cannot do: a terminal with
no image protocol at all, a crop too dense to read at cell resolution, a
grader who wants to pan around a full page at 300 DPI. One keystroke hands the
PNG to the desktop and the TUI carries on running.

Rules that shape the implementation:

* **Never block the grading loop.** The viewer is spawned detached; we do not
  wait for it, and closing it does not come back through us.
* **Never let the child touch our terminal.** Its stdio is redirected to
  ``/dev/null`` and, on POSIX, it gets its own session -- a viewer that wrote
  to stdout or grabbed the tty would corrupt the TUI's rendering.
* **Fail with something actionable.** A missing launcher or a missing file
  raises :class:`ExternalViewerError` carrying a message fit to show the user.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

#: Command template override, e.g. ``OPENGRADER_IMAGE_VIEWER="feh {}"``. A
#: ``{}`` placeholder is replaced with the file path; without one the path is
#: appended as the final argument.
ENV_VIEWER = "OPENGRADER_IMAGE_VIEWER"

_PLACEHOLDER = "{}"


class ExternalViewerError(RuntimeError):
    """The crop could not be handed to an external viewer."""


def _platform_launcher() -> list[str] | None:
    """The desktop's generic "open this file" command, if there is one."""
    if sys.platform == "darwin":
        return ["open"]
    if sys.platform == "win32":
        # Handled by os.startfile; no argv form needed.
        return []
    return ["xdg-open"]


def viewer_command(path: str | Path) -> list[str]:
    """Build the argv that opens ``path``.

    An explicit ``OPENGRADER_IMAGE_VIEWER`` always wins, so a grader on a
    headless box, a remote SSH session with an X forward, or simply a strong
    preference for a particular viewer can point us wherever they like.
    """
    configured = os.environ.get(ENV_VIEWER, "").strip()
    if configured:
        parts = shlex.split(configured)
        # `shlex.split("''")` yields `[""]`, which would try to exec the empty
        # string; treat a blank program name as "not configured, but wrong".
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
    """Human-readable account of what would be launched (for diagnostics)."""
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
    """Launch the OS image viewer on ``path`` and return immediately.

    Returns:
        The file name that was opened, for a confirmation message.

    Raises:
        ExternalViewerError: The file is missing, or no viewer could be started.
    """
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
    # start_new_session detaches the child from our controlling terminal, so a
    # viewer that misbehaves cannot steal input from the TUI or die with it.
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

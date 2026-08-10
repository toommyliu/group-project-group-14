# Packaging & Desktop Distribution

Everything needed to hand Opengrader to an end-user as a **clickable desktop
icon** — no Python knowledge required on their part. One installer run creates
the icon; every launch self-heals the environment.

```
packaging/
├── bootstrap.sh      # launcher: env check/auto-install → run TUI (macOS, Linux)
├── bootstrap.ps1     # launcher: same, for Windows
├── install.sh        # one-shot installer: copy app + create shortcut (macOS, Linux)
├── install.ps1       # one-shot installer for Windows
└── icons/
    ├── opengrader.png   # master icon (1024×1024, macOS/Linux)
    └── opengrader.ico   # Windows shortcut icon
```

## Install (what you tell users to run)

From a checkout of this repo:

| OS | Command |
| --- | --- |
| macOS / Linux | `bash packaging/install.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install.ps1` |

That's it. An **Opengrader** icon appears on the Desktop; double-clicking it
opens a terminal window, quietly verifies/installs everything, then launches
the TUI. The first launch takes a minute or two (Python + dependencies);
subsequent launches are instant.

## How it works

### Layered design

1. **`install.*` (run once)** — copies the repo to a per-user directory and
   generates the platform's native shortcut pointing at the bootstrap script:
   - macOS → `~/Library/Application Support/Opengrader` + a real
     `Opengrader.app` bundle in `~/Applications` (symlinked on the Desktop).
     The `.icns` is built at install time from `icons/opengrader.png` using
     stock `sips`/`iconutil` — no binary icon checked into git.
   - Linux → `~/.local/share/opengrader` + a freedesktop `.desktop` entry
     (`Terminal=true` forces a terminal window) in the apps menu and Desktop.
   - Windows → `%LOCALAPPDATA%\Opengrader` + a Desktop `.lnk` with the `.ico`
     attached, opening Windows Terminal when available.

   No sudo/admin at any point; everything is per-user. It also records the
   checkout's path in `<install root>/source-path`.

2. **Self-updating** — on every launch, bootstrap checks `source-path`: if
   that checkout still exists, it mirrors it into the installed copy (and
   stages the updated launcher for the next run) before starting the TUI. So
   on a machine that keeps the repo around, the desktop icon always runs the
   latest code — edit or `git pull`, then just click the icon. If the
   checkout is gone (typical end-user), the step is skipped and the installed
   copy keeps working; re-running the installer remains the manual update
   path.

3. **`bootstrap.*` (runs on every click)** — validates the environment behind
   a clean per-step progress display, repairing whatever is missing:
   - **uv** — the only tool bootstrapped directly (official installer/winget).
     It then owns everything Python-side.
   - **Python 3.12** — `uv python install 3.12`: a uv-managed interpreter,
     never touching the system Python. Matches the project's strict
     `>=3.12,<3.13` pin.
   - **Dependencies** — `uv sync --frozen --no-dev`: installs exactly what
     `uv.lock` records, into the app's own `.venv`.
   - **Tesseract OCR** — best-effort system install (brew / winget; on Linux
     it asks before using sudo). Never blocks launch: the TUI works without
     it, only scan extraction needs it.
   - **Ollama** is *not* auto-installed (multi-GB models, user choice) — the
     script prints a pointer instead.

   All installer output is redirected to a log (`opengrader-bootstrap.log` in
   the temp dir); on failure the last lines are shown and the window waits for
   a keypress instead of vanishing. Each step is a no-op when already
   satisfied, so the check adds well under a second after first run.

### Forcing a terminal window

- **macOS**: the app bundle's executable picks the best installed terminal —
  Ghostty → iTerm2 → WezTerm → kitty → Terminal.app (graphics-capable
  terminals first, so scan images render at full fidelity) — and opens
  `Opengrader.command` in it. Terminal.app is a working last resort: the TUI
  detects it, shows a coarse preview, and offers the `o` hotkey to open a crop
  in Preview.app.
- **Linux**: `Terminal=true` in the `.desktop` entry makes the DE spawn its
  default terminal emulator.
- **Windows**: the `.lnk` targets `wt.exe` (or `powershell.exe`), which always
  opens a console window.

### Changing the icon

Replace `packaging/icons/opengrader.png` (1024×1024 PNG, transparent margins)
and/or `opengrader.ico`, then re-run the installer — macOS rebuilds the
`.icns`, Linux/Windows pick up the file directly. To regenerate both from one
PNG: Pillow's `img.save("opengrader.ico", sizes=[(256,256),(48,48),(32,32),(16,16)])`.

## Uninstall

| OS | Remove |
| --- | --- |
| macOS | `~/Library/Application Support/Opengrader`, `~/Applications/Opengrader.app`, `~/Desktop/Opengrader` |
| Linux | `~/.local/share/opengrader`, `~/.local/share/applications/opengrader.desktop`, `~/Desktop/opengrader.desktop` |
| Windows | `%LOCALAPPDATA%\Opengrader`, `Desktop\Opengrader.lnk` |

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
├── opengrader        # the `opengrader` terminal command (finds its own checkout)
├── opengrader.ps1    # the same launcher for Windows
├── opengrader.cmd    # shim: .PS1 is not in PATHEXT, so this is what PATH finds
├── install-command.sh   # puts that command on your PATH (macOS, Linux)
├── install-command.ps1  # the same, for Windows
└── icons/
    ├── opengrader.png   # master icon (1024×1024, macOS/Linux)
    └── opengrader.ico   # Windows shortcut icon
```

Two different audiences, deliberately separate: the **desktop icon** above is
for an instructor who never opens a terminal, while `opengrader` /
`install-command.sh` below are for someone who lives in one. They also open
different apps: the icon runs **`opengrader classic`** — Tools & Settings with
a *Start grading* card, grading class folders in place, no workspace service
unless the user opts in to *Open in browser* (Tools → Browser app) —
while `opengrader tui` in a terminal opens a **workspace** straight to its
assignment list, shared live with the web app.

## The `opengrader` command (terminal users)

| OS | Command | Installs to |
| --- | --- | --- |
| macOS / Linux | `sh packaging/install-command.sh` | `~/.local/bin/opengrader` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1` | `%USERPROFILE%\.local\bin\opengrader.{cmd,ps1}` |

Add `-AddToPath` on Windows to have the installer edit your **user** PATH (never
the machine one, which needs admin rights); without it the script prints the
one-liner and leaves the change to you, matching what the POSIX installer does.
`%USERPROFILE%\.local\bin` is where uv installs itself on Windows, so it is
usually on PATH already.

Windows gets **two** files. A `.ps1` is not executable by name — `.PS1` is not
in `PATHEXT` — so a bare `opengrader` would find nothing with only the script
installed. `opengrader.cmd` is what the shell finds, and it does nothing but
hand off to `opengrader.ps1` beside it (`%~dp0`), with `-NoProfile` and a
process-scoped `-ExecutionPolicy Bypass` so a default RemoteSigned policy does
not refuse a script that was copied rather than signed.

Installs exactly one command rather than the thirty-odd in
`[project.scripts]`. `uv tool install .` is the standard alternative and puts
all of them — `doctor`, `curve`, `claim`, `tui` — on the user's `PATH` as
global names; uv offers no way to filter which entry points a tool install
exposes (checked on uv 0.12), so this installer exists to avoid re-creating,
globally, the problem the `opengrader` front door was built to solve.

`packaging/opengrader` holds **no path to any checkout** — it resolves one on
every run, so it never needs reinstalling when the project moves, is cloned, or
sits on an external drive whose mount point changes (macOS renames a second
volume of the same name to `Samsung_T5 1`, and which drive gets the suffix
depends on plug-in order). Resolution order, first match wins:

| # | Rule | Why |
| --- | --- | --- |
| 1 | `$OPENGRADER_HOME` | An explicit answer wins; a wrong one is an **error**, never a silent fallback |
| 2 | The checkout this script sits in | `packaging/opengrader doctor` works straight from a clone, uninstalled |
| 3 | Walking up from the current directory | Inside a checkout, that checkout is the one you meant — this is what makes two clones behave |
| 4 | The path remembered last time | `$XDG_CACHE_HOME/opengrader/checkout-path`; a *cache*, so deleting it costs one search, never correctness |
| 5 | A bounded search | Each mounted volume, then `$HOME`, depth 5, first root that answers wins |

### Which Python runs it

Preferring uv is not the same as requiring it. Having found the checkout, the
launcher picks the first runner that can actually work:

| # | Runner | Why here |
| --- | --- | --- |
| 1 | `$OPENGRADER_PYTHON` | An interpreter you name yourself; skips uv entirely |
| 2 | A project venv **another tool built** | No `uv = ` line in its `pyvenv.cfg`. Used directly and never handed to uv, because `uv run` would sync a Poetry-managed venv against `uv.lock` and quietly take it over |
| 3 | `uv run --project` | The project's own setup, and the only runner that *installs* what is missing rather than failing on it — so it stays first for everyone else |
| 4 | A uv-made project venv | For a machine where uv has since gone away |
| 5 | Poetry (`poetry env info --path`) | Poetry 2.x reads this project's PEP 621 metadata natively; verified |
| 6 | `$VIRTUAL_ENV` / `$CONDA_PREFIX` | Whatever the caller has activated (Conda does not set `VIRTUAL_ENV`, hence both) |
| 7 | A `python3` on PATH that can already import it | Covers `pipx` and a plain `pip install -e .` |

Everything except uv is invoked as `<python> -m opengrader`, never as the
venv's `bin/opengrader` console script: those scripts hardcode an absolute
interpreter path at install time and rot when it moves. This checkout's own
`.venv/bin/pytest` still points at `/Volumes/Samsung_T5/…`, a path that
stopped existing the day the drive came back as `Samsung_T5 1`. `python -m`
carries no such path, which is why `src/opengrader/__main__.py` exists.

What the launcher **cannot** do is invent an environment: the project needs
Python 3.12 and its dependencies installed somehow. When no runner works it
says so and names all three ways to fix it (`uv sync`, `poetry install`,
`python3.12 -m venv .venv && .venv/bin/pip install -e .`) rather than failing
with an import error.

A directory counts as a checkout when its `pyproject.toml` declares
`name = "opengrader"` — **not** by folder name, which here is a GitHub
Classroom artifact (`group-project-group-14`) that says nothing about what is
inside it and that anyone may rename. On a machine with other Python projects
beside it, that name check is the only thing that tells them apart.

Both launchers implement the same two ordered lists, and a change to one
belongs in the other. Where they necessarily differ:

| | POSIX (`opengrader`) | Windows (`opengrader.ps1`) |
| --- | --- | --- |
| Interpreter in a venv | `bin/python` | `Scripts\python.exe` |
| Search roots | `/Volumes/*`, then `$HOME` | `%USERPROFILE%`, then each ready drive, then the system drive shallowly |
| Loop hazard skipped | symlinked volumes (`/Volumes/Macintosh HD` → `/`) | reparse points / junctions (`C:\Users\All Users`) |
| Reporting an error from the resolver | must `return`, never `exit` — it runs in a command substitution, where `exit` ends only the subshell | `exit` is fine and ends the script |
| Remembered path | `$XDG_CACHE_HOME/opengrader/checkout-path` | `%LOCALAPPDATA%\opengrader\checkout-path` |

Debugging: `OPENGRADER_WRAPPER_DEBUG=1 opengrader ...` prints which rule
matched, on both. `OPENGRADER_SEARCH_ROOTS` overrides where the search looks
(`:`-separated on POSIX, `;`-separated on Windows).

### Verifying on Windows

`opengrader.ps1` was written on a machine with no PowerShell, so it has been
reviewed but **not executed**. CI covers the portable half (see below); this is
the checklist for the rest, in the order a failure is most likely.

```powershell
# 0. It parses at all. Nothing below matters if this reports a ParserError.
powershell -NoProfile -ExecutionPolicy Bypass -Command `
    "$null = [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw packaging\opengrader.ps1), [ref]$null); 'parsed'"

# 1. Run it in place, before installing anything.
powershell -ExecutionPolicy Bypass -File packaging\opengrader.ps1 --version
$env:OPENGRADER_WRAPPER_DEBUG = 1
powershell -ExecutionPolicy Bypass -File packaging\opengrader.ps1 --version   # says which rule matched

# 2. Install, then use it from somewhere else entirely.
powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1 -AddToPath
cd \; opengrader doctor            # new terminal first, so PATH is picked up

# 3. The rules CI cannot reach — the Windows venv layout.
cd <checkout>; uv sync              # or: poetry install
cd \; $env:OPENGRADER_WRAPPER_DEBUG = 1; opengrader --version
#   expect: "running with uv"
$env:OPENGRADER_PYTHON = "<checkout>\.venv\Scripts\python.exe"
opengrader --version                #   expect: "running with $env:OPENGRADER_PYTHON"
Remove-Item Env:\OPENGRADER_PYTHON

# 4. uv absent — the reason the runner list exists. In a shell with uv off PATH:
$env:Path = ($env:Path -split ';' | Where-Object { $_ -notmatch 'uv' }) -join ';'
opengrader --version                #   expect: "running with ...\.venv (uv not installed)"

# 5. stdout stays clean for scripts (the Write-Host trap).
opengrader commands --json | ConvertFrom-Json | Select-Object -First 1

# 6. The TUI gets a real console: keyboard and images, not a pipe.
opengrader tui --check-graphics
```

Most likely to break, given none of it has run: step 0 (a parse error), then
the `Join-Path` calls that build `Scripts\python.exe` paths in step 3, then the
`-Filter` patterns in `Test-VenvHasPackage`. If step 2 installs but `opengrader`
is still not found, check that `%USERPROFILE%\.local\bin` really made it into
PATH and that both `opengrader.cmd` and `opengrader.ps1` landed there — the
`.cmd` is the half PATH can actually find.

Tests stub `uv`, so they assert *which checkout was chosen* without running
anything: `tests/unit/packaging/test_opengrader_wrapper.py` for the POSIX
launcher, and `test_opengrader_wrapper_windows.py` for the PowerShell one —
the latter runs under `pwsh` on any OS and is skipped where PowerShell is
absent, so CI (`ubuntu-latest`, which ships pwsh) is where it actually
executes. The Windows-layout runner rules (`Scripts\python.exe`,
`Lib\site-packages`, `$env:CONDA_PREFIX`) cannot be reached off Windows and
are **not** covered; only a real Windows machine exercises those.

## Install (what you tell users to run)

From a checkout of this repo:

| OS | Command |
| --- | --- |
| macOS / Linux | `bash packaging/install.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install.ps1` |

That's it. An **Opengrader** icon appears on the Desktop; double-clicking it
opens a terminal window, quietly verifies/installs everything, then launches
the classic app (`opengrader classic`). The first launch takes a minute or two (Python + dependencies);
subsequent launches are instant.

### The browser app is optional

Nothing above installs Node.js or pnpm, and nothing needs them: the browser
app is opt-in (Tools → *Browser app* → *Show Open in browser*). Ticking it
also shows a small health panel there — whether Node.js and pnpm are found
(path + version, or an install hint when they're not) and whether the browser
app is built and up to date, stale (its web files changed since it was last
built) or not built at all — with *Check again* and *Build*/*Rebuild browser
app* buttons; nothing in it runs while the section is unticked. The first
press of *Open in browser* on a machine without a built (or an out-of-date)
web app offers to build it there too (`pnpm install --frozen-lockfile` +
`vite build` inside the installed copy, logged to `web-build.log` beside the
AI settings); with no pnpm or corepack it only says what to install, or —
for a stale build — opens the existing one anyway and says so.

To spare users that step, either pass **`--build-web`** to `install.sh` (or
**`-BuildWeb`** to `install.ps1`) — it runs the same `pnpm install
--frozen-lockfile && pnpm build` against this checkout before copying, best
effort: no pnpm found, or a failed build, just prints a warning and installs
without it — or build the web app yourself first — `pnpm --dir
src/opengrader/web install --frozen-lockfile && pnpm --dir
src/opengrader/web build`. Either way `web/dist/` is copied with everything
else. `node_modules/` never is: `install.sh`/`.ps1` and the launch-time
refresh in `bootstrap.sh`/`.ps1` exclude it. A `dist/` built in the installed
copy is kept by both whenever the checkout has none of its own (with
rsync/robocopy; the `tar` fallback starts clean); a checkout that does have
one replaces it. A build made from older sources is no longer silently
served as current — the health panel (and *Open in browser*) detect it as
stale by comparing the sources' fingerprint against the stamp `build_web`
writes into `dist/`, falling back to file mtimes for a `dist/` built by hand
that carries no stamp.

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

# Installing Opengrader on your computer

There are two ways to run Opengrader. Pick the one that fits you — you can
also install both.

| | **Desktop app** | **`opengrader` terminal command** |
| --- | --- | --- |
| Best for | Instructors and TAs who'd rather not use a terminal | People who are comfortable typing commands |
| How you start it | Double-click the **Opengrader** icon on your Desktop | Type `opengrader` in any terminal window |
| What opens | The classic app: Tools & Settings, and a *Start grading* card for grading a class folder | Whatever command you type — for example `opengrader tui` opens a shared workspace |
| Sets itself up | Yes — installs everything it needs on first launch | No — expects Opengrader to already be set up (see the main [README](../README.md)) |

Neither one needs administrator rights. Everything is installed for your user
account only.

---

## Install the desktop app

**Before you start:** you need a copy of the Opengrader folder on your
computer (for example, downloaded with `git clone` — see the main
[README](../README.md#installation)).

**Without a terminal (macOS and Windows):** open the Opengrader folder and
double-click the installer for your system:

| System | Double-click |
| --- | --- |
| macOS | **Install Opengrader (Mac)** |
| Windows | **Install Opengrader (Windows)** — extract the ZIP first; it can't run from inside it |

Your computer will probably ask whether you trust it, because it was
downloaded and isn't signed:

- **macOS:** if it says the file can't be opened, go to **System Settings →
  Privacy & Security**, scroll down, and press **Open Anyway**. (On older
  versions of macOS: right-click the file and choose **Open**.) macOS may also
  ask to let Terminal use your Desktop folder — allow it, that's where the icon
  goes.
- **Windows:** if you see *Windows protected your PC*, press **More info**,
  then **Run anyway**.

**With a terminal (any system):** open a terminal in that folder and run the
one line for your system:

| System | Run this once |
| --- | --- |
| macOS or Linux | `bash packaging/install.sh` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install.ps1` |

That's it. An **Opengrader** icon appears on your Desktop (on macOS it is also
added to the *Applications* folder in your home folder; on Linux it also
appears in your apps menu).

### The first time you open it

Double-click the icon. A terminal window opens and shows a short checklist
while Opengrader gets itself ready:

1. **Updates its own files** from the folder you installed from, if that folder
   is still there.
2. **Installs uv**, the tool it uses to manage Python, if it's missing.
3. **Installs Python 3.12** for Opengrader's own use. Your computer's existing
   Python is never touched.
4. **Installs Opengrader's libraries.**
5. **Installs Tesseract**, the text-recognition program used to read scanned
   pages — automatically on Windows, and on macOS if Homebrew is installed. On Linux it asks
   before installing, because that needs your password. If it can't be
   installed, Opengrader still opens; only reading scans is unavailable until
   you install it. On a Mac you can do that from inside the app later: once
   Homebrew is installed, **Tools → Check this computer** shows an
   **Install Tesseract** button.
6. **Checks for Ollama**, the program that runs the AI model that reads
   handwriting. Ollama is **not** installed for you (its models are several GB),
   so if it's missing you'll see a note with the download link:
   <https://ollama.com>. Inside the app, **Tools → AI setup → Set up local AI**
   starts Ollama and downloads the models with the progress on screen — and on
   a Mac with Homebrew it can install Ollama too, after asking.

The first launch takes a minute or two. After that, every step is already
done, so Opengrader opens almost instantly.

### What you'll see

The icon opens the **classic app**: a *Start grading* card where you choose a
class folder to grade, plus Tools & Settings for every other task (checking your
computer's setup, AI settings, backups, and more). It grades your class folders
right where they are and doesn't need an internet connection.

**About the terminal window:** Opengrader shows students' scanned answers
inside the window. On macOS the icon uses the best terminal app you have
installed — Ghostty, iTerm2, WezTerm or kitty if present, otherwise the
built-in Terminal. The built-in Terminal can only show a rough preview of each
answer; press **`o`** while grading to open the full-quality image in Preview.

**Quitting:** when you quit Opengrader, its window closes too. If something
went wrong while it was starting, the window stays open so you can read the
message. (If you used *Open in browser*, the browser version keeps running
until you press **Stop** on the *Start grading* card.)

### Optional: the browser version

Opengrader can also show a class folder in your web browser. This is
completely optional and off by default. To turn it on, go to
**Tools → Browser app** and tick **Show Open in browser**.

The browser version has to be *built* once, which needs **Node.js**, a free
tool that comes with npm. The *Browser app* section tells you whether it's
installed, whether the browser version is built and up to date, and has
**Check again** and **Build / Rebuild** buttons. If you press **Open in
browser** before it's built, Opengrader offers to build it for you.

**Missing Node.js on a Mac?** If Homebrew is installed, the same
section shows an **Install Node.js** button, and **Open in browser**
offers **Install and build**. Nothing is installed until you press one of
them; it needs the internet and takes a few minutes. Without Homebrew the
section says what to install instead: Homebrew from <https://brew.sh> (it
asks for your Mac password), or Node.js from <https://nodejs.org>.

**Setting it up for someone else?** Build it before you install, so they never
see that step:

| System | Run this instead of the normal install |
| --- | --- |
| macOS or Linux | `bash packaging/install.sh --build-web` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install.ps1 -BuildWeb` |

If npm isn't found or the build fails, the install still finishes — just
without the browser version.

---

## Updating

- **If you still have the folder you installed from:** update that folder (for
  example with `git pull`), then just click the icon. It copies the new version
  in automatically every time it starts. (On macOS and Linux this uses a
  standard tool called `rsync`; if your system doesn't have it, run the
  installer again instead.)
- **If you deleted that folder:** the app keeps working as it is. To update,
  get a fresh copy of Opengrader and run the installer again.

## Uninstalling

Delete these files and folders:

| System | Delete |
| --- | --- |
| macOS | `~/Library/Application Support/Opengrader`, `~/Applications/Opengrader.app`, and the `Opengrader` shortcut on your Desktop |
| Linux | `~/.local/share/opengrader`, `~/.local/share/applications/opengrader.desktop`, and `opengrader.desktop` on your Desktop |
| Windows | `%LOCALAPPDATA%\Opengrader` and `Opengrader.lnk` on your Desktop |

(`~` means your home folder.) Your class folders and grades are stored
separately and are **not** deleted.

---

## Install the `opengrader` terminal command

This lets you type `opengrader doctor` (or any other Opengrader command) from
any folder. It adds **one** command, `opengrader`; every tool is reached through
it (`opengrader tui`, `opengrader run`, `opengrader split …`).

| System | Run this once, from the Opengrader folder | It installs to |
| --- | --- | --- |
| macOS or Linux | `sh packaging/install-command.sh` | `~/.local/bin/opengrader` |
| Windows | `powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1` | `%USERPROFILE%\.local\bin\` |

**If `opengrader` isn't found afterwards,** that folder isn't on your *PATH*
(the list of places your terminal looks for commands) yet:

- **macOS / Linux:** the installer prints the exact line to add. Paste it,
  then open a new terminal window.
- **Windows:** run the installer again with `-AddToPath` on the end and it
  adds the folder for you (for your account only), then open a new terminal
  window. If you have uv installed, this folder is usually on your PATH
  already.

To install somewhere else, set `OPENGRADER_BIN` to the folder you want before
running the installer.

**To remove it:** delete `~/.local/bin/opengrader` (Windows: `opengrader.cmd`
and `opengrader.ps1` in `%USERPROFILE%\.local\bin`).

### Moving the Opengrader folder is fine

The `opengrader` command doesn't remember a fixed location for the Opengrader
folder — it finds it each time it runs. So you never need to reinstall it when
you move the folder, rename it, make a second copy, or keep it on an external
drive whose name changes. (macOS adds a number when another drive with the same
name is already connected, so `/Volumes/MyDrive` can come back as
`/Volumes/MyDrive 1`.)

If you have more than one copy, the one you're currently inside wins. To always
use a specific copy, set `OPENGRADER_HOME`:

```bash
OPENGRADER_HOME=/path/to/opengrader opengrader doctor
```

### It works with or without uv

The command prefers uv (it can install anything that's missing), but it also
works with a Poetry environment, a normal Python virtual environment, a Conda
environment, or a Python you choose yourself with `OPENGRADER_PYTHON`. If it
can't find any working setup, it tells you the exact command to create one.

---

## Troubleshooting

| Problem | What to do |
| --- | --- |
| The desktop app stops on a step with an error | The window stays open and shows the last lines of its log. The full log is `opengrader-bootstrap.log` in your temporary folder (`$TMPDIR` on macOS/Linux, `%TEMP%` on Windows). |
| "Tesseract OCR not found" | Opengrader works, but can't read scanned pages. **On a Mac:** open Tools → *Check this computer*. If Homebrew is installed, press **Install Tesseract** and wait for the check to run again — no relaunch needed. If it isn't, the check says so: install Homebrew from <https://brew.sh> (it asks for your Mac password), then run the check again and the button appears. **Elsewhere:** install Tesseract with your package manager, then relaunch. |
| "Ollama not found" | Handwriting reading is unavailable. Install Ollama from <https://ollama.com>, then relaunch. |
| Is my computer set up correctly? | Run `opengrader doctor` (or open Tools → *Check this computer* in the app). It lists anything missing and how to fix it. |
| Scanned answers look blocky | Your terminal can't show full images. Press **`o`** while grading to open the image in your normal image viewer, or install a terminal such as iTerm2, Ghostty or WezTerm. `opengrader tui --check-graphics` shows what was detected. |
| `opengrader: command not found` | See *If `opengrader` isn't found afterwards* above. |
| The command is using the wrong copy of Opengrader | Run `OPENGRADER_WRAPPER_DEBUG=1 opengrader --version` to see which copy it picked and why, then set `OPENGRADER_HOME` to the one you want. |

## Changing the icon

Replace `packaging/icons/opengrader.png` (a 1024×1024 PNG with transparent
margins) and/or `packaging/icons/opengrader.ico` (Windows), then run the
installer again. To make the `.ico` from the PNG with Python's Pillow library:
`img.save("opengrader.ico", sizes=[(256,256),(48,48),(32,32),(16,16)])`.

---

## For developers: how it works

### Files in this folder

```
Install Opengrader (Mac).command     # double-click: runs packaging/install.sh
Install Opengrader (Windows).bat     # double-click: runs packaging\install.ps1
packaging/
├── install.sh            # one-time desktop-app installer (macOS, Linux)
├── install.ps1           # one-time desktop-app installer (Windows)
├── bootstrap.sh          # runs on every icon click: check/repair, then `opengrader classic` (macOS, Linux)
├── bootstrap.ps1         # the same, for Windows
├── Opengrader.command    # what the macOS icon opens: runs bootstrap.sh, then closes the window
├── opengrader            # the `opengrader` terminal command (finds its own checkout)
├── opengrader.ps1        # the same command, for Windows
├── opengrader.cmd        # Windows shim: .PS1 isn't in PATHEXT, so this is what PATH finds
├── install-command.sh    # puts the `opengrader` command on PATH (macOS, Linux)
├── install-command.ps1   # the same, for Windows
└── icons/
    ├── opengrader.png    # master icon (1024×1024, macOS/Linux)
    └── opengrader.ico    # Windows shortcut icon
```

### The desktop app

0. **The double-click installers** sit at the top of the checkout, where
   someone who unzipped a download will see them. Each only finds the folder
   it is in and runs `install.*` from there, then waits for a key so the
   result can be read. The `.command` must be executable **in git**
   (`git add --chmod=+x`): a ZIP download takes its file modes from the index,
   and a checkout on an exFAT drive reports every file as executable, which
   hides a missing bit. `install.sh` also removes the download quarantine mark
   from the two launchers it copies — `install(1)` carries it over, and the
   icon would otherwise be stopped by Gatekeeper on every click.
1. **`install.*` (run once)** copies the checkout into a per-user folder
   (`rsync`, or `tar` when rsync is missing; `robocopy` on Windows — `.git`,
   `.venv`, caches and `node_modules` excluded), records the checkout's path in
   `<install root>/source-path`, and creates the platform's shortcut:
   - **macOS:** `~/Library/Application Support/Opengrader` plus a real
     `~/Applications/Opengrader.app` bundle, symlinked onto the Desktop. The
     `.icns` is built at install time from `icons/opengrader.png` with the
     built-in `sips`/`iconutil`, so no binary icon is checked into git. The
     bundle's executable opens `Opengrader.command` in the first terminal it
     finds: Ghostty → iTerm2 → WezTerm → kitty → Terminal.app
     (graphics-capable terminals first). `Opengrader.command` is a real file
     here (it used to be generated): it runs `bootstrap.sh` and, after a
     clean exit in Terminal.app, asks Terminal to close the window that
     holds only this session — Terminal would otherwise leave it open on
     "[Process completed]". A failed launch keeps its window. kitty is
     started with `-o macos_quit_when_last_window_closed=yes`, so it leaves
     the Dock with its window. Only the icon takes this path; the
     `opengrader` command never closes a terminal.
   - **Linux:** `${XDG_DATA_HOME:-~/.local/share}/opengrader` plus a
     freedesktop `.desktop` entry in the apps menu and on the Desktop.
     `Terminal=true` makes the desktop environment open its default terminal.
   - **Windows:** `%LOCALAPPDATA%\Opengrader` plus a Desktop `.lnk` with the
     `.ico`, targeting Windows Terminal (`wt.exe`) when available, otherwise
     `powershell.exe`.
2. **`bootstrap.*` (every click)** runs each step behind a one-line progress
   display, with all tool output sent to `opengrader-bootstrap.log` in the temp
   folder; on failure it prints the log's last lines and waits for a keypress
   instead of closing the window. Steps: refresh files from `source-path` if
   that checkout still exists (macOS/Linux need `rsync` for this, otherwise it
   is skipped; the updated launcher is staged for the next run) → install `uv`
   (official installer, or winget) → `uv python install 3.12` (a uv-managed
   interpreter, matching the project's `>=3.12,<3.13` pin) →
   `uv sync --frozen --no-dev` (exactly what `uv.lock` records) → Tesseract,
   best effort (brew / winget; on Linux apt/dnf/pacman only after asking,
   since it needs sudo) → a note if Ollama is missing →
   `uv run --no-sync opengrader classic`. Every step is a no-op when already
   satisfied, so a normal launch adds well under a second.
3. **The browser app.** `--build-web` / `-BuildWeb` runs
   `npm ci && npm run build` in `src/opengrader/web`
   before copying (best effort: a missing npm or a failed build only warns).
   Building it yourself first works too:
   `npm --prefix src/opengrader/web ci && npm --prefix src/opengrader/web run build`.
   `web/dist/` is copied with everything else. When the checkout has no
   `dist/` of its own, a `dist/` built inside the installed copy is kept by
   both the installer and the launch-time refresh (the installer's `tar`
   fallback starts clean). A stale build is detected by comparing the web
   sources' fingerprint with the stamp `build_web` writes into `dist/` (file
   modification times for a hand-built `dist/` without a stamp). Builds started
   from inside the app are logged to `web-build.log` beside the AI settings,
   and Homebrew installs of Node.js to `tools-install.log` there
   (appended, so a failed try is still readable after a retry).

### The `opengrader` command

`packaging/opengrader` contains **no path to any checkout**; it finds one on
every run. A folder counts as a checkout when its `pyproject.toml` says
`name = "opengrader"` — never by folder name, which can be anything. First
match wins:

| # | Where it looks | Why |
| --- | --- | --- |
| 1 | `$OPENGRADER_HOME` | An explicit choice wins; a wrong one is an **error**, never a silent fallback |
| 2 | The checkout the script itself sits in | `packaging/opengrader doctor` works straight from a clone, uninstalled |
| 3 | Walking up from the current folder | Inside a checkout, that's the one you meant — this is what makes two clones behave |
| 4 | The path it found last time | Saved in `${XDG_CACHE_HOME:-~/.cache}/opengrader/checkout-path`; only a cache, so deleting it costs one search |
| 5 | A search | Each mounted volume, then `$HOME`, 5 folders deep; override with `OPENGRADER_SEARCH_ROOTS` |

Then it picks the first way of running Python that works:

| # | Runner | Why here |
| --- | --- | --- |
| 1 | `$OPENGRADER_PYTHON` | An interpreter you name yourself; skips uv entirely |
| 2 | A project venv **another tool built** (no `uv =` line in its `pyvenv.cfg`) | Used directly and never handed to uv, because `uv run` would sync a Poetry-managed venv against `uv.lock` and take it over |
| 3 | `uv run --project` | The project's own setup, and the only runner that *installs* what's missing rather than failing |
| 4 | A uv-made project venv | For a machine where uv has since been removed |
| 5 | Poetry (`poetry env info --path`) | Poetry 2.x reads this project's PEP 621 metadata natively |
| 6 | `$VIRTUAL_ENV` / `$CONDA_PREFIX` | Whatever is activated (Conda doesn't set `VIRTUAL_ENV`, hence both) |
| 7 | A `python3` on PATH that can already import it | Covers `pipx` and a plain `pip install -e .` |

Everything except uv is run as `<python> -m opengrader`, never through the
venv's `bin/opengrader` script: those scripts hard-code the absolute path of
the Python they were installed with, so they break as soon as the folder moves
or its drive is renamed. `python -m` carries no such path, which is why
`src/opengrader/__main__.py` exists.

On Windows, `opengrader.cmd` exists because a `.ps1` can't be run by name
(`.PS1` isn't in `PATHEXT`). It only hands off to `opengrader.ps1` beside it
with `-NoProfile` and a process-only `-ExecutionPolicy Bypass`, so the default
policy doesn't refuse a copied, unsigned script.

Both launchers implement the same two lists, so **a change to one belongs in
the other**. Where they have to differ:

| | macOS / Linux (`opengrader`) | Windows (`opengrader.ps1`) |
| --- | --- | --- |
| Python inside a venv | `bin/python` | `Scripts\python.exe` |
| Search roots | `/Volumes/*`, then `$HOME` | `%USERPROFILE%`, then each ready drive, then the system drive (shallow) |
| Loops it skips | Symlinked volumes (`/Volumes/Macintosh HD` → `/`) | Reparse points / junctions (`C:\Users\All Users`) |
| Reporting an error from the finder | Must `return`, never `exit` — it runs in a command substitution, where `exit` only ends the subshell | `exit` is fine |
| Remembered path | `${XDG_CACHE_HOME:-~/.cache}/opengrader/checkout-path` | `%LOCALAPPDATA%\opengrader\checkout-path` |
| `OPENGRADER_SEARCH_ROOTS` separator | `:` | `;` |

`OPENGRADER_WRAPPER_DEBUG=1` makes either one print which rule matched.

### Tests

Tests stub `uv`, so they check *which checkout and runner were chosen* without
running anything: `tests/unit/packaging/test_opengrader_wrapper.py` for the
macOS/Linux launcher and `test_opengrader_wrapper_windows.py` for the
PowerShell one. The Windows test runs under `pwsh` on any OS and is skipped
where PowerShell isn't installed, so in practice it runs in CI
(`ubuntu-latest`). The Windows-only runner rules (`Scripts\python.exe`,
`Lib\site-packages`, `$env:CONDA_PREFIX`) can't be reached outside Windows and
are **not** covered.

### Checking on a real Windows machine

`opengrader.ps1` has been reviewed but not yet run on real Windows. Run these
in order; the first failure is the one to fix:

```powershell
# 0. It parses at all. Nothing below matters if this reports a ParserError.
powershell -NoProfile -ExecutionPolicy Bypass -Command `
    "$null = [System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw packaging\opengrader.ps1), [ref]$null); 'parsed'"

# 1. Run it in place, before installing anything.
powershell -ExecutionPolicy Bypass -File packaging\opengrader.ps1 --version
$env:OPENGRADER_WRAPPER_DEBUG = 1
powershell -ExecutionPolicy Bypass -File packaging\opengrader.ps1 --version   # says which rule matched

# 2. Install, then use it from somewhere else entirely (open a new terminal first).
powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1 -AddToPath
cd \; opengrader doctor

# 3. The Windows venv layout, which CI can't reach.
cd <checkout>; uv sync              # or: poetry install
cd \; $env:OPENGRADER_WRAPPER_DEBUG = 1; opengrader --version
#   expect: "running with uv"
$env:OPENGRADER_PYTHON = "<checkout>\.venv\Scripts\python.exe"
opengrader --version                #   expect: "running with $env:OPENGRADER_PYTHON"
Remove-Item Env:\OPENGRADER_PYTHON

# 4. uv absent — the reason the runner list exists. In a shell with uv off PATH:
$env:Path = ($env:Path -split ';' | Where-Object { $_ -notmatch 'uv' }) -join ';'
opengrader --version                #   expect: "running with ...\.venv (uv not installed)"

# 5. Output stays clean for scripts (nothing written with Write-Host leaks into it).
opengrader commands --json | ConvertFrom-Json | Select-Object -First 1

# 6. The grading app gets a real console: keyboard and images, not a pipe.
opengrader tui --check-graphics
```

Most likely to break: step 0 (a parse error), then the `Join-Path` calls that
build `Scripts\python.exe` paths in step 3, then the `-Filter` patterns in
`Test-VenvHasPackage`. If step 2 installs but `opengrader` still isn't found,
check that `%USERPROFILE%\.local\bin` is really on PATH and that both
`opengrader.cmd` and `opengrader.ps1` are there — the `.cmd` is the one PATH
can find.

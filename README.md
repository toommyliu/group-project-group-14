# Opengrader

> **Grade paper assignments faster, right on your own computer.** Print an assignment, scan the stack, and grade it question by question, without sending student work to anyone else's server.

**Quick links:** [Install](#installation) · [Run it](#running-the-prototype) · [Demo](#demo) · [Tests](#testing)

---

## Team

| Name | GitHub | Email |
| --- | --- | --- |
| Andy Cortes | [@andycortes23](https://github.com/andycortes23) | andy.cortes@sjsu.edu |
| Arjun Gill | [@arjung04](https://github.com/arjung04) | arjun.gill@sjsu.edu |
| Tommy Liu | [@toommyliu](https://github.com/toommyliu) | tommy.liu@sjsu.edu |
| Marco Lopez | [@TheMarco408](https://github.com/TheMarco408) | marco.lopez@sjsu.edu |

**Advisor:** [Dr. Carlos Rojas](mailto:carlos.rojas@sjsu.edu)

---

## Project Description

Grading paper assignments is slow, and the tools that speed it up (like Gradescope) are cloud services that are expensive, rigid, and require uploading student work to a third party. **Opengrader** is a local, offline-first grading toolkit: it turns a markdown answer key into a printable assignment, splits and reads the scanned submissions, and gives instructors and TAs a fast question-by-question rubric grader in the terminal or the browser, with optional AI help that runs on your own machine by default.

---

## Prototype Scope

### Works end to end today

| Step | What you can do |
| --- | --- |
| **1. Answer key** | Write a `qa.md` answer key ([format guide](docs/QA_file.md)), or draft one from an existing Word, text, or PDF answer key with `create-answer-key`. |
| **2. Printable assignment** | Generate assignment PDFs with answer boxes, multiple versions, optional QR codes, and bubble sheets. |
| **3. Scan intake** | Check a scanned stack for rotated, blank, or missing pages, split it into one PDF per student, and import phone photos. |
| **4. Reading answers** | Crop every answer and read each student's name and ID (Tesseract / PP-OCR, plus an optional local vision model for handwriting). |
| **5. Grading** | Grade question by question in the terminal app (images shown inline) or the web app, with rubrics, comments, undo, and anonymous grading on by default. |
| **6. Auto-grading** | Score multiple choice, true/false, and numeric answers automatically; an AI model can suggest grades for written answers, which a human reviews. |
| **7. Hand back** | Write grades and comments onto each student's PDF, export a gradebook CSV, or push scores to Canvas. |

`opengrader run` chains steps 3–6 into one resumable command. The desktop app (`opengrader classic`) gives you every step as buttons.

### Incomplete or rough

- **Windows and Linux** installers and password-store support have been written but not yet tested on real Windows/Linux machines. macOS is the tested platform.
- **Multi-grader web app:** Google sign-in and shared workspaces work, but haven't been tried by a real course staff yet.
- **Cloud AI providers** are opt-in and work, but several hardening items remain (for example, answer crops are not yet masked before a cloud send).
- **Handwritten math** recognition (`math` extra) is experimental.

### Left for the Expo

See [What's Next](#whats-next).

---

## System Architecture

Opengrader is a **pipeline of small command-line tools** that pass plain files to each other (markdown and image crops), ending in a grading app. All the core logic lives in one Python package; the terminal app, the web app, and the CLI are thin front ends over it.

```mermaid
flowchart LR
    subgraph Prepare
        AK["Answer key<br/>(.md / .docx / .pdf)"] -->|create-answer-key| QA["qa.md"]
        QA -->|qa-file generate-template| TPL["Printable assignment PDF"]
    end

    subgraph Ingest
        SCAN["Scanned stack PDF"] -->|scan-check + submission-splitter| SPLIT["One PDF per student"]
        SPLIT -->|"submission-extractor<br/>(OCR + optional local AI)"| SUB["student.submission.md<br/>+ answer crops"]
    end

    subgraph Grade
        SUB --> APP["Grading app<br/>Terminal (Textual) · Browser (React)"]
        QA --> APP
        QA -->|automated-grading| SUB
        APP -->|grades, comments| SUB
    end

    subgraph Return
        SUB --> OUT["Annotated PDFs · CSV gradebook · Canvas"]
    end

    TPL -. printed and handed out .-> SCAN
```

| Component | Folder | What it does |
| --- | --- | --- |
| **Core library** | `src/opengrader/core/` | All domain logic: answer-key parsing, scoring, PDF generation, OCR, splitting, auto-grading, exports. Never prints or draws UI. |
| **CLI tools** | `src/opengrader/cli/` | One thin command per pipeline step, all reachable through `opengrader <command>`. |
| **Terminal app** | `src/opengrader/tui/` | The Textual grading app plus the *Tools & Settings* screens. |
| **Local service** | `src/opengrader/server/` | A FastAPI service bound to `localhost` that stores a shared grading workspace in SQLite. |
| **Web app** | `src/opengrader/web/` | A React/Vite browser client for the same workspace. |
| **Installers** | `packaging/` | Double-click installers and self-updating launchers for macOS, Windows, and Linux. |
| **Local AI (optional)** | external | An [Ollama](https://ollama.com/) server on `127.0.0.1:11434` for handwriting reading and grade suggestions. |

**Two ways to store grades:**

- **Folder mode** (default): no database. Grades are saved straight into each student's `.submission.md` file.
- **Workspace mode**: a local SQLite file and a localhost-only service, so the terminal and browser apps (and several graders) can share live grading.

---

## Prerequisites

| Requirement | Version | Needed for | Notes |
| --- | --- | --- | --- |
| **macOS** (recommended), Windows, or Linux | macOS (tested) | Everything | Windows/Linux are untested. |
| [Python](https://www.python.org/) | **3.12 exactly** (`>=3.12,<3.13`) | Everything | `uv` can install it for you. |
| [uv](https://docs.astral.sh/uv/) | 0.5 or newer (tested on 0.12) | Everything | Python package/project manager. |
| [Git](https://git-scm.com/) | any | Cloning the repo | |
| [Tesseract OCR](https://tesseract-ocr.github.io/tessdoc/Installation.html) | 5.x (tested on 5.5) | Reading scans | Without it, scans extract to empty answers. |
| [Homebrew](https://brew.sh/) | any | Installing the above on macOS | |
| [Ollama](https://ollama.com/) | recent release (tested on 0.23) | *Optional:* handwriting reading and AI grade suggestions | The two default models need several GB of disk. |
| [Node.js](https://nodejs.org/) (includes npm) | 22 or newer (CI uses 24) | *Optional:* building the web app, once | |
| A scanner or phone camera | — | Bringing in real submissions | Sample scans are included in `samples/` so you can try it without one. |
| Canvas account + [`canvas-sak`](https://pypi.org/project/canvas-sak/) setup | — | *Optional:* pushing grades to Canvas | |
| Google Cloud OAuth client | — | *Optional:* sign-in for the shared web app | Not needed for local use. |

**Hardware:** any recent laptop works for grading. For local AI, 16 GB of RAM is recommended (Apple Silicon or a GPU makes it much faster).

<details>
<summary><b>Which terminal should I use?</b> (any works; some show sharper images)</summary>

Opengrader shows each student's scanned answer *inside* the terminal and picks the best image protocol automatically.

| Image quality | Terminals |
| --- | --- |
| Full resolution (Kitty protocol) | [Ghostty](https://ghostty.org/), [kitty](https://sw.kovidgoyal.net/kitty/), Contour |
| Full resolution (iTerm2 protocol) | [iTerm2](https://iterm2.com/), [WezTerm](https://wezfurlong.org/wezterm/), VS Code* |
| Full resolution (Sixel) | Konsole, foot, mlterm, Windows Terminal 1.22+, Rio, XTerm |
| Coarse preview | macOS Terminal.app, GNOME Terminal, PuTTY, tmux |

In a coarse-preview terminal, press **`o`** while grading to open the full-resolution image in your normal image viewer.

\* VS Code needs the `terminal.integrated.enableImages` setting ([details](https://code.visualstudio.com/docs/terminal/advanced#_image-support)).

Check what was detected (and include this in any bug report):

```bash
uv run tui --check-graphics
```

</details>

---

## Installation

### Option A: Desktop app (easiest, no terminal knowledge needed)

1. Download or clone this repository (see step 2 below).
2. Double-click **`Install Opengrader (Mac).command`** (or **`Install Opengrader (Windows).bat`**) in the project folder.
3. An **Opengrader** icon appears on your Desktop. Double-click it. The first launch takes a minute or two while it installs `uv`, Python 3.12, and dependencies; later launches are instant.

See [packaging/README.md](packaging/README.md) for details and uninstall steps.

### Option B: Developer setup (from a clean Mac)

1. **Install Homebrew** (skip if you already have it):
   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. **Install the system tools:**
   ```bash
   brew install git python@3.12 uv tesseract
   brew install ollama node      # optional: local AI and the web app
   ```

3. **Clone the repository:**
   ```bash
   git clone https://github.com/SJSU-CMPE-195/group-project-group-14.git opengrader
   cd opengrader
   ```

4. **Install the Python packages:**
   ```bash
   uv sync --all-groups --extra pydantic-ai --extra photos
   ```

   > [!WARNING]
   > Always use this full command. A plain `uv sync` **removes** the extras, and cloud-provider features (and some tests) stop working until you re-run it.

   | Extra | Adds | Needed for |
   | --- | --- | --- |
   | `pydantic-ai` | Cloud AI clients (Anthropic, OpenAI, Google, …) | Cloud AI providers; part of the test suite |
   | `photos` | iPhone HEIC photo support | `photo-import` with HEIC files |
   | `math` | Handwritten-math recognition (installs PyTorch, large) | Reading handwritten math as LaTeX |

5. **Check your setup.** `doctor` tells you what is missing and the exact command to fix it:
   ```bash
   uv run opengrader doctor
   ```

6. *(Optional)* **Set up local AI.** Either open the desktop app → **Tools → AI setup → Set up local AI** (it downloads the models for you), or by hand:
   ```bash
   ollama serve &                 # start the Ollama server
   ollama pull qwen3.5:9b         # handwriting reader
   ollama pull qwen2.5vl:7b       # grade suggestions
   ```

7. *(Optional)* **Build the web app** (needed once):
   ```bash
   npm --prefix src/opengrader/web ci
   npm --prefix src/opengrader/web run build
   ```

8. *(Optional)* **Install the `opengrader` command globally** so you can type `opengrader …` from any folder instead of `uv run opengrader …`:
   ```bash
   sh packaging/install-command.sh                                          # macOS / Linux
   powershell -ExecutionPolicy Bypass -File packaging\install-command.ps1   # Windows
   ```

### Environment variables

**None are required for local use.** Everything below is optional. Never commit real values.

**Web app sign-in** (only for shared, multi-grader hosting). Copy [`.env.example`](.env.example) to `~/.opengrader/server.env` and fill it in:

| Variable | Purpose |
| --- | --- |
| `OPENGRADER_AUTH_ENABLED` | Turns Google sign-in on. Off by default (local use, no sign-in). |
| `OPENGRADER_GOOGLE_CLIENT_ID` | Google OAuth client ID. Required when sign-in is on. |
| `OPENGRADER_GOOGLE_CLIENT_SECRET` | Google OAuth client secret. Required when sign-in is on. |
| `OPENGRADER_SESSION_SECRET` | Random secret that signs login cookies (generate with `openssl rand -hex 48`). |
| `OPENGRADER_GOOGLE_DOMAIN` | Google Workspace domain allowed to sign in. |
| `OPENGRADER_ADMINS` | Comma-separated emails of admins who can create courses. |
| `OPENGRADER_SECURE_COOKIE` | Use secure cookies when served over HTTPS. |
| `OPENGRADER_SERVER_HOME` | Folder for the service's data and `server.env` (default `~/.opengrader`). |

**AI and app behavior:**

| Variable | Purpose |
| --- | --- |
| *Provider API key* (e.g. `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`) | Key for a cloud AI provider you opted into. You can instead paste it in the app, which stores it in your OS password store (macOS Keychain, etc.), never in a project file. |
| `OPENGRADER_AI_ALLOW_CLOUD` | Set to `0` to forbid every cloud AI send on this machine (kill switch). |
| `OPENGRADER_GRAPHICS` | Force a terminal image protocol: `kitty`, `iterm2`, `sixel`, `halfcell`, or `unicode`. |
| `OPENGRADER_IMAGE_VIEWER` | Program used by `o` to open an image, e.g. `"feh {}"`. |
| `OPENGRADER_HOME` | Which checkout the global `opengrader` command should run. |
| `OPENGRADER_PYTHON` | Which Python interpreter the global `opengrader` command should use. |

---

## Running the Prototype

### Try it in 30 seconds with sample data

The `samples/` folder has real scanned quizzes. **Copy a sample first**, since grading writes into the folder:

```bash
cp -R samples/cs146-quiz1 ~/Desktop/cs146-quiz1
uv run tui --qa-file ~/Desktop/cs146-quiz1/qa.md --submissions ~/Desktop/cs146-quiz1/student-submissions/
```

Press **`enter`** to open a question, **`1`–`9`** to pick a rubric option, **`j`/`k`** for the next/previous question, and **`?`** for every shortcut.

### The desktop app (all tools, as buttons)

```bash
uv run opengrader classic
```

This is what the desktop icon opens. Pick a class folder (with a scan and an answer key) on the *Start grading* card and press **Launch Grading**. If the folder hasn't been processed yet, Opengrader offers to check, split, and read the scans for you first.

### One command: scan in, graded work out

```bash
uv run opengrader run path/to/assignment-folder/
```

Point it at a folder holding the scanned PDF and the answer key (and optionally a roster and a blank template). It checks the scan, splits it, reads every answer, auto-grades what it can, and opens the grading app. Run the same command again and it picks up where it left off. Add `--dry-run` to preview the plan, or `--llm --auto-ollama` to let local AI suggest grades for written answers.

### The web app

```bash
uv run opengrader web      # starts the local service and opens your browser
uv run opengrader stop     # stops the service
```

From the desktop app you can also tick *Show Open in browser* under **Tools → Browser app** and use the **Open in browser** button.

<details>
<summary><b>Running each step by hand</b> (power users)</summary>

```bash
# 0. (Optional) Draft a QA answer key from an existing key
uv run opengrader create-answer-key --input answer-key.pdf --output qa.md

# 1. Generate the printable assignment
uv run opengrader qa-file generate-template --qa-file qa.md --output-pdf template.pdf

# 2. Check and split the scanned stack
uv run opengrader scan-check --input scans/all.pdf --pages-per-submission 6
uv run opengrader submission-splitter --input scans/all.pdf --output split/ --fix-rotation

# 3. Read each student's answers (add --llm-responses to transcribe handwriting with local AI)
uv run opengrader submission-extractor --input split/ --output student-submissions/ --qa-file qa.md

# 4. Auto-grade what can be matched (add --llm for AI suggestions on written answers)
uv run opengrader automated-grading --solutions qa.md --input student-submissions/

# 5. Grade the rest
uv run opengrader tui --qa-file qa.md --submissions student-submissions/

# 6. Send grades to Canvas (preview first)
uv run opengrader canvas-export --submissions student-submissions/ --course "CS146" --assignment "Quiz 1" --dry-run
```

Find any command and its options:

```bash
uv run opengrader --help              # commands grouped by workflow step
uv run opengrader commands --all      # every command, including the ones the desktop app covers
uv run opengrader help <command>      # one command's options
```

Short aliases: `split`, `extract`, `auto-grade`, `answer-key`, `grade` (= `tui`), `pipeline` (= `run`).

</details>

<details>
<summary><b>Where are my settings stored?</b></summary>

AI settings are saved once in `config.json` in your user config folder:

- macOS: `~/Library/Application Support/Opengrader/`
- Linux: `~/.config/opengrader/`
- Windows: `%APPDATA%\Opengrader\`

Edit them from the desktop app under **Tools → AI setup**. Command-line flags override saved settings, which override the built-in defaults. API keys are never stored here; they go in your OS password store.

</details>

---

## Testing

Opengrader has **~250 test files** run with [pytest](https://docs.pytest.org/), plus browser tests run with [Playwright](https://playwright.dev/python/). Both run automatically on every push and pull request to `main` through [GitHub Actions](.github/workflows/tests.yml).

**Run the unit and integration tests** (about 4 minutes):

```bash
uv sync --all-groups --extra pydantic-ai --extra photos   # make sure the extras are installed
uv run pytest
```

**Run just one area** (much faster while developing):

```bash
uv run pytest tests/unit/core            # e.g. only the core library
uv run pytest tests/unit/core/test_curve.py
```

**Run the browser tests** (needs the web app built, see [Installation](#installation) step 7):

```bash
uv sync --group browser
uv run --group browser playwright install chromium
uv run --group browser pytest browser_tests --browser chromium
```

**What the tests cover:**

| Folder | Files | Covers |
| --- | --- | --- |
| `tests/unit/core/` | 118 | Answer-key parsing and round-trips, scoring, PDF generation, splitting, extraction, auto-grading, AI safeguards (no student names sent to the cloud), exports, backups, encryption |
| `tests/unit/tui/` | 63 | The terminal app driven headlessly: key bindings, grading flow, screens, startup speed |
| `tests/unit/cli/` | 36 | Every command-line tool's flags and outputs; the command registry stays in sync |
| `tests/unit/server/` | 17 | The local web service's API, workspaces, sign-in, folder sessions |
| `tests/unit/ocr/` | 15 | Page layout detection, OCR, QR codes, bubble sheets |
| `tests/unit/packaging/` | 5 | Installers and launchers; every runtime dependency ships in the desktop app |
| `browser_tests/` | 6 flows | Uploading a scan, importing an answer key, saving grades, live updates across tabs, conflict handling, CSV export |

Tests use the real scanned quizzes in `samples/` as fixtures.

---

## Demo

**Demo video:** _Coming soon. The link will be added here._

### Screenshots

_Coming soon. Screenshots of the generated assignment, the scan splitter, a student submission file, and the grading app will be added here._

---

## Technical Stack

| Area | Technologies |
| --- | --- |
| **Language** | Python 3.12 (managed with [uv](https://docs.astral.sh/uv/)), TypeScript |
| **Terminal app** | [Textual](https://textual.textualize.io/), [Rich](https://rich.readthedocs.io/), textual-image |
| **Command line** | [Click](https://click.palletsprojects.com/) |
| **Web app** | [FastAPI](https://fastapi.tiangolo.com/) + uvicorn, [React](https://react.dev/) + [Vite](https://vite.dev/) + TanStack Router/Query, SQLite |
| **PDF processing** | [PyMuPDF](https://pymupdf.readthedocs.io/), [pypdf](https://pypdf.readthedocs.io/), segno (QR codes) |
| **OCR and imaging** | [Tesseract](https://tesseract-ocr.github.io/), [RapidOCR](https://github.com/RapidAI/RapidOCR) (PaddleOCR PP-OCR models on ONNX Runtime), [OpenCV](https://opencv.org/), Pillow, NumPy |
| **AI (optional)** | [Ollama](https://ollama.com/) running local models (`qwen3.5:9b`, `qwen2.5vl:7b`); opt-in cloud providers through [Pydantic AI](https://ai.pydantic.dev/) |
| **Integrations** | Canvas LMS via `canvas-sak`, Google sign-in via Authlib |
| **Security** | `cryptography` (AES-256-GCM encrypted backups), OS password stores for API keys |
| **Testing and CI** | pytest, Playwright, GitHub Actions |
| **Hardware** | Any document scanner or phone camera; a laptop (16 GB RAM recommended for local AI) |

---

## What's Next

Before the **Project Expo on December 11**, we plan to:

- [ ] **Test on Windows and Linux**: run the installers, launchers, and password-store support on real machines and fix what breaks.
- [ ] **Pilot with real course staff**: grade a real quiz with several TAs using the shared web workspace and Google sign-in, and fix the rough edges they hit.
- [ ] **Close the remaining AI safety gaps**: mask the name/ID area out of answer crops before any cloud send, and limit the size of model responses.
- [ ] **Measure accuracy**: report how often handwriting reading and AI grade suggestions agree with human graders (using `llm-eval` and `transcribe-eval`).
- [ ] **Polish the Expo demo**: a one-click demo folder, an updated demo video, and a poster.
- [ ] **Choose and add a license.**

---

## Acknowledgments

- **Dr. Carlos Rojas**, Advisor, for guidance and support throughout the project
- **Dr. Ben Reed**, for sharing ideas and experiences, and providing valuable feedback

---

_CMPE 195A/B Senior Design Project · San José State University · 2026_

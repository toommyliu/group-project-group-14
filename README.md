# Opengrader

>

## Team

| Name        | GitHub                                           | Email                |
| ----------- | ------------------------------------------------ | -------------------- |
| Marco Lopez | [@TheMarco408](https://github.com/username)      | marco.lopez@sjsu.edu |
| Tommy Liu   | [@toommyliu](https://github.com/toommyliu)       | tommy.liu@sjsu.edu   |
| Andy Cortes | [@andycortes23](https://github.com/andycortes23) | andy.cortes@sjsu.edu |
| Arjun Gill  | [@arjung04](https://github.com/arjung04)         | arjun.gill@sjsu.edu  |

**Advisor:** [Dr. Carlos Rojas](mailto:carlos.rojas@sjsu.edu)

---

## Project Description

Opengrader is a local, offline-first exam-grading toolchain — think Gradescope, but on your own machine. Existing grading solutions aren't flexible enough to enable educators to grade assignments efficiently. Timely, quality feedback helps improve student learning outcomes, and yet, today's tools fail to meet these needs.

## Capabilities

- **Template generation** — turn a QA-format markdown answer key into printable exam PDFs (Computer Modern fonts, answer boxes, multi-version exams). Draft the QA file itself from an existing answer-key PDF or a text2qti exam.
- **Scan ingestion** — split a bulk scanner PDF into per-student PDFs, fix out-of-order pages, and extract each response via OCR (Tesseract + OpenCV) and a local vision LLM (Ollama) into structured per-student markdown files with response image crops.
- **Grading TUI** — Gradescope-style question-by-question rubric grading in the terminal: in-terminal response images, rubric editing, per-question statistics, anonymous grading by default (teacher mode reveals names), and automatic write-back of grades to submission files and annotated PDFs.
- **Automated grading** — auto-score submissions against a solutions file (exact answer matching; unmatched free responses are left for manual grading).
- **Canvas export** — push scores and per-question comments to Canvas LMS via `canvas-sak`, from the CLI or from inside the TUI.
- **Offline-first** — no cloud APIs, no database; all state lives in files. The only network use is the localhost Ollama server and optional Canvas export.

## Prerequisites

- [Python v3.12](https://www.python.org/)
- [Homebrew](https://brew.sh/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
- [Ollama](https://ollama.com/)
- Compatible Terminal Emulator

### Terminal Compatibility

For images to render in the TUI correctly[^1], you may need a terminal emulator that supports the
[Kitty Graphics Protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/).

On macOS, the following terminal emulators were tested and work correctly:

- [Ghostty](https://github.com/ghostty-org/ghostty)
- [iTerm2](https://iterm2.com/)
- [WezTerm](https://wezfurlong.org/wezterm/)

> [!NOTE]
> Visual Studio Code's integrated terminal should work as of [version 1.110](https://code.visualstudio.com/updates/v1_110#_kitty-graphics-protocol). Enable the `terminal.integrated.enableImages` setting. See [Image Support](https://code.visualstudio.com/docs/terminal/advanced#_image-support) for details.

[^1]: https://github.com/lnqs/textual-image#supported-terminals

## Installation

Install homebrew and dependencies:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install tesseract
brew install uv
brew install python@3.12
brew install ollama
```

Clone the repository:
```bash
git clone https://github.com/SJSU-CMPE-195/group-project-group-14.git opengrader
cd opengrader
```

Set up a Python virtual environment and install packages:
```bash
uv sync --all-groups
```

### Desktop App (Optional)

To create a double-clickable **Opengrader** icon on your Desktop, run once from the checkout:

```bash
bash packaging/install.sh                                        # macOS / Linux
powershell -ExecutionPolicy Bypass -File packaging\install.ps1   # Windows
```

The icon opens a terminal window and launches the TUI, quietly installing/repairing `uv`, Python 3.12, and dependencies on the way (first launch takes a minute or two; later launches are instant). While this checkout exists, every launch also re-syncs the installed copy from it, so the icon always runs the latest code. See [packaging/README.md](packaging/README.md) for details and uninstall steps.

## Usage Guide

Run CLI tools:
```bash
uv run <script_name> [arguments]
```

Run the TUI:
```bash
uv run tui --qa-file /path/to/qa.md --submissions /path/to/student-submissions
```

<details>
<summary>Available Scripts</summary>

- `tui`: Launch the TUI. Optionally pass `--qa-file` and `--submissions` together to load real files.
- `tui-dev`: Launch the TUI with Textual devtools enabled.
- `qa-file`: Work with QA markdown files.
  - `validate --qa-file <qa.md>`: Validate QA markdown structure.
  - `generate-template --qa-file <qa.md> --output-pdf <template.pdf>`: Generate template PDFs from QA markdown.
- `submission-splitter`: Split one scanned PDF containing many students into per-student PDFs.
- `submission-extractor`: Convert per-student submission PDFs into `.submission.md` files and response image crops using digital text, templates, or OCR.
- `pdf-to-qa-solutions`: Convert an answer-key PDF into a QA markdown draft using OCR.
- `text2qti-to-qa`: Convert a [text2qti](https://github.com/gpoore/text2qti) exam file into QA markdown.
- `automated-grading`: Update student `.submission.md` files from a solutions markdown file.
- `canvas-export`: Push scores and per-question comments from `.submission.md` files to Canvas LMS (requires `canvas-sak` setup; supports `--dry-run`).

</details>

> [!TIP]
> Every script supports `--help` to show its current options.

#### CLI Reference

Validate a QA markdown file:
```bash
uv run qa-file validate --qa-file samples/qa-single.md
```

Generate a template PDF from a QA markdown file:
```bash
uv run qa-file generate-template \
  --qa-file samples/qa-single.md \
  --output-pdf out/template-single.pdf
```

Useful `qa-file generate-template` options:
- `--version <ID>`: Generate a specific template version.
- `--all-versions`: Generate one template per QA version. Output filenames get a `-v<ID>` suffix.
- `--title <title>`: Override the QA file title. Required if the QA file has no title.
- `--instructions <text>`: Override the QA file instructions.
- `--continued-page-header <text>`: Override the continued page header.
- `--questions-start-page 1|2`: Start questions on page 1 or page 2.
- `--font-regular-file <path>` and `--font-bold-file <path>`: Use custom template fonts.

Generated templates use Computer Modern by default.

Split a scanned submissions PDF:
```bash
uv run submission-splitter \
  --input scans/all-submissions.pdf \
  --output split/ \
  --pages-per-submission 6
```

Useful `submission-splitter` options:
- `--start-page <N>` and `--end-page <N>`: Split only a page range, using 1-indexed page numbers.
- `--filename-prefix <prefix>`: Change output names from `submission-0001.pdf`.
- `--zero-pad-width <N>`: Change output number padding.
- `--dry-run`: Print planned split ranges without writing files.

Convert per-student PDFs into student submission markdown:
```bash
uv run submission-extractor \
  --input split/ \
  --output out/student-submissions/
```

Useful `submission-extractor` options:
- `--dpi <N>`: Set rendering DPI for OCR and cropping. Default is `300`.
- `--confidence-threshold <N>`: Set OCR field validation threshold. Default is `60.0`.
- `--lang <codes>`: Set Tesseract layout language codes. Default is `eng`.
- `--no-deskew`: Disable automatic deskewing.
- `--no-split-subquestions`: Keep nested subquestion boxes in one response image.
- `--ollama-model <model>`: Set the Ollama vision model. Default is `qwen2.5vl:7b`.
- `--ollama-host <url>`: Set the Ollama server URL. Default is `http://127.0.0.1:11434`.
- `--ollama-timeout <seconds>`: Set the Ollama request timeout. Default is `120`.
- `--no-ollama-qa`: Skip Ollama question prompt/answer extraction while still using it for name, ID, and title extraction.
- `--save-layout-debug`: Write layout JSON and overlay PNGs for each submission.

Convert an answer key PDF into a QA markdown draft:
```bash
uv run pdf-to-qa-solutions \
  --input samples/exam2b-2-answers.pdf \
  --output out/qa.md \
  --title "Midterm 1 Solutions"
```

Automatically grade student submission markdown files from a solutions file:
```bash
uv run automated-grading \
  --solutions solutions.md \
  --input out/student-submissions/
```

Export grades and comments to Canvas (preview with `--dry-run` first):
```bash
uv run canvas-export \
  --submissions out/student-submissions/ \
  --course "CS146" \
  --assignment "Quiz 1" \
  --dry-run
```

#### Example Usage Flow

0. (Optional) Convert an existing answer key PDF to a QA file:
```bash
uv run pdf-to-qa-solutions --input /path/to/answer-key.pdf --output /path/to/qa.md
```

1. Generate a template from a QA file:
```bash
uv run qa-file generate-template --qa-file /path/to/qa.md --output-pdf /path/to/template.pdf
```

2. Process scan into individual student submissions:
```bash
uv run submission-splitter --input /path/to/scan.pdf --output /path/to/split --pages-per-submission N
```

3. Process student submissions into structured markdown:
```bash
uv run submission-extractor --input /path/to/split --output /path/to/student-submissions
```

Run a vision language model to extract printed and handwritten text from the processed submissions:

Terminal 1: install the Ollama vision model and keep it running:
```bash
# Download the model.
ollama pull qwen2.5vl:7b
# Start the Ollama server.
ollama serve
```

Terminal 2: process the submissions with the model:
```bash
uv run submission-extractor \
  --input samples/cs146-midterm1/split-submissions \
  --output samples/cs146-midterm1/student-submissions \
  --ollama-model qwen2.5vl:7b
```

If `ollama run` cannot connect to Ollama, start the Ollama app or run `ollama serve`
before processing submissions.

4. (Optional) Auto-score what can be matched against a solutions file:
```bash
uv run automated-grading --solutions /path/to/solutions.md --input /path/to/student-submissions
```

5. Launch the TUI with the generated files:
```bash
uv run tui \
  --qa-file /path/to/qa.md \
  --submissions /path/to/student-submissions
```

6. (Optional) Export grades to Canvas when grading is done:
```bash
uv run canvas-export --submissions /path/to/student-submissions --course "CS146" --assignment "Quiz 1" --dry-run
```

#### Global Installation (Optional)

To use the TUI or CLI tools from anywhere on your system:

```bash
uv tool install .
```

List available executables:
```bash
uv tool list
```

Then you can execute any of the commands from anywhere on your system:
```bash
tui
qa-file --help
```

## Demo


https://github.com/user-attachments/assets/f8938fa9-d64f-4d88-a8e4-62a48111a471


---

## Screenshots

| Feature                              | Screenshot                                               |
| ------------------------------------ | -------------------------------------------------------- |
| Template generation from QA file     | ![QA Template](docs/screenshots/qa-file.png)             |
| Scan splitting into student PDFs      | ![Submission Splitter](docs/screenshots/submission-splitter.png) |
| OCR-based submission markdown export | ![Submission File](docs/screenshots/submission-file.png) |
| TUI    | ![TUI](docs/screenshots/tui.png)                         |

---

## Technical Stack

| Area | Technologies |
| ---- | ------------ |
| Frontend/backend | [Python](https://www.python.org/), [Textual](https://textual.textualize.io/), Terminal Emulator | 
| Document processing | [pypdf](https://pypdf.readthedocs.io/en/stable/), [PyMuPDF](https://pymupdf.readthedocs.io/en/latest/), [Tesseract](https://tesseract-ocr.github.io/), [opencv](https://pypi.org/project/opencv-python/) |
| Local model integration | [Ollama](https://ollama.com/)
| Testing | [pytest](https://docs.pytest.org/en/stable/) |

## What's Next (195B)

- Website syncing: Sync local data to the web information securely.

- Automated grading: Extend the current exact-match evaluator (`automated-grading`) with fuzzy, numeric, and LLM-based answer matching.

- Assignment variety: Support the capability to grade Programming or Group assignments.


---

## Acknowledgments

-  Dr. Carlos Rojas - Advisor, for guidance and support throughout the project
-  Dr. Ben Reed - For sharing ideas and experiences, and providing feedback
---

## License

This project is licensed under the <FILL IN> License - see the [LICENSE](LICENSE) file for details.

---

_CMPE 195A/B - Senior Design Project | San Jose State University | Spring 2026_

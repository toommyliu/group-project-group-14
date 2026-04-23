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

Opengrader is a set of Python scripts to orchestrate an efficient grading workflow. Existing grading solutions aren't flexible enough to enable educators to grade assignments efficiently. Timely, quality feedback helps improve student learning outcomes, and yet, today's tools fail to meet these needs.

## Proof of Concept Scope

The PoC demonstrates the start of the workflow: generating templates, processing scans into structured markdown files, and a mocked TUI application. The PoC lacks the cohension of the artifact files to be used in the TUI, as most of the TUI inputs is hardcoded.

## Prerequisites

- [Python v3.13+](https://www.python.org/)
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
brew install python@3.13
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

## Running the PoC

Run the TUI:
```bash
uv run tui
```

Run CLI tools:
```bash
uv run <script_name> [arguments]

# Examples
uv run qa-file generate-template --qa-file SAMPLE-QA.md --output-pdf out/template-vA.pdf
uv run submission-file --help
```

<details>
<summary>Available Scripts</summary>

- `tui`: The TUI application
- `pdf-to-qa`: Convert an answer key PDF into a QA markdown template
- `qa-file`
  - `validate`: Validate QA markdown structure
  - `generate-template`: Generate templates from QA files
- `submission-splitter`: Split a scan into individual student submission PDFs
- `submission-file`: Process student submission PDFs into structured markdown

</details>

> [!TIP]
> Scripts that take flags support `--help` to show available options.

#### Example Usage Flow

0. (Optional) Convert an existing answer key PDF to a template:
```bash
uv run pdf-to-qa --input /path/to/answer-key.pdf --output /path/to/qa.md
```

1. Generate a template from a QA file:
```bash
qa-file generate-template --qa-file /path/to/qa.md --output-pdf /path/to/template.pdf
```

2. Process scan into individual student submissions:
```bash
submission-splitter --input /path/to/scan.pdf --output /path/to/output --pages-per-submission N
```

3. Process student submissions into structured markdown:
```bash
submission-file --input /path/to/output --output /path/to/markdown
```

Run a vision language model to extract printed and handwritten text from the processed submissions:
```bash
# Run your vision model first
ollama run qwen2.5vl:7b

# Then process the submissions
uv run submission-file \
  --input samples/cs146-midterm1/split-submissions \
  --output samples/cs146-midterm1/student-submissions \
  --text-backend ollama \
  --ollama-model <your-model-name>
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

| Category   | Technology |
| ---------- | ---------- |
| Frontend   | Python     |
| Backend    | Python     |

## What's Next (195B)

- Website syncing: Sync local data to the web information securely.

- Automated grading: Speed up grading for certain questions with an automated evaluator. 

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

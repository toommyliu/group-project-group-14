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

## Problem Statement

Existing grading solutions aren't flexible enough to enable educators to grade assignments efficiently.
Timely, quality feedback helps improve student learning outcomes, and yet, today's tools failure to meet these needs.

## Solution

The application will ingest scanned PDF submissions that are split by assignment.
Graders can create and customize rubrics per assignment, manage course rosters, and annotate PDFs directly. These capabilities will enable educators to visualize trends in grade statistics and questions, revealing revelations into the learning progress of students. Finally, marked PDFs are returned to students with comments from graders and rubric learning outcomes, optimizing feedback pathways into actionable insights for them.

### Key Features

- Local/offline-first grading tool
- TUI-based workflow(s)

---

## Demo

[Link to demo video or GIF]

**Live Demo:** [URL if deployed]

---

## Screenshots

| Feature     | Screenshot                                   |
| ----------- | -------------------------------------------- |
| [Feature 1] | ![Screenshot](docs/screenshots/feature1.png) |
| [Feature 2] | ![Screenshot](docs/screenshots/feature2.png) |

---

## Tech Stack

| Category   | Technology |
| ---------- | ---------- |
| Frontend   | Python     |
| Backend    | Python     |
| Database   |            |

---

## Getting Started

### Prerequisites

- [Python v3.13+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)
- Compatible Terminal Emulator

#### Terminal Compatibility

For images to render in the TUI correctly[^1], you may need a terminal emulator that supports the
[Kitty Graphics Protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/).

On macOS, the following terminal emulators were tested and work correctly:

- [Ghostty](https://github.com/ghostty-org/ghostty)
- [iTerm2](https://iterm2.com/)
- [WezTerm](https://wezfurlong.org/wezterm/)

> [!NOTE]
> Visual Studio Code's integrated terminal should work as of [version 1.110](https://code.visualstudio.com/updates/v1_110#_kitty-graphics-protocol). Enable the `terminal.integrated.enableImages` setting. See [Image Support](https://code.visualstudio.com/docs/terminal/advanced#_image-support) for details.

[^1]: https://github.com/lnqs/textual-image#supported-terminals

### Installation

Clone the repository:
```bash
git clone https://github.com/SJSU-CMPE-195/group-project-group-14.git opengrader
cd opengrader
```

Set up a Python virtual environment and install packages:
```bash
uv sync --all-groups
```

### Usage

Run the TUI:
```bash
uv run tui
```

Run CLI tools:
```bash
uv run <script_name> [arguments]

# Examples
uv run qa-file generate-template --qa-file SAMPLE-QA.md --output-pdf out/template-vA.pdf
uv run ocr-pdf --help
```

<details>
<summary>Available Scripts</summary>

- `tui` / `tui-dev`: The TUI application
- `qa-file`
  - `generate-template`: Generate templates from QA files
  - `embed-snippets`: Embed question images into QA files
- `ocr-pdf`: Extract text from PDFs using OCR
- `ocr-preview-pdf`: Preview and adjust OCR detection regions from a spec.json

Helper/bootstrapping scripts:
- `pdf-splitter`:  Split PDFs
- `question-splitter`: Split PDFs by question regions
- `roster-reader`: Read course roster from csv file
- `sample-pdf`: Generate sample gradeable PDFs for testing
- `course-demo`: CLI for creating demo course data
- `seed-db`: Seed database with test data
</details>

> [!TIP]
> Scripts that take flags support `--help` to show available options.

#### Global Installation (Optional)

To use the TUI or CLI tools from anywhere on your system:

```bash
uv tool install .
```

List available executables:
```bash
uv tool list
```

### Running Tests

```bash
uv run pytest
```

## Project Structure

```
.
├── src/opengrader/
│   ├── core/           # Shared code
│   ├── cli/            # Standalone CLI scripts
│   └── tui/            # Main TUI application
├── tests/              # Test suite
├── docs/               # Documentation
├── pyproject.toml      # Project configuration
└── README.md
```

---

## Acknowledgments

- [Resource/Library/Person]
- [Resource/Library/Person]

---

## License

This project is licensed under the <FILL IN> License - see the [LICENSE](LICENSE) file for details.

---

_CMPE 195A/B - Senior Design Project | San Jose State University | Spring 2026_

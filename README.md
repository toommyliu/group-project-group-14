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
  - If not using standalone installer, install [Homebrew](https://brew.sh/) first
- [Tesseract](https://tesseract-ocr.github.io/tessdoc/Installation.html)

#### Terminal Compatibility [^1]

To render images in the TUI properly, you may need a terminal emulator that supports the
[Kitty Graphics Protocol](https://sw.kovidgoyal.net/kitty/graphics-protocol/).

On macOS, the following (stock) terminal emulators were successful in our tests:

- [Ghostty](https://github.com/ghostty-org/ghostty)
- [iTerm2](https://iterm2.com/)
- [WezTerm](https://wezfurlong.org/wezterm/)

[^1]: For more information, see [Textual Image Support](https://github.com/lnqs/textual-image#supported-terminals)

> [!NOTE]
> Visual Studio Code's integrated terminal can work (as of [version 1.110](https://code.visualstudio.com/updates/v1_110#_kitty-graphics-protocol)), you will need to enable the `terminal.integrated.enableImages` setting. See [Image Support](https://code.visualstudio.com/docs/terminal/advanced#_image-support) for details.

### Usage Guide 

Clone the repository:
```bash
git clone https://github.com/SJSU-CMPE-195/group-project-group-14.git opengrader
cd opengrader
```

Set up a Python virtual environment and install packages:
```bash
uv sync --all-groups
```

Executing a script:
```bash
uv run <script_name> [arguments]

# Examples
uv run tui
uv run qa-file generate-template --qa-file SAMPLE-QA.md --output-pdf out/template-vA.pdf
uv run ocr-pdf --help
```

> [!TIP]
> Use the `--help` flag to see the available options for each script.

If you want to run the project without installing it globally, you can use the `uv run` command.

#### Global Installation

If you want to access the CLI tools globally:

```bash
$ uv tool install .
Installed 11 executables: ...

# List available "tools" / executables
$ uv tool list
opengrader v1.0
- ...
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

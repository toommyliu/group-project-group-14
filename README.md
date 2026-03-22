# Local Grader

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

Existing grading solutions aren't flexible enough to enable educators to grade assignments efficiently. They aren't able to grade efficiently and provide students quality feedback in a timely manner.
Timely, quality feedback helps improve student learning outcomes, and yet, today's tools failure to meet these needs.

## Solution

The application will ingest scanned PDF submissions that are split by assignment.
Graders can create and customize rubrics per assignment, manage course rosters, and annotate PDFs directly.
These capabilities will enable educators to visualize trends in grade statistics and questions, revealing revelations into the learning progress of students. Finally, marked PDFs are returned to students with comments from graders and rubric learning outcomes, optimizing feedback pathways into actionable insights for them.

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
| Deployment |            |

---

## Getting Started

### Prerequisites

- [Python v3.13+](https://www.python.org/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
  - If not using standalone installer, install [Homebrew](https://brew.sh/) first

### Installation

```bash
# Clone the repository
git clone https://github.com/SJSU-CMPE-195/group-project-group-14.git my-folder-name
cd my-folder-name
```

#### Installing UV

On macOS:

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh # or brew install uv

# Install dependencies and sync virtual environment
uv sync
```

#### Library Management

```bash
# install a library
uv add my-library

# remove a library
uv remove my-library
```

### Running Locally

```bash
# Run the TUI
uv run tui

# Executing CLI tools
uv run pdf-splitter assets/sample.pdf
```

### Running Tests

```bash
[test command]
```

---

## API Reference

<details>
<summary>Click to expand API endpoints</summary>

| Method | Endpoint            | Description         |
| ------ | ------------------- | ------------------- |
| GET    | `/api/resource`     | Get all resources   |
| GET    | `/api/resource/:id` | Get resource by ID  |
| POST   | `/api/resource`     | Create new resource |
| PUT    | `/api/resource/:id` | Update resource     |
| DELETE | `/api/resource/:id` | Delete resource     |

</details>

---

## Project Structure

```
.
├── [folder]/           # Description
├── src/                # Source code files
├── tests/              # Test files
├── docs/               # Documentation files
└── README.md
```

---

## Contributing

1. Create a feature branch (`git checkout -b feature/amazing-feature`)
2. Commit your changes (`git commit -m 'Add amazing feature'`)
3. Push to the branch (`git push origin feature/amazing-feature`)
4. Open a Pull Request

### Branch Naming

- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code refactoring

### Commit Messages

Use clear, descriptive commit messages:

- `Add user authentication endpoint`
- `Fix database connection timeout issue`
- `Update README with setup instructions`

---

## Acknowledgments

- [Resource/Library/Person]
- [Resource/Library/Person]

---

## License

This project is licensed under the <FILL IN> License - see the [LICENSE](LICENSE) file for details.

---

_CMPE 195A/B - Senior Design Project | San Jose State University | Spring 2026_

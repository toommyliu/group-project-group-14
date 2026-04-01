# AGENTS.md

## Task Completion Requirements

- `uv run pytest` must pass without errors.

## Project Overview

Local Grader is a TUI application for efficient grading workflows. Think GradeScope but local and offline-first, with a focus on keyboard-driven workflows.

## Core Priorities

1. Performance first.
2. Reliability first.

## Maintainabilty

Long term maintainability is a core priority. Prefer extracting shared logic into separate modules for reusability. Avoid duplicating logic across packages. Don't be afraid to change existing code.

## Packages

- `src/local_grader/core`: Shared logic and models (PDF, Courses, etc...) that can be consumed by other packages.
- `src/local_grader/cli`: Standalone CLI tools for PDF splitting, question splitting, and roster reading. These tools are meant to be called by the TUI or independently.
- `src/local_grader/tui`: The main TUI application built with Textual. Orchestrates the grading workflow by using `core` and `cli` packages.

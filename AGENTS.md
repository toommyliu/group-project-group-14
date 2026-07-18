# AGENTS.md

## Task Completion Requirements

- `uv run pytest` must pass without errors.

## Project Overview

Opengrader is a set of utility scripts to power an efficient grading workflow. Think GradeScope, but local and offline-first. Scripts compose together to form the complete workflow.

## Core Priorities

1. Performance first.
2. Reliability first.

## Maintainabilty

Long term maintainability is a core priority. Prefer extracting shared logic into separate modules for reusability. Avoid duplicating logic across packages. Don't be afraid to change existing code.

## Packages

- `src/opengrader/core`: Core data models and utilities for PDF processing, OCR, submissions, and grading workflow primitives
- `src/opengrader/cli`: Standalone CLI tools for template generation, submission splitting, OCR extraction, and converting PDFs to markdown format
- `src/opengrader/tui`: The main TUI application. Orchestrates grading by ingesting parsed student submission markdown files and enabling question-by-question grading.
- `packaging`: End-user distribution — self-updating bootstrap launchers and desktop-shortcut installers for macOS/Linux/Windows (see `packaging/README.md`).

# Question Answer File Specification

This document outlines the structure and format for the question answer file (QA file). QA files are used to outline assignments to generate templates for.

## Structure

A QA file is a markdown that uses YAML frontmatter for metadata and bullets for question content. 

---

## Frontmatter

Use standard `---` delimiters.

Supported fields:

- `title` - Assignment title
- `instructions` - Assignment instructions
- `continued_page_header` - Optional text for continued pages
- `questions_start_page` - `1` or `2`

> [!NOTE]
> All fields are optional except for `title`.

> [!TIP]
> Use `|` for multi-line text.

---

## Version Sections

To define multiple versions, group each version under an `##` header:

- `## Version A`
- `## Version B`

The `Version` prefix is optional in parsing, but recommended for readability.

If there is only one version, the `##` header is optional.

---

## Question Format (Indentation-Based)

Each top-level bullet (`- ...`) is a question.

Use inline points in the question text:

- `(10 points) What is ...?`

### Subquestions

Subquestions are nested bullets directly under a question (and are treated as question parts).

### Correct Answers

Use a `**Correct**` section under a question or subquestion. Multiple correct answers are supported.

- `- **Correct**`
  - `- answer 1`
  - `- answer 2`

---

## Example: Single Version

```md
---
title: |
  Quiz 1: Shell/Linux Commands
  CMPE195A: Senior Design Project I
instructions: Closed book, closed notes.
questions_start_page: 1
---

- (50 points) What is the capital of France?
  - **Correct**
    - Paris

- (30 points) Answer all parts.
  - (15 points) What is 2 in binary?
    - **Correct**
      - 0b10
      - 10_2
  - (15 points) What is 8 in hexadecimal?
    - **Correct**
      - 0x8
```

## Example: Multiple Versions

```md
---
title: Quiz 1
instructions: Closed book, closed notes.
questions_start_page: 1
---

## Version A

- (10 points) Convert decimal 5 to binary.
  - **Correct**
    - 101

## Version B

- (10 points) Convert decimal 6 to binary.
  - **Correct**
    - 110
```

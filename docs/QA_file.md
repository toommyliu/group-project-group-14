# Question Answer File Specification

This document outlines the structure and format for the question answer file (QA file). QA files are used to outline assignments to generate templates for.

## Structure

A QA file is a markdown file that uses YAML frontmatter for metadata and special delimiters for questions and answers. Think of it as a DSL for describing templates.

### Frontmatter

Metadata is defined in the YAML frontmatter at the top of the file, delimited by `---` lines.

Supported frontmatter fields:
- `title` - The assignment title
- `instructions` - The assignment instructions
- `continued_page_header` - Optional header text for continued pages
- `versions` - List of version identifiers (e.g., A, B, C)
- `questions_start_page` - Page number where questions should start (1 or 2)

> [!NOTE]
> All fields are optional except for `title`.

> [!TIP]
> Use `|` for multi-line text.

> [!TIP]
> `versions` can be comma-separated or a list.

### Delimiters

The following delimiters are used in QA files for questions and answers:

- `===QUESTION===` - Marks the beginning of a question
- `===SUBQUESTION===` - Marks the beginning of a subquestion
- `===ANSWER===` - Marks the beginning of an answer
- `===IMAGE===` - Marks the beginning of an image
- `===END===` - Marks the end of a question

### On Question Structure

`===QUESTION===` starts a new question block. A question block can contain:
- Direct answers (no subquestions)
- Subquestions with their own answers (no question-level answers)

Do not mix both patterns. If a question has subquestions, answers should only appear at the subquestion level. The parser infers context from delimiter order, so precise ordering is required.

### Helpful Tips

1. Each question MUST end with `===END===`.

2. `===IMAGE===` delimiters can be omitted initially, they're auto-inserted during template generation in a second pass.

3. Use multiple `===ANSWER===` delimiters for questions with multiple correct answers.

### Example

```md
---
title: |
  Quiz 1: Shell/Linux Commands
  CMPE195A: Senior Design Project I
  San Jose State University
  Friday, March 22, 2026
instructions: Closed book, closed notes. Show your work for full credit.
versions:
  - A
  - B
  - C
questions_start_page: 1
---

===QUESTION===
(50 points) What is the capital of France?
===ANSWER===
Paris
===END===

===QUESTION===
(30 points) Answer all parts.
===SUBQUESTION===
(15 points) What is 2 in binary?
===ANSWER===
0b10
===ANSWER===
10_2 (10 with superscript 2)
===SUBQUESTION===
(15 points) What is 8 in hexadecimal?
===ANSWER===
0x8
===END===
```

This is represented as:
- `title`: Quiz 1: Shell/Linux Commands\nCMPE195A: Senior Design Project I\nSan Jose State University\nFriday, March 22, 2026
- `instructions`: Closed book, closed notes. Show your work for full credit.
- `versions`: A, B, C
- `questions_start_page`: 1
- `QUESTION`: (50 points) What is the capital of France?
- `ANSWER`: Paris
- `QUESTION`: (30 points) Answer all parts.
- `SUBQUESTION`: (15 points) What is 2 in binary?
- `ANSWER`: 0b10
- `ANSWER`: 10_2 (10 with superscript 2)
- `SUBQUESTION`: (15 points) What is 8 in hexadecimal?
- `ANSWER`: 0x8


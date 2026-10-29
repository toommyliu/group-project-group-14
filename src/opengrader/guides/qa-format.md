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

The text after the points is optional, so `- (10 points)` is a complete
question. The web app's outline shows this text as the question title.

### Subquestions

Subquestions are nested bullets directly under a question (and are treated as question parts).

### Correct Answers

Use a `**Correct**` section under a question or subquestion. Multiple correct answers are supported.

- `- **Correct**`
  - `- answer 1`
  - `- answer 2`

### Multiple Choice

Use a `**Choices**` section under a question or subquestion to define a single-select multiple-choice question.

- `- **Choices**`
  - `- A) option text`
  - `- B) option text`

Rules:

- `**Choices**` implies the question is multiple choice.
- Multiple choice is currently **single-select only**.
- Choices must use contiguous labels starting at `A`.
- Supported choice formats are `A) text`, `A. text`, or `A: text`.
- For multiple-choice questions, `**Correct**` must contain **exactly one** option label, such as `B`.
- A question cannot mix `**Choices**` with nested subquestions.

### Rubric

Use a `**Rubric**` section under a question or subquestion to define scoring criteria.

A **rubric item** is a grading category. Top-level rubric categories are fixed to:

- `Correct`
- `Partially Correct` (or `Partially Incorrect`)
- `Incorrect`

Each question/subquestion rubric must have **exactly one** of each top-level category.

A point value is the score associated with a category or subitem. A rubric can use
one of two scoring modes:

- **Negative deductions** start from full credit. Use `Correct (+0)` and deduct
  points for weaker answers.
- **Positive awards** start from zero credit. Use `Correct (+N)`, where `N` is
  the question's point value, and award points for partial answers.

A single rubric cannot mix positive awards with negative deductions.

Supported formats:

- `Label (+N)`
- `Label (-N)`
- `Label: +N`
- `Label: -N`

`Correct` and `Incorrect` are exclusive shortcut rows in the grading UI. A direct
top-level `Partially Correct (+/-N)` value is also exclusive. Nested subitems
under `Partially Correct` are checklist rows, so graders can select multiple
items for partial credit or deductions.

Weighted criteria are expressed as **subitems** under `Partially Correct`:

- Negative-deduction rubric:
  - `Correct (+0)`
  - `Partially Correct`
    - `Major issue (-3)`
    - `Minor issue (-2)`
  - `Incorrect (-5)`

- Positive-award rubric:
  - `Correct (+5)`
  - `Partially Correct`
    - `Mostly correct (+3)`
    - `Minor progress (+2)`
  - `Incorrect (+0)`

You may also put a direct value on a top-level category:

- Negative-deduction rubric:
  - `Correct (+0)`
  - `Partially Correct (-2)`
  - `Incorrect (-5)`

- Positive-award rubric:
  - `Correct (+5)`
  - `Partially Correct (+3)`
  - `Incorrect (+0)`

Nested `Partially Correct` checklist subitems must exactly cover the full question
value:

- Negative-deduction checklist subitems must sum to `-points`.
- Positive-award checklist subitems must sum to `+points`.
- Direct top-level partial rows are exempt because they are single exclusive
  choices.

### Award Checklist Example

```md
- (10 points) Convert decimal 5 to binary.
  - **Correct**
    - 101
  - **Rubric**
    - Correct (+10)
    - Partially Correct
      - Binary digits mostly correct (+7)
      - Shows conversion setup (+3)
    - Incorrect (+0)
```

### Deduction Checklist Example

```md
- (10 points) Convert decimal 5 to binary.
  - **Correct**
    - 101
  - **Rubric**
    - Correct (+0)
    - Partially Correct
      - Wrong binary digit (-3)
      - Incorrect place values (-7)
    - Incorrect (-10)
```

### Direct Partial Example

```md
- (10 points) Convert decimal 5 to binary.
  - **Correct**
    - 101
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-5)
    - Incorrect (-10)
```

### Default Comments (auto-inserted feedback)

Any rubric option — a top-level `Correct`/`Incorrect`, a direct `Partially Correct`,
or a checklist subitem — may carry a `**Comment**` bullet. When a grader selects
that option in the TUI, its comment text is appended to the question's comment box
automatically, so the most common feedback is written with a single keystroke.

```md
- (10 points) Explain recursion.
  - **Rubric**
    - Correct (+0)
      - **Comment** Clear explanation of the base and recursive cases.
    - Partially Correct
      - Missing base case (-4)
        - **Comment** Remember to state the base case that stops the recursion.
      - Vague wording (-6)
    - Incorrect (-10)
      - **Comment** This does not describe recursion; see me in office hours.
```

Behavior:

- **Append-only and de-duplicated.** The comment is added only when it is not
  already present, so re-picking or toggling an option never repeats it, and
  switching options never deletes text you have already written or edited.
- **Distinct from grader notes.** Other bullets under an option remain
  grader-facing notes; only the `**Comment**` bullet is auto-inserted.
- **Optional.** An option with no `**Comment**` bullet inserts nothing.
- The inline form (`**Comment** text`) shown above is canonical; the text
  round-trips onto one line when the QA file is re-saved.

### Learning Outcomes (`**Outcome**`)

Any question may be tagged with the learning outcomes it measures, using an
optional, repeatable `**Outcome**` bullet. This is what turns a pile of graded
exams into the per-outcome mastery evidence a programme or accreditation review
asks for.

```md
- (10 points) Derive the recurrence for merge sort.
  - **Correct**
    - T(n) = 2T(n/2) + O(n)
  - **Outcome** CLO3
  - **Outcome** ABET-1
```

Three authoring forms are accepted and all mean the same thing:

```md
  - **Outcome** CLO3                  # inline, one per bullet (canonical)
  - **Outcomes** CLO1, CLO2           # comma-separated
  - **Outcomes**                      # nested
    - CLO1
    - CLO2
```

Behavior:

- **Optional and free-form.** The name is *your* vocabulary — `CLO3`,
  `ABET-2`, `Applies Bayes' rule`. It is never interpreted, and never
  normalized, so two outcomes you meant to keep apart stay apart.
- **Repeatable.** One question can serve several outcomes, and one outcome is
  normally spread across several questions — that is the point.
- **Subquestions inherit.** Tagging a multi-part question once applies the tag
  to every part; a part with its own `**Outcome**` overrides it.

  ```md
  - (6 points) Two-part question.
    - **Outcome** CLO2          # applies to (a)
    - (3 points) Part a.
      - **Correct**
        - ...
    - (3 points) Part b.
      - **Correct**
        - ...
      - **Outcome** CLO4        # overrides for (b)
  ```
- **Costs nothing when unused.** A QA file with no `**Outcome**` bullets is
  byte-identical after a load/save, and every consumer below simply omits the
  outcome section.

Where the tags show up (all read the answer key, since that is where the tags
live — the submissions carry only points):

| Where | How |
| --- | --- |
| `opengrader item-analysis --solutions solutions.md` | a per-outcome mastery section |
| `opengrader grades-export --solutions solutions.md` | one `outcome:<NAME> %` column per outcome |
| `opengrader return-bundle --solutions solutions.md` | a Learning outcomes page in the analysis PDF |
| `opengrader course-grades --course course.toml` | mastery pooled across every assignment |

Mastery is measured in **points, not questions** — an outcome carried by a
20-point essay and a 2-point definition is dominated by the essay, which is what
your own point weighting already said. A student is counted as having mastered
an outcome at 70% of its points by default (`--mastery-threshold`), and a
question nobody has graded yet contributes nothing rather than a zero.

### Saved Rubric Labels

Student submission markdown stores the selected rubric labels in the `Rubric:`
field:

```md
- Points: 3
- Rubric: ["Partially Correct - Wrong binary digit","Partially Correct - Incorrect place values"]
```

No selected labels are saved as `Rubric: `. One selected label is saved as plain
text. Multiple labels are saved as a compact JSON array.

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
  - **Rubric**
    - Correct (+50)
    - Partially Correct
      - Country is correct but city is wrong (+30)
      - Correct country but no city answer (+20)
    - Incorrect (+0)

- (30 points) Answer all parts.
  - (15 points) What is 2 in binary?
    - **Correct**
      - 0b10
      - 10_2
    - **Rubric**
      - Correct (+0)
      - Partially Correct
        - Uses binary but wrong value (-3)
        - Binary process mostly incorrect (-12)
      - Incorrect (-15)
  - (15 points) What is 8 in hexadecimal?
    - **Correct**
      - 0x8
    - **Rubric**
      - Correct (+0)
      - Partially Correct (-5)
      - Incorrect (-15)

- (10 points) Which data structure is FIFO?
  - **Choices**
    - A) Stack
    - B) Queue
    - C) Tree
    - D) Graph
  - **Correct**
    - B
  - **Rubric**
    - Correct (+0)
    - Partially Correct (-4)
    - Incorrect (-10)
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

- (10 points) Answer both parts.
  - (5 points) Pick the even number.
    - **Choices**
      - A) 3
      - B) 4
      - C) 5
    - **Correct**
      - B

## Version B

- (10 points) Convert decimal 6 to binary.
  - **Correct**
    - 110
```

# Running each step by hand

```bash
# 0. (Optional) Draft a QA answer key from an existing key
opengrader create-answer-key --input answer-key.pdf --output qa.md

# 1. Generate the printable assignment
opengrader qa-file generate-template --qa-file qa.md --output-pdf template.pdf

# 2. Check and split the scanned stack
opengrader scan-check --input scans/all.pdf --pages-per-submission 6
opengrader submission-splitter --input scans/all.pdf --output split/ --fix-rotation

# 3. Read each student's answers (optional: add --llm-responses to transcribe handwriting with the AI in AI setup)
opengrader submission-extractor --input split/ --output student-submissions/ --qa-file qa.md

# 4. Auto-grade what can be matched (optional: add --llm for AI suggestions on written answers)
opengrader automated-grading --solutions qa.md --input student-submissions/

# 5. Grade the rest
opengrader tui --qa-file qa.md --submissions student-submissions/

# 6. Send grades to Canvas (preview first)
opengrader canvas-export --submissions student-submissions/ --course "CS146" --assignment "Quiz 1" --dry-run
```

Find any command and its options:

```bash
opengrader --help              # commands grouped by workflow step
opengrader commands --all      # every command, including the ones the desktop app covers
opengrader help <command>      # one command's options
```

Short aliases, also under `opengrader`: `split`, `extract`, `auto-grade`, `answer-key`, `grade` (= `tui`), `pipeline` (= `run`).

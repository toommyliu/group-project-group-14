# Working with your AI assistant

Small local models grade written answers poorly. The assistant you already use does much better, and Opengrader keeps student identities away from it. The assistant always works in its own folder beside the class folder, never in it.

**Class folders and workspaces.** The same jobs work in both. A class folder (the desktop app's classic mode) uses the Tools card below. In a **workspace** (`opengrader tui`, the browser app), press **A** on the assignment list, or open **AI assistant** in the browser's sidebar. The copy is made from the workspace's own grades and merged back through its service. There the assistant counts as a grader of its own: it fills answers nobody has graded, and its grade on an answer someone graded after the copy was made waits as a change request you approve or dismiss (**Q** while grading). A reading it types helps it grade but is not stored, because a workspace keeps the scan's reading. When you accept a fix to the answer key, you choose whether the assignment's `qa.md` gets it too: writing grades back to files never carries the key.

**In the desktop app:** **Tools → AI assistant**. Choose the class folder and it offers only what fits:

| The class folder has | You see |
| --- | --- |
| A blank assignment, no answer key | **Draft a standard key** (no AI: every question, its points and the standard rubric, answers blank) and **Write the key with my assistant…** |
| An answer key with blank answers | **Type the answers…**, **Answers from a list…**, or **Write the key with my assistant…** |
| An answer key and submissions | **Grade…** and, for questions with no partial credit yet, **Draft rubrics…** |

Scanning, splitting and reading the scans always stay on your computer: whole pages carry names.

An answers list is a plain text file, one question a line, numbered like the answer form:

```text
1: H2O; Water
2) B
3a. 101
4.
Paris
City of Light
```

Several accepted answers go on one line separated by `;` or `|` (a comma followed by a space works too when a line has neither, so `1,000` stays one answer), or on lines of their own under the question, as for 4. A line needs `1:`, `1.`, `1)` or `1 -` to start a question, so a bare `2` is an answer, and a blank line ends a question. From a terminal: `opengrader qa-file fill-answers --qa-file qa.md --answers answers.txt`.

**From a terminal**, the same jobs:

```bash
uv run opengrader agent answer-key --class-folder class [--source key.docx]   # the assistant writes or fills qa.md
uv run opengrader agent use-key --folder class-ai-answer-key --to class/qa.md
uv run opengrader agent prepare --submissions class/submissions --qa-file class/qa.md            # grading
uv run opengrader agent prepare --submissions class/submissions --qa-file class/qa.md --rubrics  # drafting rubrics

# the same in a workspace: name it and the assignment instead
uv run opengrader agent prepare --workspace course --assignment "Quiz 1"
uv run opengrader agent answer-key --workspace course --assignment "Quiz 1"
```

Start your assistant **inside the folder Opengrader made** and ask it to do what `AGENTS.md` there says (Claude Code reads `CLAUDE.md`, which points to it). For grading it checks the key with `opengrader agent check-key`, looks at each answer image, and records transcriptions and rubric choices with `opengrader agent transcribe` and `opengrader agent grade`; Opengrader checks every choice against the rubric and works out the points. When the key looks wrong, or a question has no partial credit, it records a fix with `opengrader agent suggest` instead of acting on it. You accept or dismiss each one (**Suggestions…** in the app, or `opengrader agent suggestions` / `accept` / `dismiss`), and an accepted fix changes only that question of your key.

Chat apps without a shell (Claude Desktop, ChatGPT) connect to a copy as an MCP server instead (**Copy MCP setup**, or `opengrader agent mcp-config --packet <copy>`). The assistant then has no file access at all, only the grading tools.

When it is done:

```bash
uv run opengrader agent apply --submissions class/submissions --incoming class-ai-assistant/<name> --yes --delete-copy
# a workspace (accept / use-key also take --key-file update|leave):
uv run opengrader agent apply --workspace course --assignment "Quiz 1" --incoming course-ai-assistant/<name> --yes
```

Its grades arrive marked as AI grades. In the grading app, **v** steps through a question's unconfirmed AI grades and **y** confirms one; picking another rubric option changes it, and the review queue and `grade-check` flag the low-confidence ones. The three-way merge never overwrites a grade you made, and `grade-restore` can undo it.

**What protects student data:** the copy itself, not instructions to the assistant.
- Every student is an `og-…` code. The scans, the name and ID crops, and the key that maps codes to students stay in your class folder.
- Only the answers the job needs go in: the ones still to grade (`--question` narrows it further; `--with-graded` adds your graded ones as examples), or, for rubrics, the answers to the questions being drafted.
- Names and IDs are scrubbed from answer text and comments, and Opengrader refuses to make a copy that still contains one.
- Every answer image is read with Tesseract first. One showing the student's name or ID, or one that cannot be read, stays with you.
- The copy's `.claude/settings.json` stops Claude Code reading outside it and sandboxes its shell commands. Other assistants have no such file, so start them in the copy.
- Tesseract misses messy handwriting, so a name scrawled inside an answer box can still get through.

The copy is still student work: use the account your school approved, turn off training on your data on a personal plan, and delete the copy once it is merged (`--delete-copy`). `uv run opengrader ai-readme` is the page to give your assistant.

## Where AI settings are stored

AI settings are saved once in `config.json` in your user config folder:

- macOS: `~/Library/Application Support/Opengrader/`
- Linux: `~/.config/opengrader/`
- Windows: `%APPDATA%\Opengrader\`

Edit them from the desktop app under **Tools → AI models & providers → Open AI setup**. Command-line flags override saved settings, which override the built-in defaults. API keys are never stored here; they go in your OS password store.

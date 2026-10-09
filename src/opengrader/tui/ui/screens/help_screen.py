from __future__ import annotations

from collections.abc import Iterable

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static

from opengrader.core.app_config import DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL

_GLOBAL_ROWS: list[tuple[str, str]] = [
    ("q", "Quit Opengrader."),
    ("F2", "Save pending feedback and choose something else to grade: the workspace's assignment list (opengrader tui), or the Start grading card (opengrader classic)."),
    ("F3", "Tools, in opengrader classic only: every pipeline step and helper tool (doctor, AI setup and providers, sharing, backup). F3 again goes back to grading. A workspace (opengrader tui) is grading only."),
    (
        "t",
        "Toggle teacher mode. Grading is anonymous by default — students "
        "appear as IDs; teacher mode reveals names and the Name tab in "
        "the QA picker. Disabled when the assignment is locked to anonymous "
        "grading (multi-grader mode; see the grading-policy tool). In Tools "
        "(F3) with nothing open there is nobody to anonymize, so t opens the "
        "copy-paste answer-key format templates instead.",
    ),
    ("?", "Open this help."),
]

_REPORT_ROWS: list[tuple[str, str]] = [
    (
        "shift+R",
        "Open the needs-review queue: submissions likely holding an OCR/"
        "extraction error or low-confidence AI grade, most severe first. Each "
        "row shows its source PDF. Select a row to jump to it — or, for a "
        "missing/duplicate student ID, to pick the right student (from the "
        "tied roster names) or type the ID, written straight back to the file.",
    ),
    (
        "shift+G",
        "Open the regrade-request queue: student grade disputes tracked as "
        "first-class items (filed with shift+F or the regrade CLI). Each row "
        "shows who is disputing which question and why. From a row: enter opens "
        "the disputed question to regrade, r resolves the request, d denies it "
        "(those last two are keys of this pop-up, not app hotkeys). Open "
        "disputes also appear at the top of the review queue. Anonymity-safe.",
    ),
    (
        "shift+C",
        "Open the grading-consistency audit: answer groups where students who "
        "gave the same answer got different grades — scoring conflicts first, "
        "then same-score/different-rubric ones. Select a student (click or "
        "enter) to jump to their question and regrade. Anonymity-safe.",
    ),
    (
        "shift+P",
        "Open the answer-similarity audit: clusters of students whose "
        "free-response answers are near-identical — near-verbatim first, then "
        "strongly similar, with the pairwise match percentages. Select a "
        "student (click or enter) to jump to their answer. Anonymity-safe.",
    ),
    (
        "shift+D",
        "Divide the grading: who has claimed which slice of the class, so two "
        "TAs never mark the same paper. From here: c claims a run of questions "
        "(q1-q3), r releases one of yours, d marks it done, a switches between "
        "showing only your slice and the whole class. While a division is in "
        "force your navigation skips other people's work and questions they "
        "hold are labelled with their name — but nothing is ever blocked: "
        "opening someone else's question warns you and still saves. Same log "
        "the claim CLI writes.",
    ),
    (
        "shift+S",
        "Open item analysis: per-question difficulty (how much credit the "
        "class earned) and separation (how well the question tells stronger "
        "students from weaker ones), plus a per-choice breakdown for "
        "multiple-choice questions. All class aggregates — no student named. "
        "When the assignment was graded across class sections, f cycles the "
        "view through all sections then each section on its own. If your "
        "answer key tags learning outcomes (**Outcome** bullets), o switches "
        "to per-outcome mastery: who cleared the bar, and which questions — "
        "and how much of the assignment — actually measured each outcome.",
    ),
    (
        "shift+A",
        "Calibrate AI grading: re-grade free responses you have already marked "
        "with the model and see how often it agreed with you — per model, per "
        "question type, and per confidence level — then get the minimum "
        "confidence the evidence supports. Accepting it saves that number "
        "into AI setup, so every later run uses it — including ones started "
        "from a terminal. Nothing else is written; this only measures. Unlike "
        "the other reports here it takes a while (every measured answer is "
        "one model call), so there is an answer limit for a quick first look. "
        "Also in Tools (F3, classic).",
    ),
    (
        "shift+H",
        "Teacher mode only. Open the grade history: the durable audit log of "
        "who changed which grade, from what to what, and when (all graders, "
        "all sessions).",
    ),
    (
        "shift+V",
        "Teacher mode only. Open grading-session analytics: how much grading "
        "happened, how fast, over how many sessions, per grader — throughput "
        "and active time from the same audit journal as shift+H.",
    ),
    (
        "shift+I",
        "Teacher mode only. Open grader agreement: is the team marking this "
        "the same way? Per grader, how generously they marked relative to how "
        "each question was marked overall (so being handed the hard questions "
        "doesn't read as being harsh), whether they drifted between the start "
        "and end of their session, and — where two graders marked comparable "
        "work — how often they agreed and who is the more generous. On a "
        "cleanly divided class nobody marks comparable work, and it says so "
        "rather than guessing: `calibrate pack` sets up a norming round where "
        "everyone marks the same sample. CLI: calibrate report.",
    ),
    (
        "shift+F",
        "Teacher mode only. File a regrade request: log a student's grade "
        "dispute. On the grade "
        "screen the student and question in focus pre-fill the form; on the QA "
        "picker pick the student (a dropdown, or enter an ID manually) and "
        "question. It never changes a grade — it appears in the regrade queue "
        "(shift+G) and the review queue (shift+R).",
    ),
]

_SHARING_ROWS: list[tuple[str, str]] = [
    (
        "shift+E",
        "Make a grader copy of this class for someone else to mark on their "
        "own machine. Every name and student id is replaced by a code and the "
        "scanned work stays here, so handing the folder over gives nothing "
        "away — you keep the one file that turns the codes back into students, "
        "and merging their work back needs it. The copy is written to a "
        "grader-copies/ folder next to your submissions unless you tick the "
        "box to choose somewhere else. Tick 'Protect the copy with a "
        "passphrase' to send it as one locked file instead of a folder — for "
        "email or a USB stick. Hiding names and locking the file solve "
        "different problems and work together: one stops the grader knowing "
        "whose work they are marking, the other stops anyone else opening the "
        "copy at all. Tell the grader the passphrase some other way than the "
        "message carrying the file.",
    ),
    (
        "shift+M",
        "Merge a grader copy back in. Answers only they marked are taken, "
        "answers only you marked are kept, and anything you both changed is "
        "listed for you to settle one row at a time (o keeps yours, t takes "
        "theirs; j/k moves between them). Nothing is written until you press "
        "Merge, and the result tells you the single command that undoes the "
        "whole thing.",
    ),
    (
        "shift+B",
        "Back up this class, or check what has changed in it. Record a "
        "checksum of every file and you can ask later exactly what moved — a "
        "changed grade file is reported as explained (grading was recorded for "
        "that student since) or unexplained (nothing in the audit trail says "
        "why), and that second list is the one worth reading. The archive it "
        "writes is read back and verified before it says it worked, and it "
        "goes next to your submissions folder, never inside it — a backup kept "
        "there is lost with what it protects. Tick 'Protect the archive with a "
        "passphrase' for a backup that leaves this computer: a backup holds "
        "every name, id and scan there is. Nobody can recover that passphrase "
        "for you, so store it somewhere you will still have it in a year.",
    ),
    (
        "shift+L",
        "Release the grades — hand this class back. It first says whether you "
        "are ready: what is still ungraded, whose student ID could not be "
        "read, open regrade requests, answers where the same response got "
        "different points, and whether a curve is on. Recording a release "
        "changes no grade and stops nothing; it remembers what each student "
        "was shown, so press shift+L again later and 'What changed' names "
        "every question whose grade has moved since — which is the answer to "
        "\"this is not the grade you gave me\". From a terminal: grade-check "
        "and grade-release.",
    ),
]

_QA_PICKER_ROWS: list[tuple[str, str]] = [
    ("j · k · ↑ · ↓", "Move the highlight in the active pane."),
    ("tab", "Switch between the Questions and Students panes."),
    (
        "enter · click",
        "Start grading the highlighted question across every student.",
    ),
    (
        "/",
        "Search the active pane. Type to filter the list; enter or esc "
        "finishes the search.",
    ),
    ("u", "Jump to the first question that still has ungraded students."),
    (
        "d",
        "Students pane: switch between ID and Name views (teacher mode "
        "only).",
    ),
    ("x", "Open the Canvas export dialog, prefilled for this assignment."),
    (
        "b",
        "Open the return-bundle export dialog: package this class into one "
        "folder — the gradebook CSV, each student's annotated + summarized PDF, "
        "and a class analysis report — ready to hand back. Pick which documents "
        "from the dropdown (gradebook only … everything).",
    ),
    (
        "c",
        "Open the score-curve editor: set an assignment-wide curve (flat bonus, "
        "scale, or square-root) and watch the class distribution shift raw → "
        "curved before saving. Or type a target class average (mean/median, %) "
        "and Solve to have it find the bonus/scale value for you. A reversible "
        "overlay on the raw totals — it never edits a grade, and Clear restores "
        "them. Applied to Canvas/CSV exports and the summary page.",
    ),
    (
        "z",
        "Undo the most recent grade or comment change listed in the Grading "
        "Log, restoring the submission file to its previous value.",
    ),
    (
        "click log row",
        "Grading Log: reopen that entry's question — and student, when the "
        "entry has one — on the grade screen.",
    ),
]

_GRADING_ROWS: list[tuple[str, str]] = [
    ("j · k", "Next / previous question (also ↓ / ↑)."),
    ("ctrl+j · ctrl+k", "Next / previous student (also → / ←)."),
    ("n · p", "Next / previous student whose answer is still ungraded."),
    ("a", "Jump back to the first student for this question."),
    (
        "1-9",
        "Toggle the numbered rubric option for this answer. If the option "
        "has a default comment, its text is appended to the comment box "
        "automatically (never duplicated).",
    ),
    (
        "shift+1-9",
        "Select the rubric option and finalize in one stroke. ctrl+digit and "
        "alt+digit do the same; shift is the closest reach, and on a non-US "
        "keyboard layout ctrl/alt are the reliable ones.",
    ),
    (
        "b",
        "Grade by answer, not by student — the fast way through a big class. "
        "Turn it on and j/k, n/p and ctrl+j/k move between this question's "
        "distinct answers instead of its students, so a class of 200 that "
        "wrote 27 different answers is 27 stops, not 200. The Submission card "
        "says which answer you are on, how many are left, and how many "
        "students the one in front of you speaks for. Nothing is graded "
        "differently: you still pick a rubric option and press g, which now "
        "also moves you on to the next answer. Press b again for ordinary "
        "student-by-student grading.",
    ),
    (
        "g",
        "Grade the whole answer group: apply this question's rubric "
        "selection to every student who gave the same answer. The "
        "Submission card shows how many share it. In 'by answer' mode (b) "
        "this also jumps to the next answer still needing a grade.",
    ),
    (
        "v",
        "Confirm AI grades: sweep the answers this question's auto-grading "
        "marked with an [AI ...] tag but no human has checked. Turn it on and "
        "j/k, n/p and ctrl+j/k move between exactly those answers - the "
        "ordinary 'next ungraded' keys skip them, because an AI grade is a "
        "grade. The Submission card names the model and its confidence. "
        "Press v again to leave the mode.",
    ),
    (
        "y",
        "Confirm the AI grade on this answer: the points and rubric stay "
        "exactly as they are and the note is marked as checked by a human, "
        "which clears it from the review queue and from the release "
        "pre-flight's warning. In 'confirm AI' mode (v) this also moves on to "
        "the next one. To disagree, just pick a different rubric option as "
        "usual - that overwrites the AI grade.",
    ),
    (
        "shift+Y",
        "AI draft grades for this question: the configured AI grades every "
        "student who has no grade on it yet, using this question's rubric - "
        "one call per distinct written answer, so students who wrote the same "
        "thing get the same grade (accepted similar-answer merges count as the "
        "same answer). Blank answers are never sent, and a grade that already "
        "exists is never overwritten. Answers the AI is not confident about "
        "are left for you. Every drafted grade carries the [AI ...] tag, so v "
        "sweeps them for you to confirm (y) or override. A cloud provider "
        "shows how many calls will be sent and asks first.",
    ),
    (
        "shift+U",
        "Unify similar answers: review the merges proposed for this "
        "question's near-identical answers and accept one to widen what g "
        "grades. It merges nothing until you accept, and accepting still "
        "grades nobody — you pick a rubric option and press g as usual. "
        "Only free-response answers are ever proposed; multiple choice and "
        "numeric are already grouped exactly.",
    ),
    (
        "ctrl+g",
        "Finalize this question and advance to the next ungraded student.",
    ),
    (
        "e",
        "Edit the rubric. Saved changes are written back to the QA file "
        "and apply to every student. In the editor, Draft with AI (or "
        "ctrl+r) asks the configured AI to draft partial-credit items from "
        "the answers students actually gave; the draft only fills the "
        "editor rows, and nothing is saved until you review it and press Save. "
        "The same draft from a terminal: CLI: draft-rubric.",
    ),
    (
        "c",
        "Focus the comment box for this answer; enter or esc returns to "
        "the rubric.",
    ),
    (
        "x",
        "Fix the transcription: open an inline editor to correct the OCR/AI "
        "reading of the handwritten answer. Saving records it as verified "
        "(confidence 100, clearing the low-confidence review flag) and is "
        "undoable with z.",
    ),
    (
        "l",
        "Open the comment library: insert a saved feedback snippet (press "
        "its number) into the comment box, or save a new one. Snippets are "
        "kept with the assignment; g toggles the order between most-used "
        "everywhere (global) and most-used on this question.",
    ),
    (
        "z",
        "Undo the last persisted grade or comment change, newest first. "
        "Restores the submission file to its previous value.",
    ),
    ("+ · - · 0", "Zoom the response image in, out, or back to fit."),
    (
        ", · .",
        "Turn the displayed image a quarter turn left (,) or right (.) — for "
        "an answer the student wrote sideways in the box, or a page the scan "
        "check could not orient. The view only: the scan, the transcription "
        "and the grade are untouched. The current angle is shown next to the "
        "zoom level, and clears when you move to another student. To fix a "
        "whole stack scanned the wrong way round, use scan-check --fix "
        "(or the splitter's scan-check option) before extracting instead.",
    ),
    (
        "w · shift+W",
        "Whole-page context: step the image pane forward (w) or back "
        "(shift+W) "
        "through the answer crop, the scan page(s) this answer was cropped "
        "from — with the crop region outlined — and then every extra work "
        "page in the submission. Use it whenever an answer looks cut off "
        "('continued on back') or the student used scratch paper; extra "
        "pages belong to no single question, so they are reachable from all "
        "of them. Outside teacher mode the sheet's Name / Student ID header "
        "is blacked out of the page, so paging around stays anonymous.",
    ),
    (
        "o",
        "Open the displayed image in your desktop image viewer — the crop, a "
        "whole page, or rendered math, whichever is showing. Works in any "
        "terminal — use it when the in-terminal preview is coarse, or when a "
        "crop needs a full screen. See TERMINAL IMAGES below.",
    ),
    (
        "[ · ]",
        "Shrink / widen the sidebar (its height in top-bottom layout).",
    ),
    ("ctrl+v", "Toggle side-by-side vs top-bottom layout."),
    ("esc", "Back to the QA picker."),
]

_RUBRIC_EDITOR_ROWS: list[tuple[str, str]] = [
    ("tab · shift+tab", "Cycle between the editor fields and buttons."),
    (
        "Auto-comment field",
        "The line under each option sets its default comment: feedback "
        "auto-inserted into the comment box whenever a grader picks that "
        "option. Leave blank for none.",
    ),
    ("ctrl+s", "Validate and save the rubric."),
    ("esc", "Cancel without saving."),
]

_PIPELINE_ROWS: list[tuple[str, str]] = [
    (
        "Prepare an assignment",
        "One card in Tools (F3, classic), under PREPARE AN ASSIGNMENT: open "
        "'Run it — one scan in, graded work out', choose the class folder and "
        "press Run Pipeline. The scan, answer key, roster and blank template "
        "inside the folder are found for you and listed under the box; the "
        "run then checks the scan, splits it, extracts every submission and "
        "auto-grades what it can, and grading opens on the result. Preview "
        "shows the plan without writing anything. If the folder holds more "
        "than one candidate, or the scan or answer key lives somewhere else, "
        "Advanced settings opens so you can choose. The scanned bundle can "
        "also be a folder of already-split per-student PDFs (what Import "
        "phone photos writes), in which case nothing is split. CLI: opengrader-run class/ ('--scan bundle.pdf "
        "--qa qa.md --out class/' still works, and any flag overrides what "
        "the folder offered).",
    ),
    (
        "Running it again",
        "There is no separate button per stage: run the same card again and "
        "it works out what is left. An unchanged scan is not checked or split "
        "twice, a submission already extracted is skipped (so an interrupted "
        "run picks up where it stopped, and anything you have graded is never "
        "extracted over), and auto-grading runs again only when the answer "
        "key or the set of submissions changed. The log names each stage as "
        "run or reused. Tick 'Start over (ignore the earlier run)' under Advanced "
        "settings to check, split and auto-grade again regardless (--fresh). A "
        "scan with a serious problem stops the run before anything is graded "
        "until you fix it or tick 'Grade it anyway' (--force).",
    ),
    (
        "What each stage does",
        "Scan check: turns upside-down pages upright and reports blank "
        "sheets, low resolution, clipped margins and a page count that isn't "
        "a whole number of submissions. Split: cuts the bundle into "
        "per-student PDFs at the boundaries it detects from page numbers and "
        "Name/ID headers. Extract: reads each PDF with OCR — and the "
        "handwriting model when 'Read handwriting on scans' is on in AI setup "
        "— into gradable .submission.md files plus response images; with a "
        "roster, a name found without a usable ID is matched and its ID "
        "filled in, and tied names are saved for you to resolve in the review "
        "queue (Shift+R). Auto-grade: scores the answers your rubric settles "
        "outright and, with 'Suggest grades for written answers' on in AI "
        "setup, suggests rubric choices for the rest — see AI SETUP below. "
        "CLI: scan-check, submission-splitter, submission-extractor, "
        "automated-grading.",
    ),
    (
        "Grade while it runs",
        "Extraction is the longest stage, so don't wait for it: with 'Start "
        "grading as submissions arrive' ticked (the default) the grading "
        "workspace opens on the first submission that is ready, and the rest "
        "of the class appears as each one lands. It needs the answer key.",
    ),
    (
        "Settings, and what is remembered",
        "Settings holds what most runs need: pages per submission, the roster, "
        "the blank template, grading as submissions arrive, sorting "
        "out-of-order pages and drafting rubric items. Each Run Pipeline "
        "saves what you chose. What describes the assignment is saved inside "
        "the class folder (.opengrader/pipeline-settings.json) and comes back "
        "when you choose that folder again; parallel jobs, grading as "
        "submissions arrive and the folder you used last are saved for this "
        "computer. 'Grade it anyway' and 'Start over' are never remembered.",
    ),
    (
        "Advanced settings",
        "Collapsed at the bottom of the card. 'Override what was found' "
        "points the run at a scan or answer key the folder scan did not pick. "
        "'Use a region map (your own "
        "assignment)' is for a paper Opengrader didn't print, instead of a "
        "blank template — see Region map below. 'This stack mixes several "
        "class sections' takes one roster per section and routes each student "
        "by which roster holds their ID; the section is saved on every "
        "submission and can scope item analysis (s, then f). 'Set how many to "
        "extract at once' overrides the automatic count of CPU cores "
        "(--jobs).",
    ),
    (
        "Create answer key",
        "In Helper Tools, beside Generate template — the two things you do "
        "before the exam is printed. Builds the QA answer-key markdown from a "
        "plain-text assignment file (.md/.txt) or a PDF. A born-digital PDF "
        "is read directly from its text layer; a scanned or image-based PDF "
        "is read with OCR, and with the handwriting model too when 'Read "
        "handwriting on scans' is on in AI setup (--llm-read-scans). On the "
        "start-up page, press t for copy-paste format templates. CLI: "
        "create-answer-key.",
    ),
    (
        "Re-read unclear answers",
        "In Helper Tools. A second pass over a class already prepared: only "
        "the submissions holding a low-confidence transcription are re-read, "
        "and a new reading is kept only where it beats the old one. It uses "
        "the class folder and options set in Prepare an assignment. CLI: "
        "submission-extractor --refine --refine-dpi 450.",
    ),
]

_LOCAL_AI_ROWS: list[tuple[str, str]] = [
    (
        "Where you set it up",
        "One place: 'AI setup' under Helper Tools in Tools (F3, classic). Which "
        "provider, which two models, which server, and how confident a grade "
        "suggestion has to be — answered once, saved for this computer, and "
        "read by everything: Prepare an assignment, the helper tools, and the "
        "same commands run from a terminal. No step asks you about a model, "
        "because which model you have installed is a fact about the machine, "
        "not about today's assignment.",
    ),
    (
        "Blank = recommended",
        "Every field there is optional and blank means 'use whatever "
        "Opengrader currently recommends' — so an upgrade moves you to a "
        "better default instead of leaving you pinned to whatever was current "
        "the day you first opened the screen. Fill a field in only to "
        "override it; the placeholder names what blank resolves to.",
    ),
    (
        "Test connection",
        "The button beside Save. It asks the server what it actually has, "
        "using the values you have typed rather than the saved ones, so you "
        "can try a model you turn out not to have and close without changing "
        "anything. A failure prints the exact command that fixes it. It also "
        "times one small real request, so 'How long to wait for the AI' is a "
        "measurement rather than a guess. Worth "
        "pressing before a long extraction rather than after.",
    ),
    (
        "How long to wait for the AI",
        "The limit on a single request, 120 seconds unless you change it. It "
        "matters because a model that runs out of time does not raise an "
        "error you will see — the answer simply comes back empty, an hour "
        "into an extraction. On a slower computer one handwritten answer can "
        "exceed 120s, and the very first request of a run also waits for the "
        "model to load. Don't guess: press Test connection, which reports "
        "what this computer actually took and offers a roomier limit when "
        "the measurement is close to the one you have. Headless: "
        "'doctor --time-ai' reports the same measurement.",
    ),
    (
        "Only use suggestions I have measured",
        "Off by default. On, Auto-grade refuses to write AI suggestions "
        "until the minimum confidence came from an actual measurement rather "
        "than a number somebody typed — it names Calibrate AI grading as the "
        "fix and stops before touching a file, instead of grading a class "
        "against a threshold nobody checked. Accepting a calibration result "
        "records that it was measured; retyping the confidence by hand "
        "clears that, because it is then a guess again.",
    ),
    (
        "What it powers",
        "Two optional jobs — everything works without them — and each has "
        "its own switch at the top of AI setup: 'Read handwriting on scans' "
        "(used by Extract, by Split for a page number nothing else can read, "
        "and by Create answer key on a scan) and 'Suggest grades for written "
        "answers' (used by Auto-grade / --llm). They are separate because "
        "they are separate decisions: a scanned exam whose rubric settles "
        "every answer wants the first and not the second.",
    ),
    (
        "How grading works",
        "Only unmatched free responses are sent, and only their transcribed "
        "Response text — never the image. The model just picks from your "
        "existing rubric; the score is computed from the rubric, so it can't "
        "invent points. Each suggestion is pre-selected and tagged [AI · NN%] "
        "for you to confirm or change. MC, numeric, and True/False never use it.",
    ),
    (
        "Should you trust it?",
        "Don't guess. Grade some free responses by hand first, then press "
        "shift+A (or open Calibrate AI grading in Tools, F3 in classic). It re-grades those same answers with the model and "
        "reports how often it agreed with you, per model and per confidence "
        "level — then names the minimum confidence the evidence supports and "
        "offers to save it into AI setup, where every tool picks it up. It "
        "never writes a grade. Headless: "
        "'uv run llm-eval --solutions solutions.md --input <submissions>'.",
    ),
    (
        "Handwritten math",
        "AI setup's 'Transcribe handwritten maths as LaTeX' (--llm-math) "
        "reads fractions, exponents, integrals, etc. as LaTeX so they survive "
        "instead of being flattened. On the grade screen, a 'View rendered math' "
        "button shows the LaTeX rendered beside the handwriting. Which model "
        "does the reading is the 'Maths reader' choice in AI setup: the vision "
        "model reads the whole "
        "answer and marks the maths up inline (right when students explain "
        "their working), while pix2tex is a dedicated maths model that is much "
        "better on dense notation but reads the answer box as ONE equation — "
        "pick it for an assignment whose answers are equations. pix2tex is an "
        "optional install (uv sync --extra math); without it the option is "
        "listed as not installed and the vision model keeps working.",
    ),
    (
        "The quick way",
        "Press 'Set up local AI' in AI setup. It starts the AI server, tells "
        "you which models are missing, and on a second press downloads them "
        "with the progress on screen — the models named in AI setup, or the "
        "recommended ones where a field is blank, and only for the jobs that "
        "are ticked: the handwriting model for reading scans or names, the "
        "grading model for suggesting grades. On a Mac with Homebrew it "
        "can install Ollama as well. Steps 1-3 below are the same thing by "
        "hand.",
    ),
    (
        "1 · Install Ollama",
        "macOS / Windows: download from https://ollama.com/download (or "
        "'brew install ollama'). Linux: "
        "'curl -fsSL https://ollama.com/install.sh | sh'.",
    ),
    (
        "2 · Start the server",
        "Leave 'Start the AI automatically when needed' ticked in AI setup and "
        "a step starts 'ollama serve' for you if it isn't already running, at "
        "http://127.0.0.1:11434. Untick it — on a metered connection, or where "
        "you want to control when a multi-gigabyte download happens — and a "
        "step reports that the AI isn't up rather than starting it. Running "
        "the CLIs by hand? Start it yourself or let the desktop app.",
    ),
    (
        "3 · Get the model",
        "With that same box ticked, a step also downloads the model it needs "
        "on first use, reporting how far it has got. By hand: for "
        f"handwriting, run 'ollama pull {DEFAULT_VISION_MODEL}'; for grading, "
        f"'ollama pull {DEFAULT_TEXT_MODEL}' — several GB each. 'ollama "
        "list' shows what you have, and 'Check this computer' in Tools (F3, "
        "classic) reports whether the models AI setup names are actually "
        "installed.",
    ),
    (
        "Choosing a model",
        "Any model you have pulled works ('ollama list' shows them). Extract "
        "needs a vision model; free-response grading also runs on a text-only "
        "one. Sizes below are the rough download and RAM. Name them in AI "
        "setup — one field for the handwriting model, one for the grading "
        "model; leave either blank for the default.",
    ),
    (
        "· the defaults",
        f"{DEFAULT_VISION_MODEL} reads handwriting and {DEFAULT_TEXT_MODEL} "
        "suggests grades — what a blank field in AI setup means.",
    ),
    (
        "· vision — light",
        "qwen2.5vl:3b   ~3 GB   — for lower-RAM machines.",
    ),
    (
        "· vision — best",
        "llama3.2-vision:11b (~8 GB) or qwen2.5vl:32b (~21 GB) — tough scans.",
    ),
    (
        "· text — default",
        "llama3.1:8b (~4.9 GB) or qwen2.5:7b (~4.7 GB) — grading only.",
    ),
    (
        "· text — light",
        "llama3.2:3b or qwen2.5:3b   ~2 GB.",
    ),
    (
        "Where it is saved",
        "A small config.json in your user config folder — Library/Application "
        "Support/Opengrader on macOS, .config/opengrader on Linux, AppData "
        "Roaming/Opengrader on Windows. It describes this computer, so it is "
        "deliberately NOT inside an assignment folder: a folder handed to a "
        "TA must not carry a server only you can reach. It records only what "
        "you actually changed.",
    ),
    (
        "From a terminal",
        "An explicit flag wins over the saved settings, which win over the "
        "built-in defaults. Edit config.json to change your machine defaults; "
        "its $schema field gives editors completion and validation.",
    ),
    (
        "Cloud AI?",
        "Not today: 'Ollama on this computer (offline)' is the only provider "
        "in the list, and nothing you grade leaves the machine. The provider "
        "is a dropdown rather than an assumption because that is the honest "
        "shape — if a hosted provider is ever added it appears there with its "
        "own models and its own API-key row, and the screen states plainly "
        "that choosing it sends student handwriting off this computer. "
        "Offline stays the default, and a key is read from this computer's "
        "password store or an environment variable, never written to an "
        "Opengrader file.",
    ),
    (
        "If it isn't ready",
        "If the 'ollama' binary is missing, a step reports it (install Ollama, "
        "then retry) — 'Check this computer' names the exact command. You can "
        "always skip the AI in Auto-grade: objective "
        "answers are still scored and free responses are left for you to grade "
        "by hand.",
    ),
]

_TERMINAL_GRAPHICS_ROWS: list[tuple[str, str]] = [
    (
        "Best terminals",
        "Ghostty, kitty, WezTerm, iTerm2, Konsole, foot, Windows Terminal "
        "1.22+, or VS Code with terminal.integrated.enableImages. Each of "
        "these draws real pixels, so handwriting is legible in place.",
    ),
    (
        "No image support",
        "macOS Terminal.app, GNOME Terminal, PuTTY, and tmux/screen without "
        "passthrough. Grading still works: crops fall back to a coarse "
        "colour-block preview and o opens the real image outside the "
        "terminal.",
    ),
    (
        "o (grade screen)",
        "Open the current crop in your desktop image viewer. Set "
        "OPENGRADER_IMAGE_VIEWER to choose a specific viewer command (a {} "
        "in it is replaced with the file path).",
    ),
    (
        "--check-graphics",
        "uv run tui --check-graphics prints the protocol chosen, how it was "
        "detected, and the viewer that o would launch.",
    ),
    (
        "--graphics PROTOCOL",
        "Force kitty, iterm2, sixel, halfcell, or unicode when detection "
        "guesses wrong (equivalently OPENGRADER_GRAPHICS=<protocol>). Useful "
        "over SSH, inside tmux with passthrough enabled, or in an unusual "
        "emulator.",
    ),
]

_SCRIPT_ROWS: list[tuple[str, str]] = [
    (
        "Doctor",
        "Checks that this computer has everything Opengrader needs and prints "
        "the exact command to fix whatever is missing: the Python install, the "
        "packages, the Tesseract text recognition that reads scans, the "
        "optional Ollama server and its models, and whether this terminal can "
        "display answer crops. Run it on a new machine, and any time something "
        "misbehaves for no obvious reason. Add --smoke to build a template PDF "
        "end to end. Read-only. CLI: doctor. Also in Tools (F3, classic) as "
        "'Check this computer', which on a Mac with Homebrew adds an Install "
        "Tesseract button when Tesseract is missing — the one thing there "
        "that changes this computer, and only when pressed.",
    ),
    (
        "AI providers",
        "Connect an AI service other than the local one: a service your "
        "department runs, or one you pay for. Each provider is a small file "
        "naming the service's address, its models and the environment "
        "variable that holds your key (never the key itself). Add one from a "
        "shipped example, test it, or remove it. A service that is not on "
        "this computer or your local network receives student work. Choose "
        "the provider to use in AI setup. CLI: opengrader-providers. Also in "
        "Tools (F3, classic) as 'AI providers'.",
    ),
    (
        "Data-flow report",
        "States, for the current AI setup, which kinds of student data would "
        "leave this computer and which host would receive them. Student names "
        "and IDs are never sent to a cloud service. Printable, to hand to a "
        "department that asks. Read-only. CLI: opengrader-data-flow.",
    ),
    (
        "AI setup",
        "The one place the optional AI is configured: provider, the "
        "handwriting model, the grading model, the server, the maths reader, "
        "and how confident a grade suggestion must be before it is offered. "
        "What you save is read by every step and every command, including "
        "ones run from a terminal — no tool asks you about a model any more. "
        "In Tools (F3, classic), right after 'Check this "
        "computer', which is what tells you whether those models are "
        "installed. See AI SETUP above for the full detail.",
    ),
    (
        "Grade check",
        "The other end of the scan check: a pre-flight before grades leave the "
        "tool. One command answers 'is this ready to hand back?' instead of "
        "running the review queue, the claims list, the regrade list and the "
        "curve separately. Exits 2 on anything blocking, so it can gate a "
        "script. Read-only. CLI: grade-check.",
    ),
    (
        "Grade release",
        "Record what every student was handed, then ask later what moved. "
        "'grade-release create' snapshots each student's grade question by "
        "question; 'grade-release diff' lists everything that has changed "
        "since — a regrade, a re-curve, a merge. It never freezes a grade: "
        "releasing is precisely when regrades start, so the record exists to "
        "explain changes, not to prevent them. CLI: grade-release. Also "
        "shift+L in the app.",
    ),
    (
        "Scan check",
        "Pre-flight a scan before grading it: pages fed in upside down or "
        "sideways, sheets that ended up out of order, blank sheets that shift a "
        "fixed-count split, pages scanned below the resolution handwriting OCR "
        "needs, content clipped off at the margin, and a page count that isn't a "
        "whole number of submissions. These all fail silently otherwise — an "
        "upside-down page still splits, still extracts, and produces an empty "
        "answer, and a swapped sheet is worse still: it grades cleanly against "
        "the wrong question. --fix writes an upright copy; your original scan is "
        "never modified. Out-of-order pages are reported here and put right by "
        "the splitter's --fix-order, which only acts when the page numbers came "
        "from QR codes, a PDF text layer or a blank template — where they were "
        "merely read off the scan it tells you and leaves them alone, since a "
        "misread number would move a page that was already correct. Page order "
        "can only be checked when --pages-per-submission or --blank-template "
        "says how long a submission is. CLI: scan-check.",
    ),
    (
        "Photo import",
        "For a class that hands in phone photos instead of one scanned bundle. "
        "Each photo is straightened — by the sheet's corner registration marks "
        "when it has them, by the page's own edges otherwise — and the photos "
        "belonging to one student are bound into a single PDF. That is exactly "
        "what splitting a scan produces, so Prepare an assignment takes that "
        "folder as its scanned bundle (it is filled in for you) and nothing "
        "downstream ever learns the pages came from a phone. Students are grouped by "
        "subfolder if there is one each, otherwise by the leading part of the "
        "filename (s1234-p1.jpg, s1234-p2.jpg), or set a fixed number of photos "
        "per student. HEIC (iPhone) photos need one extra install, and it names "
        "the command. Your photos are only ever read. In Tools (F3, classic) as "
        "'Import phone photos'. CLI: photo-import.",
    ),
    (
        "Region map",
        "Grade an assignment Opengrader didn't generate — the one written in "
        "Word, typeset in LaTeX, or photocopied from a paper original years "
        "ago. Draw a box around each answer area once, on a blank copy, and "
        "every scan of that assignment is cropped against those boxes: no text "
        "layer needed, and nothing else in the pipeline changes. 'Draw answer "
        "regions' in Tools (F3, classic) opens the editor with "
        "the boxes already proposed for you to correct — j/k select, arrows "
        "move, shift+arrows resize, and the panel on the right sets each "
        "question's number, points and type. Saving is refused while anything "
        "would crop the wrong thing. The map it writes fills straight into the "
        "'Use a region map' box under Advanced settings in Prepare an assignment "
        "(which replaces the blank template — "
        "they answer the same question, so only one is used). A map is tied to "
        "the exact PDF it was drawn on and is refused on any other, because a "
        "re-export moves the boxes and a mis-crop is silent. "
        "CLI: region-map propose/preview/validate, then submission-extractor "
        "--region-map.",
    ),
    (
        "Work claims",
        "Divide grading between several TAs so nobody marks a paper someone "
        "else already did. claim divide --graders alice,bob,carol splits the "
        "class into contiguous slices (by question, or by student); claim "
        "status shows coverage, gaps, and any work two people are both holding. "
        "Claiming work someone else holds is refused rather than silently "
        "double-booked. Coordination, not permission — it never blocks a save. "
        "CLI: claim divide/add/list/status/release/complete.",
    ),
    (
        "Share with a grader",
        "Work claims divide a class everyone opens in one shared folder. This "
        "is for the other arrangement: a TA takes a copy away and grades it on "
        "their own machine. The copy replaces every name and student id with a "
        "code and leaves the scanned work behind, so handing it over gives "
        "nothing away — you keep the one file that turns the codes back into "
        "students, and merging their work back needs it. Merging is a real "
        "three-way merge: answers only they graded are taken, answers only you "
        "graded are kept, and anything you both changed is shown for you to "
        "settle one row at a time. An applied merge can be undone with "
        "grade-restore --without-actor merge:<name>. In the app: shift+E makes "
        "a copy and shift+M merges one back; also Share with another grader in "
        "Tools (F3, classic). CLI: grade-merge pack/list/preview/apply.",
    ),
    (
        "Backup & integrity",
        "Grades, answer images and the audit journal are files in one folder, "
        "with no server holding a second copy. Record a checksum of every file "
        "and you can ask later what moved — and a changed grade file is "
        "reported as explained (grading was recorded for that student since) "
        "or unexplained (nothing in the audit trail says why), which is the one "
        "worth chasing. Append-only logs are checked by their recorded lines, "
        "so normal growth is not noise but a rewritten history is flagged. The "
        "archive it writes is read back and verified before it claims success, "
        "and restoring always creates a new folder. In the app: shift+B; also "
        "Backup & integrity on the start-up screen. CLI: grade-backup "
        "snapshot/verify/backup/check/restore.",
    ),
    (
        "Grading policy",
        "Lock a submissions folder to anonymous grading for a multi-grader "
        "team (graders inherit it — the t toggle is disabled), or strip "
        "student names out of the graders' copies entirely, keeping a name "
        "map to restore before export. It also holds 'Randomize student order "
        "per grader': each grader walks the class in their own fixed order "
        "rather than alphabetically, so whoever gets marked while you are tired "
        "is not the same students every time. The order is stable per grader — "
        "reopening returns you to the same sequence — and only the traversal "
        "moves: the gradebook, exports and every report keep their usual order. "
        "Also the start-up screen's Set grading policy tool. "
        "CLI: grading-policy.",
    ),
    (
        "Needs review",
        "List submissions that likely need a human — probable OCR/extraction "
        "errors, missing/duplicate IDs, and low-confidence AI grades — most "
        "severe first. Same list as the shift+R pop-up. CLI: review-queue.",
    ),
    (
        "Regrade requests",
        "Track student grade disputes as first-class items. File one with "
        "shift+F (teacher mode) or the CLI when a student contests a grade "
        "(add --student ID [--question qN] --reason ...); open requests surface "
        "at the top of the review queue and the shift+R pop-up, routing straight "
        "to the disputed question. Resolve or deny when done. "
        "CLI: regrade add/list/resolve/deny.",
    ),
    (
        "Calibrate AI grading",
        "Measure the LLM grader before trusting it with a class. Re-grades free "
        "responses a human already marked and reports agreement per model, per "
        "question type, and per confidence bucket, then recommends the minimum "
        "confidence that evidence supports (--llm-min-confidence on the CLI; "
        "in the app, accepting it saves the number into AI setup). Answers "
        "already graded by "
        "a previous AI run are excluded — scoring a model against its own "
        "earlier verdict measures repeatability, not accuracy. Read-only. Same "
        "tool as the shift+A pop-up. CLI: llm-eval.",
    ),
    (
        "Check transcription accuracy",
        "Every time you fix a misread answer with x, Opengrader records what "
        "the machine read beside what you typed. transcribe-eval reads that log "
        "back and reports how often each reader (Tesseract, the vision model) "
        "was right, broken down by the confidence it claimed, then recommends "
        "the --escalate-confidence cut-off for submission-extractor "
        "--llm-escalate. It measures only answers somebody checked, and says "
        "what share of the assignment that was — an answer nobody looked at is "
        "not evidence it was read correctly. Read-only. "
        "CLI: transcribe-eval --input <submissions>.",
    ),
    (
        "Re-read only what OCR read badly",
        "submission-extractor --llm-escalate reads every answer with the fast "
        "local OCR first and asks the vision model only about the ones it read "
        "poorly or not at all, instead of sending every crop to the model. On "
        "an assignment whose answers are mostly short and legible that is a "
        "fraction of the model calls, and it cannot cost you an answer: a model "
        "that reads nothing leaves the OCR text in place. Also a checkbox under "
        "Transcribe answers on the setup screen.",
    ),
    (
        "Learning outcomes",
        "Tag a question in your QA answer key with what it measures, by adding "
        "an **Outcome** bullet under it — 'Outcome CLO3', or 'Outcome "
        "ABET-2', or any wording you use. Repeat it for a question that serves "
        "several, and tag a multi-part question once to cover all its parts "
        "(a part can override with its own). Then: shift+S then o "
        "shows per-outcome mastery for this assignment, grades-export adds a "
        "column per outcome, the return bundle's analysis PDF gains a page, and "
        "the course gradebook rolls mastery up across the whole semester — the "
        "artifact a programme review asks for. Mastery is measured in points, "
        "so an outcome on a 20-point essay counts for more than one on a "
        "2-point definition, and a question nobody has graded counts for "
        "nothing rather than as a zero. Entirely optional: a key with no "
        "Outcome bullets behaves exactly as before.",
    ),
    (
        "Course gradebook",
        "A whole semester in one table: every student's weighted course grade "
        "and letter, an at-risk list, how each student is trending, and — if "
        "your answer keys tag learning outcomes — mastery across the whole "
        "course, which is the evidence a programme review asks for. Open it "
        "from the start-up screen's Course gradebook tool; you don't have to "
        "write the course file by hand — press New… there, point Scan at "
        "the folder holding your assignments, and it fills in a row for each "
        "one. Weight is how much an assignment counts, and it is "
        "relative — 10/10/30/50 and 1/1/3/5 describe the same course — so the "
        "builder shows each row's live share of the final grade and you just "
        "adjust until it says what you mean. An assignment a student has no "
        "graded work for is left out of "
        "their average until you say missing = \"zero\". It reads your "
        "assignment folders and never writes into them. CLI: course-grades.",
    ),
    (
        "Grade history",
        "Show the durable audit log of who changed which grade, from what to "
        "what, and when (all graders, all sessions). Same data as the shift+H "
        "pop-up. CLI: grade-history.",
    ),
    (
        "Grading stats",
        "Summarize grading throughput from that same journal — actions, active "
        "time, sessions, and pace per grader. Same view as the shift+V pop-up. "
        "CLI: grading-stats.",
    ),
    (
        "Calibrate",
        "Are the graders marking this the same way? `calibrate pack --graders "
        "alice,bob` cuts one anonymous, ungraded packet per grader, all "
        "holding the same sampled students under different codes, so everyone "
        "marks the same answers independently — a norming round. `calibrate "
        "report --set <name>` compares the returned packets: who is harsher, "
        "how often they agree, and on which answers they split. With no --set "
        "it reads the audit journal instead, which needs no meeting and is the "
        "only source that can show drift over a session. Read-only apart from "
        "pack. Same view as the shift+I pop-up. CLI: calibrate.",
    ),
    (
        "Answer groups",
        "How much distinct work an assignment actually contains: per question, "
        "the responses that came in, how many distinct answers that is, and "
        "how many remain once near-identical ones are merged. --suggestions "
        "prints each proposed merge with the differences between the answers "
        "marked, which is the same list the grade screen's shift+U view "
        "offers. "
        "Read-only — it never merges or grades anything. CLI: answer-groups.",
    ),
    (
        "Grade restore",
        "Undo grading that is already saved, using that same audit journal. "
        "Rewind the whole folder to any past moment (--at 2h, --at "
        "2026-08-02T14:30), or undo one grader or one batch run wherever "
        "nobody has edited the same answer since (--without-actor NAME). "
        "diff previews, apply --yes writes, and the restore is itself "
        "journalled, so it can be undone in turn. An automated-grading run "
        "records itself under 'automated-grading' (and 'automated-grading:llm' "
        "for AI-applied grades), so a bad batch can be dropped without "
        "touching anyone's hand marking. CLI: grade-restore show/diff/apply/log.",
    ),
    (
        "Bubble sheets (OMR)",
        "For a large multiple-choice class: qa-file generate-template "
        "--bubble-sheet prints a scantron-style answer sheet (add --roster for "
        "one QR-identified sheet per student), and submission-extractor "
        "--bubble-sheet QA_FILE reads the filled sheets back. Marks are "
        "measured, not recognized, so it needs no Tesseract, no vision model "
        "and no Ollama at all, and it is far faster than handwriting "
        "extraction. Two circles filled on one question is reported as "
        "unreadable rather than guessed, and lands in the needs-review queue. "
        "Questions that are not multiple choice cannot be bubbled; they are "
        "listed when the sheet is generated and still get a block to grade.",
    ),
    (
        "Scrambled forms",
        "Print a whole batch at once — one sheet per roster student, or just "
        "--copies N — and add --shuffle to give every sheet its own question "
        "order so neighbours cannot copy by position. Each sheet prints a short "
        "form code (also in its QR); extraction re-derives that sheet's order "
        "from it, so you keep nothing and can reprint any time. Scan the whole "
        "page: a sheet whose form code cannot be read is left unread rather "
        "than scored against the wrong questions, and --bubble-form KEY types "
        "the printed code back in to rescue it. Bubble sheets only — a written "
        "template is read back against one blank layout, so a per-sheet order "
        "could not be recovered.",
    ),
    (
        "Score curve",
        "Set a reversible assignment curve (bonus / scale / square-root) applied "
        "to every export and the summary page, raw shown alongside. Or solve for "
        "a target class average and let it pick the value. Same editor as the "
        "QA picker's c pop-up. CLI: curve show/set/suggest/clear.",
    ),
    (
        "Consistency audit",
        "Flag answer groups where students who gave the same answer received "
        "different grades — the fairness check to run before export. Same list "
        "as the shift+C pop-up. CLI: consistency-audit.",
    ),
    (
        "Similarity audit",
        "The academic-integrity check, in two views. Free responses: students "
        "whose long written answers are near-identical. Press o for objective "
        "answers: pairs who gave the same WRONG multiple-choice answers far "
        "more often than the class's own spread of wrong answers allows — the "
        "only integrity check a bubble sheet can have, since a text diff calls "
        "every honest pair 100% alike on 'B'. Same views as the shift+P pop-up. "
        "CLI: similarity-audit (add --solutions for the objective half).",
    ),
    (
        "Item analysis",
        "Per-question difficulty and separation (discrimination), plus "
        "multiple-choice distractor breakdowns — which questions to fix or "
        "reuse. Same view as the shift+S pop-up. CLI: item-analysis.",
    ),
    (
        "Canvas export",
        "Post scores and per-question comments to Canvas via canvas-sak "
        "(dry run by default). Press x in the QA picker. "
        "CLI: canvas-export.",
    ),
    (
        "Return bundle",
        "Package the class into one hand-back folder: gradebook CSV, each "
        "student's annotated + summarized PDF, and a class analysis report. "
        "Pick which documents from the dropdown. Press b in the QA picker. "
        "CLI: return-bundle (or grades-export for just the CSV).",
    ),
    (
        "Returnable PDF",
        "Each graded student's annotated PDF gets a Grade Summary page appended "
        "at the end — per-question points, rubric selections, and comments, plus "
        "the total — refreshed on every save and listing ungraded questions "
        "explicitly, so the scan can be handed back as-is.",
    ),
    (
        "Generate template",
        "Helper: render a printable blank template PDF (one per version) from "
        "a QA file. In Tools (F3, classic). Tick 'Add QR codes' "
        "for reliable auto-splitting/reordering (identical sheets), or add a "
        "roster to reveal per-student options: one anonymous QR sheet per student "
        "(prints the id, not the name) plus a teacher master key (id -> name) on "
        "page 1. CLI: qa-file generate-template --qr / --roster.",
    ),
]


class HelpScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }

    #help-dialog {
        width: 80%;
        max-width: 100;
        height: 85%;
        background: $surface;
        border: round $accent;
        border-title-align: left;
        border-title-color: $accent;
        border-title-style: bold;
        padding: 1 2;
    }

    #help-hint {
        width: 100%;
        height: auto;
        color: $text-muted;
    }

    #help-body {
        height: 1fr;
        margin-top: 1;
        scrollbar-size: 1 1;
    }

    HelpScreen .help-section {
        width: 100%;
        height: auto;
        color: $text-muted;

        margin-top: 1;
        text-style: bold;
    }

    HelpScreen .help-section.-first {
        margin-top: 0;
    }

    HelpScreen .help-caption {
        width: 100%;
        height: auto;
        color: $text-muted;
        margin-bottom: 1;
    }

    HelpScreen .help-row {
        width: 100%;
        height: auto;

        margin-bottom: 1;
    }

    HelpScreen .help-key {
        width: 18;
        padding-right: 2;
        color: #EBCB8B;
        text-style: bold;
    }

    HelpScreen .help-tool {
        width: 24;
        padding-right: 2;
        color: $accent;
        text-style: bold;
    }

    HelpScreen .help-desc {
        width: 1fr;
        height: auto;
    }

    #help-buttons {
        height: auto;
        align-horizontal: right;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close_help", "Close"),
        Binding("question_mark", "close_help", "Close", key_display="?"),
    ]

    @staticmethod
    def _section(
        title: str,
        caption: str,
        rows: list[tuple[str, str]],
        *,
        key_class: str = "help-key",
        first: bool = False,
    ) -> Iterable[Static | Horizontal]:
        classes = "help-section -first" if first else "help-section"
        yield Static(f"[b]{title}[/b]", classes=classes)
        if caption:
            yield Static(caption, classes="help-caption")
        for key, description in rows:
            yield Horizontal(
                Static(key, classes=key_class, markup=False),
                Static(description, classes="help-desc", markup=False),
                classes="help-row",
            )

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog") as dialog:
            dialog.border_title = "▸ Help"
            yield Static(
                "Every command, grouped by screen. Press esc or ? to close.",
                id="help-hint",
            )
            with VerticalScroll(id="help-body"):
                yield from self._section("ANYWHERE", "", _GLOBAL_ROWS, first=True)
                yield from self._section(
                    "REPORTS & AUDITS",
                    "Hold shift and the letter opens a report about the whole "
                    "class — from the QA picker or mid-grading, either way. "
                    "The ones marked teacher mode name people; the rest show "
                    "student IDs, so they stay safe while grading anonymously.",
                    _REPORT_ROWS,
                )
                yield from self._section(
                    "SHARING & BACKUPS",
                    "Also shift and a letter, but these change something "
                    "instead of reporting on it: hand this class to another "
                    "grader, take their marking back, or protect the folder. "
                    "From the QA picker or mid-grading, either way — and "
                    "nothing is written until you confirm it.",
                    _SHARING_ROWS,
                )
                yield from self._section(
                    "QA PICKER",
                    "Choose one question, then grade it for every student.",
                    _QA_PICKER_ROWS,
                )
                yield from self._section(
                    "GRADING",
                    "One question at a time, student by student.",
                    _GRADING_ROWS,
                )
                yield from self._section(
                    "RUBRIC EDITOR",
                    "Open with e while grading.",
                    _RUBRIC_EDITOR_ROWS,
                )
                yield from self._section(
                    "PIPELINE",
                    "Prepare an assignment: one card on the start-up screen "
                    "takes a scan all the way to graded work, and running it "
                    "again does only what is left. Each stage is also a "
                    "command for a terminal: uv run opengrader --help lists "
                    "every one, and uv run opengrader <command> --help "
                    "explains one. The standalone names (uv run "
                    "submission-splitter, …) still work too.",
                    _PIPELINE_ROWS,
                    key_class="help-tool",
                )
                yield from self._section(
                    "AI SETUP (OPTIONAL)",
                    "Set up once in Helper Tools → AI setup, then used by "
                    "every step and every command. Powers handwriting reading "
                    "(Extract) and free-response grade suggestions "
                    "(Auto-grade). Runs on this computer; nothing you grade "
                    "leaves it.",
                    _LOCAL_AI_ROWS,
                    key_class="help-tool",
                )
                yield from self._section(
                    "TERMINAL IMAGES",
                    "How answer crops get drawn, and what to do when they "
                    "don't. Run tui --check-graphics to see what was detected.",
                    _TERMINAL_GRAPHICS_ROWS,
                    key_class="help-tool",
                )
                yield from self._section(
                    "TOOLS & SCRIPTS",
                    "Standalone commands you reach for as needed, in or out "
                    "of the app.",
                    _SCRIPT_ROWS,
                    key_class="help-tool",
                )
            with Horizontal(id="help-buttons"):
                yield Button("Close", id="help-close", compact=True)

    def action_close_help(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#help-close")
    def _on_close_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        self.dismiss(None)

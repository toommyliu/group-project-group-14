"""What the *Prepare an assignment* card remembers between sessions.

Two files, split by what a setting describes:

* **the assignment** — pages per submission, roster, template, region map,
  page sorting — in `<class folder>/.opengrader/pipeline-settings.json`, beside
  the run ledger. Quiz 1's six pages must not follow you to quiz 2, and the
  file travels with the folder (paths inside it are stored relative to it).
* **this computer** — parallel jobs, whether grading opens as submissions
  arrive, whether a run auto-grades, the class folder used last, the points
  Create answer key gives a question that states none — in `pipeline.json`
  beside the AI settings.

One-shot overrides ("grade it anyway", "start over") are deliberately not
fields here: a remembered "ignore scan warnings" would silently grade the next
bad scan.

Both files are a convenience. A missing or malformed one reads as "nothing
saved", and only saving can raise (`OSError`).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

FOLDER_SETTINGS_DIRNAME = ".opengrader"
FOLDER_SETTINGS_FILENAME = "pipeline-settings.json"
MACHINE_SETTINGS_FILENAME = "pipeline.json"

_VERSION = 1

# What Run Pipeline does once the card is filled in: "" asks every time;
# the others are a remembered answer, run without asking.
RUN_ASK = ""
RUN_GRADE_ASAP = "grade-asap"
RUN_GRADE_AT_END = "grade-at-end"
RUN_ONLY = "run-only"
RUN_CHOICES = (RUN_ASK, RUN_GRADE_ASAP, RUN_GRADE_AT_END, RUN_ONLY)

# "Get help from AI" answered once and for all: "" asks; ASSISTANT opens the
# AI assistant's copy screen after every run that leaves answers for a
# person; AI_SETUP goes straight to AI setup.
AI_HELP_ASK = ""
AI_HELP_ASSISTANT = "assistant"
AI_HELP_AI_SETUP = "ai-setup"
AI_HELP_CHOICES = (AI_HELP_ASK, AI_HELP_ASSISTANT, AI_HELP_AI_SETUP)


@dataclass(frozen=True)
class FolderPipelineSettings:
    # Blank means "whatever the folder scan finds"; set only when a person
    # pointed the run somewhere else by hand.
    scan: str = ""
    qa_file: str = ""
    pages_per_submission: int | None = None
    use_roster: bool = False
    roster: str = ""
    use_template: bool = False
    blank_template: str = ""
    use_region_map: bool = False
    region_map: str = ""
    use_section_rosters: bool = False
    # `TSV[=SECTION]` lines, kept exactly as typed.
    section_rosters: tuple[str, ...] = ()
    use_section_scans: bool = False
    # `PDF[=SECTION]` lines, one scan per section, kept exactly as typed. Blank
    # (with the box ticked) means the ones the folder scan finds.
    section_scans: tuple[str, ...] = ()
    fixed_split: bool = False
    reorder_pages: bool = False
    draft_rubrics: bool = False


@dataclass(frozen=True)
class MachinePipelineSettings:
    grade_as_ready: bool = True
    # Opt-in: a run scores what the answer key settles outright only when a
    # person ticked "Auto-grade after reading the scans". Saved for every
    # folder on this computer, not one assignment.
    auto_grade: bool = False
    # Run Pipeline's question answered once and for all (`RUN_CHOICES`);
    # RUN_ASK = ask every time.
    run_choice: str = RUN_ASK
    # The one-time "an AI can help with what auto-grading left" offer after
    # the first auto-graded run has been shown on this computer.
    ai_help_offered: bool = False
    # `AI_HELP_CHOICES`; AI_HELP_ASK = ask every time.
    ai_help_choice: str = AI_HELP_ASK
    # None = let Opengrader pick the number of parallel extractions.
    jobs: int | None = None
    last_class_folder: str = ""
    # None = the standard (`standard_rubric.DEFAULT_QUESTION_POINTS`).
    answer_key_points: float | None = None


# Fields holding one path, stored relative to the class folder when inside it.
_PATH_FIELDS = ("scan", "qa_file", "roster", "blank_template", "region_map")
_BOOL_FIELDS = (
    "use_roster",
    "use_template",
    "use_region_map",
    "use_section_rosters",
    "use_section_scans",
    "fixed_split",
    "reorder_pages",
    "draft_rubrics",
)


def folder_settings_path(folder: str | Path) -> Path:
    return Path(folder) / FOLDER_SETTINGS_DIRNAME / FOLDER_SETTINGS_FILENAME


def machine_settings_path() -> Path:
    from opengrader.core.app_config import settings_path

    return settings_path().parent / MACHINE_SETTINGS_FILENAME


def load_folder_settings(folder: str | Path) -> FolderPipelineSettings | None:
    """The settings saved for this class folder, or None when there are none.

    A file holding only how to split the scan (`save_split_choice`, written by
    `opengrader run`) is not the card's settings: applying it would untick the
    roster and template the folder scan found. Read it with `load_split_choice`.
    """
    root = Path(folder)
    data = _read(folder_settings_path(root))
    if data is None or not set(data) - _SPLIT_CHOICE_ONLY:
        return None

    values: dict[str, object] = {}
    for name in _PATH_FIELDS:
        raw = data.get(name)
        if isinstance(raw, str) and raw:
            values[name] = _absolute(root, raw)
    for name in _BOOL_FIELDS:
        if isinstance(data.get(name), bool):
            values[name] = data[name]
    pages = data.get("pages_per_submission")
    if _positive_int(pages):
        values["pages_per_submission"] = pages
    for name in ("section_rosters", "section_scans"):
        entries = data.get(name)
        if isinstance(entries, list):
            values[name] = tuple(
                entry for entry in entries if isinstance(entry, str) and entry.strip()
            )
    return FolderPipelineSettings(**values)  # type: ignore[arg-type]


def save_folder_settings(folder: str | Path, settings: FolderPipelineSettings) -> Path:
    root = Path(folder)
    body: dict[str, object] = {"version": _VERSION}
    for name in _PATH_FIELDS:
        value = getattr(settings, name)
        if value:
            body[name] = _relative(root, value)
    for name in _BOOL_FIELDS:
        body[name] = getattr(settings, name)
    if settings.pages_per_submission:
        body["pages_per_submission"] = settings.pages_per_submission
    if settings.section_rosters:
        body["section_rosters"] = list(settings.section_rosters)
    if settings.section_scans:
        body["section_scans"] = list(settings.section_scans)
    return _write(folder_settings_path(root), body)


_SPLIT_CHOICE_ONLY = frozenset({"version", "fixed_split"})


def load_split_choice(folder: str | Path) -> bool | None:
    """How this folder's scan is split, once someone chose: True to cut every
    N pages, False to find where each paper starts, None when never chosen."""
    data = _read(folder_settings_path(Path(folder)))
    choice = data.get("fixed_split") if data is not None else None
    return choice if isinstance(choice, bool) else None


def save_split_choice(folder: str | Path, fixed: bool) -> Path:
    """Remember how to split this folder's scan, leaving every other saved
    setting as it is (a folder with none saved gets nothing else)."""
    path = folder_settings_path(Path(folder))
    data = _read(path) or {"version": _VERSION}
    data["fixed_split"] = fixed
    return _write(path, data)


def load_machine_settings() -> MachinePipelineSettings:
    data = _read(machine_settings_path())
    if data is None:
        return MachinePipelineSettings()
    jobs = data.get("jobs")
    last = data.get("last_class_folder")
    points = data.get("answer_key_points")
    return MachinePipelineSettings(
        # Absent or anything but an explicit false keeps the default (on).
        grade_as_ready=data.get("grade_as_ready") is not False,
        # Absent or anything but an explicit true keeps the default (off).
        auto_grade=data.get("auto_grade") is True,
        ai_help_offered=data.get("ai_help_offered") is True,
        ai_help_choice=(
            data["ai_help_choice"]
            if data.get("ai_help_choice") in AI_HELP_CHOICES
            else AI_HELP_ASK
        ),
        run_choice=(
            data["run_choice"] if data.get("run_choice") in RUN_CHOICES else RUN_ASK
        ),
        jobs=jobs if _positive_int(jobs) else None,
        last_class_folder=last if isinstance(last, str) else "",
        answer_key_points=float(points) if _positive_number(points) else None,
    )


def save_machine_settings(settings: MachinePipelineSettings) -> Path:
    body: dict[str, object] = {
        "version": _VERSION,
        "grade_as_ready": settings.grade_as_ready,
        "auto_grade": settings.auto_grade,
    }
    if settings.ai_help_offered:
        body["ai_help_offered"] = True
    if settings.ai_help_choice in AI_HELP_CHOICES and settings.ai_help_choice != AI_HELP_ASK:
        body["ai_help_choice"] = settings.ai_help_choice
    if settings.run_choice in RUN_CHOICES and settings.run_choice != RUN_ASK:
        body["run_choice"] = settings.run_choice
    if settings.jobs:
        body["jobs"] = settings.jobs
    if settings.last_class_folder:
        body["last_class_folder"] = settings.last_class_folder
    if settings.answer_key_points:
        body["answer_key_points"] = settings.answer_key_points
    return _write(machine_settings_path(), body)


def _positive_int(value: object) -> bool:
    # bool is an int in Python; `true` is not a page count.
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _positive_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(path: Path, body: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write beside the target and swap, so a crash never leaves half a file.
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)
    return path


def _relative(root: Path, value: str) -> str:
    try:
        return Path(value).relative_to(root).as_posix()
    except ValueError:
        return value


def _absolute(root: Path, value: str) -> str:
    path = Path(value)
    return str(path if path.is_absolute() else root / path)

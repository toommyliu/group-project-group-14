"""What the *Prepare an assignment* card remembers between sessions.

Two files, split by what a setting describes:

* **the assignment** — pages per submission, roster, template, region map,
  page sorting — in `<class folder>/.opengrader/pipeline-settings.json`, beside
  the run ledger. Quiz 1's six pages must not follow you to quiz 2, and the
  file travels with the folder (paths inside it are stored relative to it).
* **this computer** — parallel jobs, whether grading opens as submissions
  arrive, the class folder used last, the points Create answer key gives a
  question that states none — in `pipeline.json` beside the AI settings.

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
    """The settings saved for this class folder, or None when there are none."""
    root = Path(folder)
    data = _read(folder_settings_path(root))
    if data is None:
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
        jobs=jobs if _positive_int(jobs) else None,
        last_class_folder=last if isinstance(last, str) else "",
        answer_key_points=float(points) if _positive_number(points) else None,
    )


def save_machine_settings(settings: MachinePipelineSettings) -> Path:
    body: dict[str, object] = {
        "version": _VERSION,
        "grade_as_ready": settings.grade_as_ready,
    }
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

from __future__ import annotations

import json
from pathlib import Path

from opengrader.core.submissions.student_file import (
    load_student_submission_markdown,
    set_student_identity_file,
)

# Relative POSIX filename -> student_id and student_name.
NameMap = dict[str, dict[str, str]]


def discover_submission_paths(submissions_path: str | Path) -> list[Path]:
    base = Path(submissions_path)
    if base.is_file():
        return [base]
    direct = sorted(base.glob("*.submission.md"))
    nested = sorted(base.glob("*/*.submission.md"))
    return direct + nested


def _map_key(base: Path, path: Path) -> str:
    if base.is_file():
        return path.name
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def redact_submission_names(submissions_path: str | Path) -> NameMap:
    base = Path(submissions_path)
    name_map: NameMap = {}
    for path in discover_submission_paths(base):
        document = load_student_submission_markdown(path)
        key = _map_key(base, path)
        name_map[key] = {
            "student_id": document.student_id,
            "student_name": document.student_name,
        }
        if document.student_name or document.id_candidates:
            set_student_identity_file(
                path,
                student_id=document.student_id,
                student_name="",
            )
    return name_map


def restore_submission_names(
    submissions_path: str | Path, name_map: NameMap
) -> int:
    base = Path(submissions_path)
    restored = 0
    for path in discover_submission_paths(base):
        entry = name_map.get(_map_key(base, path))
        if not entry:
            continue
        set_student_identity_file(
            path,
            student_id=entry.get("student_id", ""),
            student_name=entry.get("student_name", ""),
        )
        restored += 1
    return restored


def write_name_map(path: str | Path, name_map: NameMap) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(name_map, ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(payload + "\n", encoding="utf-8")
    return target


def read_name_map(path: str | Path) -> NameMap:
    text = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"name map is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("name map must be a JSON object of filename → identity")
    result: NameMap = {}
    for key, value in data.items():
        if not isinstance(value, dict):
            continue
        result[str(key)] = {
            "student_id": str(value.get("student_id", "")),
            "student_name": str(value.get("student_name", "")),
        }
    return result

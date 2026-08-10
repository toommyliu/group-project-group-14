"""Layer 2 anonymity: strip student names from the graders' submission copies.

Layer 1 (`core/policy.py`) hides names behind a UI lock — a *soft* control. The
names are still sitting in the ``.submission.md`` files, so a determined grader
could open one in a text editor. Real enforcement is data minimization: hand the
graders a copy that has no names in it at all. Then there is nothing for a
de-anonymize toggle to reveal, because the data isn't there.

This module is that transform, built entirely on the existing surgical
frontmatter rewrite (`set_student_identity_file`), so question blocks are never
reformatted:

* `redact_submission_names` blanks the ``student_name`` in every submission
  (and, via that rewrite, drops the ``id_candidates`` block — which also carries
  roster names), returning a **name map**: the filename→(id, name) mapping the
  teacher keeps *out* of the graders' copy.
* `restore_submission_names` re-joins that map, writing the names back — the
  teacher runs it on their own copy for Canvas export / records once grading is
  done.

Student *ids* are retained throughout, so graders still grade and export by id
and the teacher can always re-attach names. Resolve any missing/duplicate ids
*before* redacting: identity resolution needs the names, and redaction drops the
candidate lists.
"""

from __future__ import annotations

import json
from pathlib import Path

from opengrader.core.submissions.student_file import (
    load_student_submission_markdown,
    set_student_identity_file,
)

# filename (relative, posix) -> {"student_id": ..., "student_name": ...}
NameMap = dict[str, dict[str, str]]


def discover_submission_paths(submissions_path: str | Path) -> list[Path]:
    """Find ``.submission.md`` files, matching SubmissionFilesProvider's rules."""
    base = Path(submissions_path)
    if base.is_file():
        return [base]
    direct = sorted(base.glob("*.submission.md"))
    nested = sorted(base.glob("*/*.submission.md"))
    return direct + nested


def _map_key(base: Path, path: Path) -> str:
    """A stable, portable name-map key for a submission (robust to blank ids)."""
    if base.is_file():
        return path.name
    try:
        return path.relative_to(base).as_posix()
    except ValueError:
        return path.name


def redact_submission_names(submissions_path: str | Path) -> NameMap:
    """Blank every submission's student name, returning the name map to keep.

    Only files that actually carry a name (or candidate names) are rewritten, so
    an already-anonymous folder is left byte-identical. Ids are preserved.
    """
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
    """Write names from ``name_map`` back into the submissions; return the count.

    Files absent from the map are left untouched, so restoring against a partial
    or stale map is safe. Matches submissions to the map by the same key
    `redact_submission_names` wrote.
    """
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
    """Persist the name map to a JSON file the teacher keeps out of the copy."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(name_map, ensure_ascii=False, indent=2, sort_keys=True)
    target.write_text(payload + "\n", encoding="utf-8")
    return target


def read_name_map(path: str | Path) -> NameMap:
    """Load a name map written by `write_name_map`.

    Raises ``FileNotFoundError`` if missing and ``ValueError`` if malformed —
    restoring names is a deliberate teacher action, so a bad map should fail
    loudly rather than silently skip everyone.
    """
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

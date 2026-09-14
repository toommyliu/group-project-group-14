"""Keep web submission links stable when another scan or local file is added."""

import json
from pathlib import Path


def submission_ids(directory: Path, paths: list[Path]) -> dict[str, int]:
    registry = directory.parent / ".opengrader" / "submission-ids.json"
    ids: dict[str, int] = json.loads(registry.read_text()) if registry.exists() else {}
    if any(type(value) is not int or value < 1 for value in ids.values()) or len(set(ids.values())) != len(ids):
        raise ValueError("Submission ID registry is invalid.")
    changed = False
    next_id = max(ids.values(), default=0) + 1
    for path in paths:
        key = path.relative_to(directory).as_posix()
        if key not in ids:
            ids[key] = next_id
            next_id += 1
            changed = True
    if changed:
        registry.parent.mkdir(parents=True, exist_ok=True)
        temporary = registry.with_suffix(".tmp")
        temporary.write_text(json.dumps(ids))
        temporary.replace(registry)
    return ids

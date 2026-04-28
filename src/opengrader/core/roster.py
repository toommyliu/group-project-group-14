from __future__ import annotations

import csv
from pathlib import Path


def read_roster(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        rows: list[dict[str, str]] = []
        for row in reader:
            normalized = {
                str(key).strip(): (value or "").strip()
                for key, value in row.items()
                if key is not None
            }
            if any(normalized.values()):
                rows.append(normalized)
        return rows

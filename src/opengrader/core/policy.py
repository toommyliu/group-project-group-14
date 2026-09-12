from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

GradingMode = Literal["solo", "multi"]

_POLICY_DIRNAME = ".opengrader"
_POLICY_FILENAME = "policy.json"


@dataclass(frozen=True)
class GradingPolicy:
    mode: GradingMode = "solo"
    anonymity_locked: bool = False
    traversal_shuffle: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "anonymity_locked": self.anonymity_locked,
            "traversal_shuffle": self.traversal_shuffle,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GradingPolicy":
        mode = data.get("mode")
        if mode not in ("solo", "multi"):
            mode = "solo"
        return cls(
            mode=mode,
            anonymity_locked=bool(data.get("anonymity_locked", False)),
            traversal_shuffle=bool(data.get("traversal_shuffle", False)),
        )


def resolve_policy_path(submissions_path: str | Path) -> Path:
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _POLICY_DIRNAME / _POLICY_FILENAME


def read_policy(submissions_path: str | Path) -> GradingPolicy:
    path = resolve_policy_path(submissions_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return GradingPolicy()
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return GradingPolicy()
    if not isinstance(data, dict):
        return GradingPolicy()
    return GradingPolicy.from_dict(data)


def write_policy(submissions_path: str | Path, policy: GradingPolicy) -> Path:
    path = resolve_policy_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(policy.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path

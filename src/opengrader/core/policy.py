"""Per-assignment grading policy — the teacher-set anonymity lock.

Anonymous grading (screens show student IDs, names stay hidden) is a bias
control: a grader shouldn't be swayed by *whose* paper they're looking at. In a
solo workflow the grader is trusted to toggle that freely. In a *multi-grader*
workflow the teacher wants the TAs to stay anonymous — and the one thing that
can never enforce that is a control the TA operates on themselves (they'd just
not use it). So the decision has to belong to the teacher and travel with the
assignment, not sit on each grader's launch screen.

This module is that decision: a tiny JSON file the teacher writes once and the
graders' app reads. When it locks anonymity, the TUI hides the teacher-mode
toggle (and, transitively, the identity-revealing audit view).

Honest scope: this is a *soft* control for a semi-trusted professor+TA team, not
a security boundary. The submission files are plaintext on a shared disk; anyone
who wants a name can open one in a text editor. Real enforcement would mean
withholding the name data itself (a later layer). What this buys is a sane
default that puts the choice with the trusted party and closes the "grader just
never opts in" hole.

Design decisions (mirroring `core/history/journal.py`):

* **Location** — ``<submissions>/.opengrader/policy.json``. Co-located with the
  assignment data so the policy travels when the folder is copied/moved, namespaced in
  the same ``.opengrader`` dot-dir as the audit journal so it reads as tool
  metadata and stays out of submission discovery.
* **Absent file = permissive default** — no policy means solo/unlocked, so
  existing folders and solo users behave exactly as before. A malformed or
  unreadable file degrades to the same default rather than raising, so a
  corrupt write never blocks grading.
"""

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
    """The anonymity policy for one assignment's submissions folder.

    ``mode`` is descriptive (who this folder is set up for); ``anonymity_locked``
    is the enforced bit — when true the TUI won't let a grader reveal names.
    ``traversal_shuffle`` enables per-grader randomized order to guard against
    fatigue drift landing on the same students.
    """

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
    """Return the ``.opengrader/policy.json`` path for a submissions file or dir."""
    base = Path(submissions_path)
    if base.is_file():
        base = base.parent
    return base / _POLICY_DIRNAME / _POLICY_FILENAME


def read_policy(submissions_path: str | Path) -> GradingPolicy:
    """Load the policy for a submissions folder, or the permissive default.

    A missing, unreadable, or malformed file all resolve to ``GradingPolicy()``
    (solo / unlocked) so grading is never blocked by policy state.
    """
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
    """Write ``policy`` into ``<submissions>/.opengrader/policy.json``.

    Returns the path written. Creates the ``.opengrader`` dir if needed.
    """
    path = resolve_policy_path(submissions_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(policy.to_dict(), ensure_ascii=False, indent=2)
    path.write_text(payload + "\n", encoding="utf-8")
    return path

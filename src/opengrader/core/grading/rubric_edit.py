"""What an edited rubric means for grades already saved against the old one.

A grade stores the *labels* of the rubric items it selected, so an edit that
renames an item must rename it in those grades, and an edit that removes one
has to know whether a grade still uses it. Both the terminal app (which
rewrites `qa.md` and the submission files) and the browser (which updates
its workspace and writes through to the same files) start from this one
comparison, so the two cannot disagree about which item became which.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Generic, Protocol, Sequence, TypeVar

__all__ = ["RubricOptionLike", "RubricOptionDiff", "diff_rubric_options"]


class RubricOptionLike(Protocol):
    @property
    def label(self) -> str: ...

    @property
    def points_delta(self) -> float: ...


Option = TypeVar("Option", bound=RubricOptionLike)


@dataclass(frozen=True)
class RubricOptionDiff(Generic[Option]):
    """`changed` pairs an old option with the one that replaced it in place."""

    changed: tuple[tuple[Option, Option], ...]
    deleted: tuple[Option, ...]

    @property
    def renames(self) -> dict[str, str]:
        """Old label → new label, for the options whose wording changed."""
        return {
            old.label: new.label for old, new in self.changed if old.label != new.label
        }


def diff_rubric_options(
    original: Sequence[Option], edited: Sequence[Option]
) -> RubricOptionDiff[Option]:
    """Match the options of a rubric before and after an edit.

    An option that kept its position but changed its label or points is the
    *same* option, changed: grades follow it. A run of options replaced by a
    run of a different length cannot be paired up one for one, so those are
    treated as deleted (and the new ones as new) rather than guessed at.
    """
    matcher = SequenceMatcher(
        a=[(option.label, option.points_delta) for option in original],
        b=[(option.label, option.points_delta) for option in edited],
        autojunk=False,
    )
    changed: list[tuple[Option, Option]] = []
    deleted: list[Option] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("equal", "insert"):
            continue
        before, after = original[i1:i2], edited[j1:j2]
        if tag == "delete" or len(before) != len(after):
            deleted.extend(before)
            continue
        for old, new in zip(before, after, strict=True):
            if old.label == new.label and abs(old.points_delta - new.points_delta) <= 1e-6:
                continue
            changed.append((old, new))
    return RubricOptionDiff(changed=tuple(changed), deleted=tuple(deleted))

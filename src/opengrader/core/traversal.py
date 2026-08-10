"""Per-grader traversal order — the fairness guard against fatigue drift.

Order and fatigue effects on grading are real: a grader marks differently at
student 180 than at student 5, whether from tiredness, from a rubric they have
quietly tightened, or from having recalibrated against the answers they have
already seen. Opengrader already *measures* that drift
(``core/analytics/grader_agreement.py``). What it could not do was stop the
drift from landing on the same people: every grader walked the cohort in the
same on-disk order, so the students at the end of the list absorbed the tail of
every grader's session, every time. Systematic, invisible, and always the same
students.

A per-grader seeded shuffle fixes that for a few lines: the drift still exists,
but which student it falls on is now uncorrelated with who that student is.

**The invariant that makes this usable, and must not be reversed: the order is
stable for a given (grader, assignment) pair.** Randomness here is *between
graders*, never between sessions. A grader who stops at student 40 and reopens
the app tomorrow gets the same order back — otherwise "next ungraded" walks a
differently-shuffled pile every session, progress stops being something a
person can hold in their head, and the feature would be switched off within a
day. That is why the seed comes from a SHA-256 digest and not from Python's
built-in ``hash()``, which is randomized per process by ``PYTHONHASHSEED`` and
would silently re-roll the cohort on every launch.

The second half of the design is that two graders on the same assignment must
get *different* orders. That is the entire point — a shared order would leave
drift correlated with student identity exactly as before, just in a scrambled
sequence.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence, TypeVar

T = TypeVar("T")


def traversal_seed(actor: str, assignment_key: str) -> int:
    """A seed for ``actor`` grading ``assignment_key``, stable across processes.

    Derived from SHA-256 rather than ``hash()`` so the same pair yields the same
    seed in every process — see this module's docstring for why that stability
    is the whole feature rather than an implementation detail.
    """
    # The separator keeps the two fields from bleeding into each other: without
    # it, actor "alice-bob" + assignment "quiz1" and actor "alice" +
    # assignment "bob-quiz1" would hash identically and share an order.
    combined = f"{actor}|{assignment_key}"
    digest = hashlib.sha256(combined.encode("utf-8")).digest()
    # 8 bytes is far more entropy than a shuffle of a few hundred students can
    # consume; taking a prefix of the digest keeps the seed a comfortably small
    # integer while staying deterministic.
    return int.from_bytes(digest[:8], "big", signed=False)


def shuffled_order(items: Sequence[T], seed: int) -> list[T]:
    """Return a new list holding ``items`` in a ``seed``-determined order.

    The input is never mutated, so a caller can keep its own canonical ordering.
    """
    # A private Random instance rather than the module-level `random.shuffle`:
    # seeding the global generator would make this function reach out and
    # change the numbers every other part of the process draws.
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled

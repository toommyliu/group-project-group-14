from __future__ import annotations

import hashlib
import random
from typing import Sequence, TypeVar

T = TypeVar("T")


def traversal_seed(actor: str, assignment_key: str) -> int:
    combined = f"{actor}|{assignment_key}"
    digest = hashlib.sha256(combined.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def shuffled_order(items: Sequence[T], seed: int) -> list[T]:
    rng = random.Random(seed)
    shuffled = list(items)
    rng.shuffle(shuffled)
    return shuffled

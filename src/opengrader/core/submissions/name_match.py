"""How alike a name read off a paper is to a name on the roster.

The name is usually read from handwriting by plain OCR (identity never goes
to a cloud model), so what comes back is rarely the roster's spelling:

* digits for letters — `T0mmy L1u`;
* the other order, as many students write it — `Liu, Tommy`;
* part of the name — `Tommy`, `T. Liu`, or a nickname over the roster's
  formal name (`Andy Cortes` for `Andrew Cortes`).

`name_similarity` compares the two as words, in either order, after undoing
the common OCR swaps. A *partial* agreement (one name, or the last name and a
first initial) is reported apart from a whole-name one, so the caller can
accept it only when no other student fits — and say it is unsure.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

__all__ = ["NameScore", "name_similarity", "name_tokens"]

# What OCR reads for a handwritten letter, back to the letter. Only applied
# to a name, which has no digits of its own.
_OCR_LETTERS = str.maketrans(
    {
        "0": "o",
        "1": "l",
        "2": "z",
        "3": "e",
        "4": "a",
        "5": "s",
        "6": "g",
        "7": "t",
        "8": "b",
        "9": "g",
        "|": "l",
        "!": "l",
        "@": "a",
        "$": "s",
    }
)
_APOSTROPHES = re.compile(r"['’`]")
_NOT_LETTER = re.compile(r"[^a-z]+")

# Two words are the same word at this ratio (one letter off in a short one).
_SAME_WORD = 0.8


@dataclass(frozen=True)
class NameScore:
    """`score` is the whole-name similarity, 0–1. `partial` is True when the
    names agree only in part (see the module docstring); `score` is then
    usually below a whole-name match's."""

    score: float
    partial: bool = False


def name_tokens(text: str) -> tuple[str, ...]:
    """The words of a name, lower case, accents and OCR digit swaps undone.

    `O'Brien` is one word (`obrien`); a hyphen or comma separates words.
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    plain = _APOSTROPHES.sub("", plain.casefold().translate(_OCR_LETTERS))
    return tuple(word for word in _NOT_LETTER.split(plain) if word)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _same_word(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 3 and _ratio(a, b) >= _SAME_WORD)


def _partial(read: tuple[str, ...], roster: tuple[str, ...]) -> bool:
    if len(read) == 1:
        # One name written: it must be a whole name on the roster, not an initial.
        return len(read[0]) >= 3 and any(_same_word(read[0], word) for word in roster)
    if len(roster) < 2:
        return False
    first, last = roster[0], roster[-1]
    # The last name, in either position, and another word starting with the
    # first name's initial (`T Liu`, `Liu T`, `Andy Cortes` for `Andrew`).
    for index, word in enumerate(read):
        if len(last) >= 2 and _same_word(word, last):
            others = read[:index] + read[index + 1 :]
            return any(other[0] == first[0] for other in others)
    return False


def name_similarity(read: str, roster_name: str) -> NameScore:
    """How alike `read` (off the paper) is to `roster_name`."""
    a, b = name_tokens(read), name_tokens(roster_name)
    if not a or not b:
        return NameScore(0.0)
    score = max(
        _ratio(" ".join(a), " ".join(b)),
        # Word order does not matter: `Liu Tommy` is `Tommy Liu`.
        _ratio(" ".join(sorted(a)), " ".join(sorted(b))),
    )
    return NameScore(score, partial=_partial(a, b))

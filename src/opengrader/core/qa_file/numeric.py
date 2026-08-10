"""Numeric answer keys: parsing, tolerance-based grading, and formatting.

A numeric question's answer key is written directly in the ``**Correct**``
section of a QA file, so this module is the single place that understands the
syntax. Supported forms (units optional in every case):

- ``42``                 exact value
- ``42 ±0.5``            value with an absolute tolerance (``±``/``+/-``/``+-``/``~``)
- ``42 ±5%``             value with a percentage tolerance
- ``[40, 44]``           an inclusive range
- ``40..44`` / ``40 to 44``   an inclusive range
- ``1000 units:mL``      an explicit units tag
- ``1000 mL``            a trailing unit token

The module is pure (no I/O) and self-contained so it can be reused by the QA
parser, the auto-grader, answer grouping, and the TUI, and covered by fast unit
tests. ``grade_numeric_response`` returns ``None`` for an unparseable student
response so the caller can leave it for manual grading rather than guess.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# A small absolute fudge so exact answers survive float round-off (e.g. a
# response of ``0.1 + 0.2`` typed as ``0.30000000000000004``) without
# meaningfully loosening a declared tolerance.
_MATCH_EPSILON = 1e-9

# A signed decimal or scientific-notation number.
_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
# An optional trailing unit token (letters/symbols, never starting with a digit
# so it can't swallow part of a number). Percent is excluded — it belongs to a
# tolerance, not a unit.
_UNIT = r"(?:\s*(?P<units>[A-Za-z°µΩ/][\w°µΩ/.^·\-]*))?"

_UNITS_EXPLICIT_RE = re.compile(r"\bunits?\s*[:=]\s*(?P<u>\S+)", re.IGNORECASE)
_RANGE_BRACKET_RE = re.compile(
    rf"^\[\s*(?P<lo>{_NUM})\s*,\s*(?P<hi>{_NUM})\s*\]{_UNIT}$"
)
_RANGE_KEYWORD_RE = re.compile(
    rf"^(?P<lo>{_NUM})\s*(?:\.\.|to)\s*(?P<hi>{_NUM}){_UNIT}$", re.IGNORECASE
)
_TOLERANCE_RE = re.compile(
    rf"^(?P<val>{_NUM})\s*(?:±|\+/-|\+-|~)\s*(?P<tol>{_NUM})\s*(?P<pct>%?){_UNIT}$"
)
_PLAIN_RE = re.compile(rf"^(?P<val>{_NUM}){_UNIT}$")
_LEADING_NUM_RE = re.compile(rf"^\s*(?P<val>{_NUM})")


@dataclass(frozen=True)
class NumericAnswerSpec:
    """A gradable numeric answer key.

    Exactly one of two shapes is populated: a target ``value`` (optionally with a
    ``tolerance``), or an inclusive ``[lower, upper]`` range. ``units`` is carried
    for display only — grading compares numbers, never unit strings, so a student
    who omits or abbreviates the unit is not penalized here.
    """

    value: float | None = None
    tolerance: float | None = None
    tolerance_is_percent: bool = False
    lower: float | None = None
    upper: float | None = None
    units: str | None = None
    raw: str = ""

    @property
    def is_range(self) -> bool:
        return self.lower is not None or self.upper is not None

    def matches(self, number: float) -> bool:
        """Whether ``number`` satisfies this answer key."""
        if self.is_range:
            low = self.lower if self.lower is not None else float("-inf")
            high = self.upper if self.upper is not None else float("inf")
            return low - _MATCH_EPSILON <= number <= high + _MATCH_EPSILON
        if self.value is None:
            return False
        tolerance = self.tolerance or 0.0
        if self.tolerance_is_percent:
            tolerance = abs(self.value) * tolerance / 100.0
        return abs(number - self.value) <= tolerance + _MATCH_EPSILON


def _clean_units(units: str | None) -> str | None:
    if units is None:
        return None
    cleaned = units.strip()
    return cleaned or None


def parse_numeric_answer(text: str) -> NumericAnswerSpec | None:
    """Parse a numeric answer-key string, or ``None`` when it is not numeric.

    Returning ``None`` (rather than raising) lets callers treat "does this answer
    look numeric?" as a plain boolean — the QA parser uses it to decide a
    question's type without any extra marker.
    """
    raw = text.strip()
    if not raw:
        return None

    units: str | None = None
    working = raw
    explicit = _UNITS_EXPLICIT_RE.search(working)
    if explicit is not None:
        units = explicit.group("u")
        working = (working[: explicit.start()] + working[explicit.end() :]).strip()

    bracket = _RANGE_BRACKET_RE.match(working)
    if bracket is not None:
        return _range_spec(bracket, units, raw)

    keyword = _RANGE_KEYWORD_RE.match(working)
    if keyword is not None:
        return _range_spec(keyword, units, raw)

    tolerance = _TOLERANCE_RE.match(working)
    if tolerance is not None:
        return NumericAnswerSpec(
            value=float(tolerance.group("val")),
            tolerance=abs(float(tolerance.group("tol"))),
            tolerance_is_percent=bool(tolerance.group("pct")),
            units=_clean_units(units or tolerance.group("units")),
            raw=raw,
        )

    plain = _PLAIN_RE.match(working)
    if plain is not None:
        return NumericAnswerSpec(
            value=float(plain.group("val")),
            units=_clean_units(units or plain.group("units")),
            raw=raw,
        )

    return None


def _range_spec(
    match: re.Match[str], units: str | None, raw: str
) -> NumericAnswerSpec:
    low = float(match.group("lo"))
    high = float(match.group("hi"))
    if low > high:
        low, high = high, low
    return NumericAnswerSpec(
        lower=low,
        upper=high,
        units=_clean_units(units or match.group("units")),
        raw=raw,
    )


def parse_numeric_answers(answers: list[str]) -> list[NumericAnswerSpec] | None:
    """Parse every answer as a numeric spec, or ``None`` if any is non-numeric.

    A question is numeric only when *all* of its accepted answers are numeric
    (multiple specs mean "any of these values is correct").
    """
    if not answers:
        return None
    specs: list[NumericAnswerSpec] = []
    for answer in answers:
        spec = parse_numeric_answer(answer)
        if spec is None:
            return None
        specs.append(spec)
    return specs


def response_value(text: str) -> float | None:
    """Extract the numeric value a student intended, or ``None`` if there isn't one.

    Only the leading number is read, so "42 mL", "42", and "≈42" all yield 42.0;
    prose like "forty two" yields ``None``.
    """
    cleaned = text.strip().lstrip("≈~=").strip()
    match = _LEADING_NUM_RE.match(cleaned)
    if match is None:
        return None
    try:
        return float(match.group("val"))
    except ValueError:
        return None


def grade_numeric_response(
    response_text: str, specs: list[NumericAnswerSpec]
) -> bool | None:
    """Grade a response against numeric answer key(s).

    Returns ``True``/``False`` when the response is a number that does/does not
    satisfy any spec, and ``None`` when the response is not a gradable number
    (leave for manual grading).
    """
    value = response_value(response_text)
    if value is None:
        return None
    return any(spec.matches(value) for spec in specs)


def _format_number(value: float) -> str:
    return f"{value:g}"


def format_numeric_spec(spec: NumericAnswerSpec) -> str:
    """Render a spec back to a compact, human-readable answer string."""
    if spec.is_range:
        low = _format_number(spec.lower) if spec.lower is not None else "-∞"
        high = _format_number(spec.upper) if spec.upper is not None else "∞"
        body = f"[{low}, {high}]"
    elif spec.value is not None:
        body = _format_number(spec.value)
        if spec.tolerance:
            suffix = "%" if spec.tolerance_is_percent else ""
            body += f" ± {_format_number(spec.tolerance)}{suffix}"
    else:
        body = spec.raw
    if spec.units:
        body += f" {spec.units}"
    return body

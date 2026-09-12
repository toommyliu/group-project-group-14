from __future__ import annotations

import re
from dataclasses import dataclass

# Floating-point roundoff allowance, separate from the declared tolerance.
_MATCH_EPSILON = 1e-9

_NUM = r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
# Units cannot start with digits; percent belongs to tolerance syntax.
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
    value = response_value(response_text)
    if value is None:
        return None
    return any(spec.matches(value) for spec in specs)


def _format_number(value: float) -> str:
    return f"{value:g}"


def format_numeric_spec(spec: NumericAnswerSpec) -> str:
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

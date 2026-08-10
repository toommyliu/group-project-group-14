from __future__ import annotations

import pytest

from opengrader.core.qa_file.numeric import (
    NumericAnswerSpec,
    format_numeric_spec,
    grade_numeric_response,
    parse_numeric_answer,
    parse_numeric_answers,
    response_value,
)


class TestParse:
    def test_plain_integer(self) -> None:
        spec = parse_numeric_answer("42")
        assert spec == NumericAnswerSpec(value=42.0, raw="42")

    def test_plain_float_and_negative(self) -> None:
        assert parse_numeric_answer("-3.5").value == -3.5
        assert parse_numeric_answer(".25").value == 0.25
        assert parse_numeric_answer("1e3").value == 1000.0

    def test_absolute_tolerance_variants(self) -> None:
        for text in ("42 ±0.5", "42 +/- 0.5", "42 +-0.5", "42~0.5"):
            spec = parse_numeric_answer(text)
            assert spec is not None
            assert spec.value == 42.0
            assert spec.tolerance == 0.5
            assert spec.tolerance_is_percent is False

    def test_percent_tolerance(self) -> None:
        spec = parse_numeric_answer("200 ±5%")
        assert spec is not None
        assert spec.value == 200.0
        assert spec.tolerance == 5.0
        assert spec.tolerance_is_percent is True

    def test_range_bracket_and_keyword(self) -> None:
        bracket = parse_numeric_answer("[40, 44]")
        assert bracket is not None and (bracket.lower, bracket.upper) == (40.0, 44.0)
        dots = parse_numeric_answer("40..44")
        assert dots is not None and (dots.lower, dots.upper) == (40.0, 44.0)
        words = parse_numeric_answer("40 to 44")
        assert words is not None and (words.lower, words.upper) == (40.0, 44.0)

    def test_range_orders_bounds(self) -> None:
        spec = parse_numeric_answer("[44, 40]")
        assert spec is not None and (spec.lower, spec.upper) == (40.0, 44.0)

    def test_explicit_units_tag(self) -> None:
        spec = parse_numeric_answer("1000 ±10 units:mL")
        assert spec is not None
        assert spec.value == 1000.0
        assert spec.tolerance == 10.0
        assert spec.units == "mL"

    def test_trailing_unit_token(self) -> None:
        spec = parse_numeric_answer("1000 mL")
        assert spec is not None
        assert spec.value == 1000.0
        assert spec.units == "mL"

    def test_units_on_range(self) -> None:
        spec = parse_numeric_answer("[40, 44] kg")
        assert spec is not None and spec.units == "kg"

    def test_non_numeric_returns_none(self) -> None:
        assert parse_numeric_answer("blue") is None
        assert parse_numeric_answer("True") is None
        assert parse_numeric_answer("quicksort") is None
        assert parse_numeric_answer("") is None


class TestGrade:
    def test_exact_match(self) -> None:
        specs = [parse_numeric_answer("42")]  # type: ignore[list-item]
        assert grade_numeric_response("42", specs) is True
        assert grade_numeric_response("42.0", specs) is True
        assert grade_numeric_response("43", specs) is False

    def test_tolerance_boundaries(self) -> None:
        specs = [parse_numeric_answer("42 ±0.5")]  # type: ignore[list-item]
        assert grade_numeric_response("41.5", specs) is True
        assert grade_numeric_response("42.5", specs) is True
        assert grade_numeric_response("42.6", specs) is False

    def test_percent_tolerance_grade(self) -> None:
        specs = [parse_numeric_answer("200 ±5%")]  # type: ignore[list-item]
        assert grade_numeric_response("190", specs) is True   # within ±10
        assert grade_numeric_response("211", specs) is False

    def test_range_grade(self) -> None:
        specs = [parse_numeric_answer("[40, 44]")]  # type: ignore[list-item]
        assert grade_numeric_response("40", specs) is True
        assert grade_numeric_response("44", specs) is True
        assert grade_numeric_response("39.9", specs) is False

    def test_units_ignored_when_grading(self) -> None:
        specs = [parse_numeric_answer("1000 units:mL")]  # type: ignore[list-item]
        assert grade_numeric_response("1000 mL", specs) is True
        assert grade_numeric_response("1000", specs) is True

    def test_multiple_specs_any_match(self) -> None:
        specs = parse_numeric_answers(["3.14", "3.14159 ±0.001"])
        assert specs is not None
        assert grade_numeric_response("3.14", specs) is True
        assert grade_numeric_response("3.1416", specs) is True
        assert grade_numeric_response("2", specs) is False

    def test_unparseable_response_is_none(self) -> None:
        specs = [parse_numeric_answer("42")]  # type: ignore[list-item]
        assert grade_numeric_response("forty two", specs) is None
        assert grade_numeric_response("", specs) is None


class TestHelpers:
    def test_parse_numeric_answers_rejects_mixed(self) -> None:
        assert parse_numeric_answers(["42", "blue"]) is None
        assert parse_numeric_answers([]) is None

    def test_response_value(self) -> None:
        assert response_value("42 mL") == 42.0
        assert response_value("≈ 3.5") == 3.5
        assert response_value("n/a") is None

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("42", "42"),
            ("42 ±0.5", "42 ± 0.5"),
            ("200 ±5%", "200 ± 5%"),
            ("[40, 44]", "[40, 44]"),
            ("1000 units:mL", "1000 mL"),
        ],
    )
    def test_format(self, text: str, expected: str) -> None:
        spec = parse_numeric_answer(text)
        assert spec is not None
        assert format_numeric_spec(spec) == expected

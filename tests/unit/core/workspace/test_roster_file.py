"""One roster file, two shapes: read both, change one student, keep the rest."""

from __future__ import annotations

import pytest

from opengrader.core.roster import load_roster_students
from opengrader.core.workspace.models import StudentDetails
from opengrader.core.workspace.roster_file import (
    csv_has_roster_headings,
    parse_roster,
    roster_format,
    split_name,
    with_student_added,
    with_student_changed,
    with_student_removed,
)

TSV = "1001\tAda Lovelace\n1002\tGrace Brewster Hopper\n1003\tLinus\n"
CSV = (
    "Last Name,First Name,SID,Email,Section\n"
    "Lovelace,Ada,1001,ada@example.edu,A\n"
    '"Hopper, Jr.",Grace,1002,,B\n'
)


def _student(sid: str, first: str, last: str = "", email: str | None = None) -> StudentDetails:
    return StudentDetails(sid=sid, first_name=first, last_name=last, email=email)


def test_the_extension_decides_the_shape() -> None:
    assert roster_format("class/roster.csv") == "csv"
    assert roster_format("class/roster.tsv") == "tsv"
    assert roster_format("class/ids.txt") == "tsv"


def test_a_single_name_divides_at_its_last_word() -> None:
    assert split_name("Grace Brewster Hopper") == ("Grace Brewster", "Hopper")
    assert split_name("  Linus ") == ("Linus", "")
    # Shown joined again, it is the name that was written.
    for name in ("Ada Lovelace", "Grace Brewster Hopper", "Linus"):
        assert " ".join(part for part in split_name(name) if part) == name


# -- reading -------------------------------------------------------------------


def test_a_tab_separated_roster_is_read() -> None:
    found = parse_roster(TSV, "tsv")

    assert [(s.sid, s.first_name, s.last_name) for s in found.students] == [
        ("1001", "Ada", "Lovelace"),
        ("1002", "Grace Brewster", "Hopper"),
        ("1003", "Linus", ""),
    ]
    assert (found.format, found.has_email, found.skipped) == ("tsv", False, 0)


def test_rows_that_are_not_a_student_with_an_id_are_counted_not_read() -> None:
    text = "1001\tAda Lovelace\n\nName Only\n\tNo Id\n1001\tAda Again\n1002\t\n"

    found = parse_roster(text, "tsv")

    assert [s.sid for s in found.students] == ["1001"]
    assert found.skipped == 4


def test_a_comma_separated_roster_is_read_by_its_headings() -> None:
    found = parse_roster(CSV, "csv")

    assert [(s.sid, s.name, s.email) for s in found.students] == [
        ("1001", "Ada Lovelace", "ada@example.edu"),
        ("1002", "Grace Hopper, Jr.", None),
    ]
    assert (found.format, found.has_email, found.skipped) == ("csv", True, 0)


def test_a_csv_without_the_heading_row_is_not_a_roster() -> None:
    assert not csv_has_roster_headings("id,name\n1,Ada\n")
    assert csv_has_roster_headings("﻿SID, First  Name ,Last Name\n")
    with pytest.raises(ValueError, match="First Name, Last Name, and SID"):
        parse_roster("id,name\n1,Ada\n", "csv")


def test_a_csv_with_headings_and_no_students_is_an_empty_roster() -> None:
    found = parse_roster("First Name,Last Name,SID\n", "csv")

    assert found.students == () and found.has_email is False


def test_classic_reads_either_shape_through_the_one_loader(tmp_path) -> None:
    (tmp_path / "roster.tsv").write_text(TSV, encoding="utf-8")
    (tmp_path / "roster.csv").write_text("﻿" + CSV, encoding="utf-8")

    tabbed = load_roster_students(tmp_path / "roster.tsv")
    comma = load_roster_students(tmp_path / "roster.csv")

    assert [(s.student_id, s.name) for s in tabbed][:2] == [
        ("1001", "Ada Lovelace"),
        ("1002", "Grace Brewster Hopper"),
    ]
    assert [(s.student_id, s.name) for s in comma] == [
        ("1001", "Ada Lovelace"),
        ("1002", "Grace Hopper, Jr."),
    ]


# -- changing one student ------------------------------------------------------


def test_a_tsv_edit_touches_one_line_and_keeps_extra_columns() -> None:
    text = "1001\tAda Lovelace\tsection A\r\n1002\tGrace Hopper\r\n"

    changed = with_student_changed(text, "tsv", "1001", _student("1009", "Ada", "King"))

    assert changed == "1009\tAda King\tsection A\r\n1002\tGrace Hopper\r\n"


def test_a_tsv_gains_a_line_even_when_the_last_one_was_not_ended() -> None:
    added = with_student_added("1001\tAda Lovelace", "tsv", _student("1002", "Grace", "Hopper"))

    assert added == "1001\tAda Lovelace\n1002\tGrace Hopper\n"


def test_a_tsv_loses_exactly_the_students_line() -> None:
    assert with_student_removed(TSV, "tsv", "1002") == "1001\tAda Lovelace\n1003\tLinus\n"


def test_a_csv_edit_rewrites_one_record_and_leaves_the_rest_byte_for_byte() -> None:
    changed = with_student_changed(
        CSV, "csv", "1002", _student("1002", "Grace", "Hopper", "grace@example.edu")
    )

    before, after = CSV.splitlines(), changed.splitlines()
    assert after[:2] == before[:2]
    # Its own columns changed; the Section column, which this knows nothing
    # about, kept its value.
    assert after[2] == "Hopper,Grace,1002,grace@example.edu,B"


def test_a_csv_addition_fills_only_the_columns_it_knows() -> None:
    added = with_student_added(CSV, "csv", _student("1003", "Linus", "Torvalds"))

    assert added == CSV + "Torvalds,Linus,1003,,\n"


def test_a_csv_keeps_its_byte_order_mark_and_windows_line_endings() -> None:
    text = "﻿" + CSV.replace("\n", "\r\n")

    added = with_student_added(text, "csv", _student("1003", "Linus", "Torvalds"))
    removed = with_student_removed(added, "csv", "1003")

    assert added.startswith("﻿") and added.endswith("Torvalds,Linus,1003,,\r\n")
    assert removed == text


def test_a_value_with_a_comma_is_quoted_on_the_way_back() -> None:
    changed = with_student_changed(
        CSV, "csv", "1001", _student("1001", "Ada", "King, Countess", "ada@example.edu")
    )

    assert changed.splitlines()[1] == '"King, Countess",Ada,1001,ada@example.edu,A'
    assert parse_roster(changed, "csv").students[0].last_name == "King, Countess"


def test_an_edit_that_clears_the_email_clears_it_in_the_file() -> None:
    changed = with_student_changed(CSV, "csv", "1001", _student("1001", "Ada", "Lovelace"))

    assert changed.splitlines()[1] == "Lovelace,Ada,1001,,A"


def test_a_record_spanning_two_lines_is_replaced_whole() -> None:
    text = 'First Name,Last Name,SID,Notes\nAda,Lovelace,1001,"line one\nline two"\nGrace,Hopper,1002,\n'

    removed = with_student_removed(text, "csv", "1001")

    assert removed == "First Name,Last Name,SID,Notes\nGrace,Hopper,1002,\n"


def test_an_email_cannot_be_saved_where_there_is_no_column_for_it() -> None:
    student = _student("1004", "Alan", "Turing", "alan@example.edu")

    with pytest.raises(ValueError, match="no Email column"):
        with_student_added(TSV, "tsv", student)
    with pytest.raises(ValueError, match="no Email column"):
        with_student_added("First Name,Last Name,SID\n", "csv", student)


def test_a_student_the_file_does_not_have_is_a_lookup_error() -> None:
    for text, kind in ((TSV, "tsv"), (CSV, "csv")):
        with pytest.raises(LookupError):
            with_student_removed(text, kind, "9999")
        with pytest.raises(LookupError):
            with_student_changed(text, kind, "9999", _student("9999", "No", "One"))

from opengrader.core.roster import (
    RosterStudent,
    assign_student_ids,
    load_roster_students,
    read_roster,
)


def test_read_roster_strips_headers_values_and_skips_empty_rows(tmp_path) -> None:
    roster_path = tmp_path / "roster.csv"
    roster_path.write_text(
        "\ufeff name , sid , email \n"
        " Alice Student , 12345 , alice@example.edu \n"
        " , , \n",
        encoding="utf-8",
    )

    assert read_roster(roster_path) == [
        {
            "name": "Alice Student",
            "sid": "12345",
            "email": "alice@example.edu",
        }
    ]


def test_load_roster_students_parses_tsv_and_skips_blank_lines(tmp_path) -> None:
    roster_path = tmp_path / "roster.tsv"
    roster_path.write_text(
        "﻿012345678\tAlice Student\n"
        "\n"
        "987654321 \t Bob Builder \n"
        "malformed-line-without-tab\n",
        encoding="utf-8",
    )

    assert load_roster_students(roster_path) == [
        RosterStudent(student_id="012345678", name="Alice Student"),
        RosterStudent(student_id="987654321", name="Bob Builder"),
    ]


def test_load_roster_students_allow_missing_id_keeps_name_only_rows(tmp_path) -> None:
    roster_path = tmp_path / "names.tsv"
    roster_path.write_text(
        "100001\tAlice Zhang\nCarol Xavier\n\nBob Young\n",
        encoding="utf-8",
    )
    assert load_roster_students(roster_path) == [
        RosterStudent(student_id="100001", name="Alice Zhang"),
    ]
    assert load_roster_students(roster_path, allow_missing_id=True) == [
        RosterStudent(student_id="100001", name="Alice Zhang"),
        RosterStudent(student_id="", name="Carol Xavier"),
        RosterStudent(student_id="", name="Bob Young"),
    ]


def test_assign_student_ids_keeps_existing_and_mints_six_digit() -> None:
    students = [
        RosterStudent(student_id="42", name="Has Id"),
        RosterStudent(student_id="", name="No Id"),
    ]
    assigned = assign_student_ids(students)
    assert assigned[0].student_id == "42"
    minted = assigned[1].student_id
    assert len(minted) == 6 and minted.isdigit()


def test_assign_student_ids_is_deterministic() -> None:
    students = [RosterStudent(student_id="", name="Jane Doe")]
    first = assign_student_ids(students)
    second = assign_student_ids(students)
    assert first == second


def test_assign_student_ids_resolves_duplicate_name_collisions() -> None:
    students = [
        RosterStudent(student_id="", name="Carol Xavier"),
        RosterStudent(student_id="", name="Carol Xavier"),
    ]
    assigned = assign_student_ids(students)
    ids = [s.student_id for s in assigned]
    assert len(set(ids)) == 2
    assert all(len(i) == 6 and i.isdigit() for i in ids)

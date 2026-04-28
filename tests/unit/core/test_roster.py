from opengrader.core.roster import read_roster


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

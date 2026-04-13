from opengrader.core.roster import read_roster, display_roster, save_roster_with_grades


def run_cli():
    roster_path = "data/roster.csv"
    students = read_roster(roster_path)
    display_roster(students)
    print(f"\nTotal students: {len(students)}")
    save_roster_with_grades(students, "data/roster_graded.csv")


if __name__ == "__main__":
    run_cli()

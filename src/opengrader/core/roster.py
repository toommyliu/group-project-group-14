import csv
import sys
from typing import List, Dict


# reads the csv file and stores each row
def read_roster(filename: str) -> List[Dict[str, str]]:
    students = []
    with open(filename, newline="", encoding="utf-8") as csvfile:
        reader = csv.DictReader(
            csvfile
        )  # DictReader makes each row a dict so we can access by column name
        for row in reader:
            try:
                students.append(dict(row))
            except csv.Error as exc:
                print(f"roster: skipping malformed row: {exc}", file=sys.stderr)
    return students


# prints out the roster
def display_roster(students: List[Dict[str, str]]):
    print(f"\n{'=' * 65}")
    print(f"{'COURSE ROSTER':^65}")
    print(f"{'=' * 65}")
    print(f"{'NAME':<20} {'EMAIL':<28} {'SEC':<5} {'COURSE':<12} {'SID':<12} {'GRADE'}")
    print(f"{'-' * 65}")
    for student in students:
        grade = student.get("grade", "---") if student.get("grade") else "---"
        print(
            f"{student['name']:<20} {student['email']:<28} {student['section']:<5} {student['course']:<12} {student['sid']:<12} {grade}"
        )
    print(f"{'=' * 65}")


# saves the roster to a new csv file
def save_roster_with_grades(students: List[Dict[str, str]], output_filename: str):
    fieldnames = ["name", "email", "section", "course", "sid", "grade"]
    with open(output_filename, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(students)
    print(f"\n Roster saved to '{output_filename}'")

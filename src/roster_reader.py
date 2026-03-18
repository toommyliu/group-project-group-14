import csv

# reads the csv file and stores each row 
def read_roster(filename):
    students = []
    with open(filename, newline='', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)  # DictReader makes each row a dict so we can access by column name
        for row in reader:
            students.append(row)
    return students

# prints out the roster
def display_roster(students):
    print(f"\n{'='*65}")
    print(f"{'COURSE ROSTER':^65}")
    print(f"{'='*65}")
    # header row
    print(f"{'NAME':<20} {'EMAIL':<28} {'SEC':<5} {'COURSE':<12} {'SID':<12} {'GRADE'}")
    print(f"{'-'*65}")
    for student in students:
        # if grade is empty its gonna show -A-- instead of it just being blank
        grade = student['grade'] if student['grade'] else '---'
        print(f"{student['name']:<20} {student['email']:<28} {student['section']:<5} {student['course']:<12} {student['sid']:<12} {grade}")
    print(f"{'='*65}")

# saves the roster to a new csv file
def save_roster_with_grades(students, output_filename):
    fieldnames = ['name', 'email', 'section', 'course', 'sid', 'grade']
    with open(output_filename, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()  # writes the column names first
        writer.writerows(students)
    print(f"\n Roster saved to '{output_filename}'")

# reads the roster, displays it, and saves a copy
if __name__ == "__main__":
    students = read_roster("../data/roster.csv")
    display_roster(students)
    print(f"\nTotal students: {len(students)}")
    save_roster_with_grades(students, "../data/roster_graded.csv")
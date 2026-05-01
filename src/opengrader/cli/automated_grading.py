import os
import sys
import yaml
from argparse import ArgumentParser, RawDescriptionHelpFormatter
import re
from pathlib import Path

"""
Automatically grades the student markdown files using an answer key markdown file

"""


EPILOG = """
examples:
  uv run automated-grading --solutions solutions.md --input student-0001.submission.md
  uv run automated-grading --solutions solutions.md --input ./student-submissions/
  uv run automated-grading --solutions Solutions/solutions.md --input samples/student_responses.md
  uv run automated-grading --solutions Solutions/final_exam_solutions.md --input ./Solutions/student_submissions/
"""


def parse_solutions(solutions_path: Path) -> dict:
    """
    Parses the solutions Markdown file.
    Maps question numbers to points and valid answers.
    """
    solutions = {}
    content = solutions_path.read_text()

    # Find question blocks: "- (P points)"
    # Use re.MULTILINE to find the start of bullets and re.DOTALL to capture content between them
    blocks = re.findall(
        r"^- \(\s*(\d+)\s*(?:points?|pts)\s*\)(.*?)(?=\n- \(|\Z)",
        content,
        re.MULTILINE | re.DOTALL,
    )

    for i, (pts, body) in enumerate(blocks, start=1):
        # Extract answer(s) from the **Correct** section
        # Look for the block under **Correct** until the next bolded section (like Rubric)
        correct_section_match = re.search(
            r"\*\*Correct\*\*\s*\n(.*?)(?=\s*- \*\*|$)", body, re.DOTALL
        )
        if correct_section_match:
            ans_text = correct_section_match.group(1)
            # Find each bullet point in the captured group
            valid_answers = [
                m.strip() for m in re.findall(r"^\s*-\s*(.*)", ans_text, re.MULTILINE)
            ]
            solutions[f"q{i}"] = {"answers": valid_answers, "points": int(pts)}
    return solutions


def parse_student_submission(student_path: Path) -> dict:
    """
    Parses the Student Response Markdown file.
    Extracts Type and Student Response for each q# block.
    """
    student_data = {}
    content = student_path.read_text()

    # Find each block starting with "- qN:"
    # Captures the question ID and the indented block following it
    q_blocks = re.findall(
        r"^- (q\d+):\s*\n(.*?)(?=\n- q\d+:|\Z)", content, re.DOTALL | re.MULTILINE
    )

    for q_id, body in q_blocks:
        # Extract "Type"
        type_match = re.search(r"^\s*-\s*Type:\s*(.*)", body, re.MULTILINE)

        # Extract "Student Response"
        resp_match = re.search(r"^\s*-\s*Response text:\s*(.*)", body, re.MULTILINE)

        student_data[q_id] = {
            "type": type_match.group(1).strip() if type_match else None,
            "student_answer": resp_match.group(1).strip() if resp_match else None,
        }
    return student_data


def grade_student_file(student_path: Path, solutions: dict):
    """
    Compares student responses to the key and updates the student Markdown file
    with points earned based on the logic provided.
    """
    submission = parse_student_submission(student_path)

    # Read the full content of the student file to modify it
    content = student_path.read_text()

    for q_id, key_info in solutions.items():
        if q_id not in submission:
            continue

        student_ans = submission[q_id]["student_answer"]
        q_type = submission[q_id]["type"]
        max_pts = key_info["points"]

        # Skip if the student answer is blank/missing
        if not student_ans or student_ans.strip() == "":
            continue

        is_correct = student_ans.strip() in key_info["answers"]

        # Determine what to write in the Points field
        points_to_write = ""
        if q_type == "Multiple Choice":
            points_to_write = str(max_pts) if is_correct else "0"
        elif q_type == "Free Response":
            if is_correct:
                points_to_write = str(max_pts)
            else:
                # If incorrect Free Response, leave it blank
                points_to_write = ""

        # Update the Markdown content for this specific question block
        # We look for the block starting with "- qN:" and replace the Points field within it
        pattern = rf"(- {q_id}:.*?\n\s*- Points:)\s*(?=\n)"

        # First, update 'Possible points'
        content = re.sub(
            rf"(- {q_id}:.*?\n\s*- Possible points:)\s*(?=\n)",
            rf"\1 {max_pts}",
            content,
            flags=re.DOTALL,
        )

        # Second, update 'Points'
        content = re.sub(pattern, rf"\1 {points_to_write}", content, flags=re.DOTALL)

        # Third, update 'Analysis'
        analysis_to_write = "Correct" if is_correct else ""
        content = re.sub(
            rf"(- {q_id}:.*?\n\s*- Analysis:)\s*(?=\n)",
            rf"\1 {analysis_to_write}",
            content,
            flags=re.DOTALL,
        )

    # Save the updated content back to the file
    student_path.write_text(content)
    print(f"Successfully graded and updated {student_path.name}")


def setup_args() -> ArgumentParser:
    parser = ArgumentParser(
        description="Grade student markdown files using a solutions template.",
        epilog=EPILOG,
        formatter_class=RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--solutions", required=True, help="Path to the solutions markdown file"
    )
    parser.add_argument(
        "--input",
        required=True,
        help="Path to a single student file or a directory of files",
    )
    return parser


def run_cli() -> None:
    parser = setup_args()
    args = parser.parse_args()

    solutions_path = Path(args.solutions)
    if not solutions_path.exists():
        print(f"Error: Solutions file not found at {solutions_path}")
        sys.exit(1)

    solutions = parse_solutions(solutions_path)
    input_path = Path(args.input)

    if input_path.is_file():
        grade_student_file(input_path, solutions)
    elif input_path.is_dir():
        # Filter for .submission.md to avoid processing unrelated markdown files
        for md_file in sorted(input_path.glob("*.submission.md")):
            grade_student_file(md_file, solutions)
    else:
        print(f"Error: Input path '{args.input}' is not a file or directory.")
        sys.exit(1)


if __name__ == "__main__":
    run_cli()

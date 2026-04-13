from opengrader.core.questions import split_pdf_by_rectangles

# Hardcode question with rectangles: (page_index, x0, y0, x1, y1)
DEFAULT_QUESTIONS = {
    "question_1_2_3": (0, 100, 180, 550, 275),
    "question_4": (0, 100, 270, 550, 365),
    "question_5": (0, 100, 365, 550, 400),
    "question_6": (0, 100, 400, 550, 440),
    "question_7": (0, 100, 440, 550, 480),
    "question_8": (0, 100, 480, 550, 515),
    "question_9": (0, 100, 515, 550, 580),
    "question_10": (0, 100, 575, 550, 620),
}


def run_cli():
    # In the future, these could be arguments
    input_pdf = "assets/sample2.pdf"
    output_dir = "tests/question_split_output"

    split_pdf_by_rectangles(input_pdf, output_dir, DEFAULT_QUESTIONS)


if __name__ == "__main__":
    run_cli()

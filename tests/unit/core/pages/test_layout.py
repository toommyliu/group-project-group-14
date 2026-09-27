from opengrader.core.pages.layout import page_layout, question_regions
from opengrader.core.pages.printed import TextLine


def _line(text: str, y: float, x: float = 0.1, width: float = 0.5, height: float = 0.012) -> TextLine:
    return TextLine(text, (x, y, x + width, y + height))


def test_each_answer_region_runs_from_its_label_to_the_next():
    layout = page_layout([
        _line("1. (10 pts) Convert decimal 5 to binary.", 0.30),
        _line("2. (10 pts) Which literals are valid?", 0.50),
        _line("3", 0.95, x=0.49, width=0.01),
    ], printed_only=True)

    regions = [region for region, _ in question_regions(layout)]

    assert [r.key for r in regions] == ["q1", "q2"]
    assert regions[0].box[1] < 0.30 and abs(regions[0].box[3] - regions[1].box[1]) < 1e-9
    assert regions[1].box[3] < 0.95
    assert [r.points for r in regions] == [10.0, 10.0]
    assert layout.page_number == 3


def test_a_question_with_parts_gives_its_prompt_to_the_first_part():
    layout = page_layout([
        _line("5. (25 pts) Answer all parts.", 0.40),
        _line("a) (8 pts) Which algorithm?", 0.45, x=0.12),
        _line("b) (9 pts) Which number?", 0.60, x=0.12),
    ], printed_only=True)

    regions = [region for region, _ in question_regions(layout)]

    assert [r.key for r in regions] == ["q5a", "q5b"]
    assert regions[0].box[1] < 0.40


def test_prompts_drop_the_printed_label():
    layout = page_layout([_line("2. (1 point) if the number is stored big-endian", 0.5)], printed_only=True)

    (region, _), = question_regions(layout)

    assert region.prompt == "if the number is stored big-endian"


def test_the_last_answer_continues_into_the_gap_above_the_next_pages_first_question():
    this_page = page_layout([_line("4. (2 points) Write the code.", 0.80)], printed_only=True)
    next_page = page_layout([
        _line("Name:", 0.06, width=0.05), _line("ID:", 0.06, x=0.7, width=0.03),
        _line("5. (2 points) What does each level mean?", 0.15),
    ], printed_only=True)

    (region, continuation), = question_regions(this_page, next_page)

    assert region.key == "q4"
    assert continuation is not None and continuation[1] > 0.07 and continuation[3] < 0.15


def test_a_short_gap_below_a_running_header_is_not_a_continuation():
    next_page = page_layout([
        _line("Quiz 1: Mixed Topics", 0.03),
        _line("4. (10 pts) Which numbers are even?", 0.07),
    ], printed_only=True)

    assert next_page.top_band is None


def test_page_numbers_are_read_from_page_n_of_m_headers():
    layout = page_layout([_line("CMPE 30 Quiz 2", 0.05), _line("Page 2 of 2", 0.05, x=0.8, width=0.1),
                          _line("3. (25 points) Implement it.", 0.1)], printed_only=True)

    assert (layout.page_number, layout.page_count) == (2, 2)


def test_name_and_id_fields_stop_at_the_first_letter_of_the_next_label_on_the_line():
    layout = page_layout([
        _line("CMPE 30 Quiz 2 (100 points)", 0.05, width=0.25),
        _line("ID:", 0.05, x=0.39, width=0.03),
        _line("Name:", 0.05, x=0.59, width=0.05),
        _line("1. (50 points) Minimum score.", 0.2),
    ], printed_only=True)

    assert set(layout.fields) == {"name", "id", "title"}
    assert 0.59 < layout.fields["id"][2] < 0.605
    assert layout.fields["name"][0] < 0.59 < layout.fields["name"][2]
    assert layout.title.startswith("CMPE 30 Quiz 2")


def test_handwriting_beside_a_field_is_not_taken_for_the_title():
    layout = page_layout([
        _line("Name:", 0.10, width=0.05),
        _line("Ben Davis", 0.085, x=0.2, width=0.2, height=0.03),
        _line("SP26 CMPE 142 Exam 2B", 0.13, x=0.35, width=0.3, height=0.02),
        _line("1. (50 points) Minimum score.", 0.6),
    ], printed_only=False)

    assert layout.title == "SP26 CMPE 142 Exam 2B"


def test_assignments_without_point_labels_fall_back_to_numbered_questions_in_the_margin():
    layout = page_layout([
        _line("1. Explain recursion.", 0.2),
        _line("Step 2. is not a question", 0.3, x=0.3),
        _line("2. Define a heap.", 0.5),
    ], printed_only=True)

    assert [label.key for label in layout.labels] == ["q1", "q2"]


def test_a_question_starting_with_name_is_not_a_name_field():
    layout = page_layout([
        _line("1. (5 pts) Sorting.", 0.2),
        _line("Name three stable sorting algorithms.", 0.23),
    ], printed_only=True)

    assert "name" not in layout.fields


def test_the_title_box_stops_before_an_id_label_further_along_its_row():
    layout = page_layout([
        _line("CMPE 30 Quiz 25a (100 points)", 0.05, width=0.3),
        _line("ID:", 0.05, x=0.38, width=0.03),
        _line("1. (50 points) Minimum score.", 0.2),
    ], printed_only=True)

    assert layout.fields["title"][2] <= layout.fields["id"][0]


def test_an_instructions_paragraph_is_not_the_title_even_when_its_lines_read_taller():
    layout = page_layout([
        _line("Name:", 0.10, width=0.05), _line("ID:", 0.11, x=0.66, width=0.03),
        _line("SP26 CMPE 142 Exam 2B", 0.122, x=0.39, width=0.23, height=0.019),
        _line("You are not allowed to have anything in or over your ears.", 0.164, x=0.17, width=0.69, height=0.024),
        _line("electronics including smartwatch during the exam.", 0.178, x=0.15, width=0.7, height=0.024),
        _line("1. (6 points) What is a process?", 0.4),
    ], printed_only=True)

    assert layout.title == "SP26 CMPE 142 Exam 2B"


def test_handwriting_in_the_answer_space_is_not_part_of_the_prompt_but_printed_choices_are():
    layout = page_layout([
        _line("1. (10 pts) Convert decimal 5 to binary.", 0.366, x=0.176),
        _line("yus", 0.394, x=0.552, width=0.06, height=0.035),
        _line("no", 0.397, x=0.167, width=0.05, height=0.029),
        _line("2. (10 pts) What is the most significant bit", 0.55, x=0.176),
        _line("of the binary number 1010?", 0.566, x=0.176),
        _line("A) 0", 0.584, x=0.204, width=0.04),
        _line("B) 1", 0.602, x=0.204, width=0.04),
        _line("maybe", 0.7, x=0.3, width=0.1, height=0.03),
    ], printed_only=False)

    prompts = [region.prompt for region, _ in question_regions(layout)]

    assert prompts == ["Convert decimal 5 to binary.", "What is the most significant bit of the binary number 1010? A) 0 B) 1"]

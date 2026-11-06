from opengrader.core.pages.printed import TextLine, merge_lines, split_field_labels
from opengrader.core.pdf.templates.fonts import CMU_REGULAR_FILE


def test_a_title_merged_with_field_labels_is_cut_at_each_label():
    merged = TextLine("CMPE 30 Quiz 2 ID: 1234 Name: Ana", (0.1, 0.05, 0.9, 0.07))

    pieces = split_field_labels([merged])

    assert [p.text for p in pieces] == ["CMPE 30 Quiz 2", "ID: 1234", "Name: Ana"]
    assert pieces[0].box[2] <= pieces[1].box[0] + 1e-9


def test_words_like_identifier_are_not_field_labels():
    line = TextLine("Which is a valid identifier?", (0.1, 0.5, 0.6, 0.52))

    assert split_field_labels([line]) == [line]


def test_fragments_on_one_row_are_joined_but_a_field_label_starts_its_own_line():
    fragments = [
        TextLine("3. (10 pts)", (0.10, 0.50, 0.20, 0.52)),
        TextLine("What is the MSB?", (0.21, 0.50, 0.45, 0.52)),
        TextLine("Name", (0.10, 0.20, 0.15, 0.22)),
        TextLine("Tommy Liu", (0.16, 0.20, 0.30, 0.22)),
    ]

    lines = merge_lines(fragments)

    assert [line.text for line in lines] == ["Name", "Tommy Liu", "3. (10 pts) What is the MSB?"]


def test_what_a_student_wrote_after_a_label_is_not_part_of_the_printed_label():
    from opengrader.core.pages.printed import _label_only

    line = _label_only(TextLine("ID: 5552831", (0.4, 0.05, 0.6, 0.07)))

    assert line.text == "ID:" and line.box[2] < 0.5


def _title_image(subscript: str | None) -> "Image.Image":
    from PIL import Image, ImageDraw, ImageFont

    regular, small = ImageFont.truetype(str(CMU_REGULAR_FILE), 40), ImageFont.truetype(str(CMU_REGULAR_FILE), 28)
    image = Image.new("RGB", (900, 90), "white")
    pen = ImageDraw.Draw(image)
    x = 20 + pen.textlength("CMPE 30 Quiz 2", font=regular)
    pen.text((20, 20), "CMPE 30 Quiz 2", fill="black", font=regular)
    if subscript:
        pen.text((x + 1, 38), subscript, fill="black", font=small)
        x += pen.textlength(subscript, font=small) + 1
    pen.text((x + 12, 20), "(100 points)    ID:", fill="black", font=regular)
    return image.rotate(0.8, fillcolor="white")


def test_a_subscript_is_found_by_its_size_and_position_but_descenders_are_not():
    import numpy as np

    from opengrader.core.pages.printed import subscript_starts

    assert len(subscript_starts(np.asarray(_title_image("5a").convert("L")))) == 1
    assert subscript_starts(np.asarray(_title_image(None).convert("L"))) == []


def test_a_title_reads_with_its_subscript_marked_and_without_the_field_label_after_it():
    from opengrader.core.pages.printed import read_title

    assert read_title(_title_image("5a")) == "CMPE 30 Quiz 2_5a (100 points)"
    assert read_title(_title_image(None)) == "CMPE 30 Quiz 2 (100 points)"


def test_reading_one_line_does_not_leave_the_engine_skipping_text_detection():
    from opengrader.core.pages.printed import read_lines, recognize_line

    title = _title_image(None)
    recognize_line(title)

    assert [line.text for line in read_lines(title)][:1] == ["CMPE 30 Quiz 2 (100 points)"]


def test_a_label_is_cut_where_the_recognizer_saw_its_word_start():
    merged = TextLine("CMPE 30 Quiz 25a (100 points) ID:", (0.15, 0.04, 0.48, 0.07))
    word_starts = [[(0, 0.15), (5, 0.20), (8, 0.23), (13, 0.29), (17, 0.32), (22, 0.36), (30, 0.43)]]

    title, label = split_field_labels([merged], word_starts)

    assert (title.text, label.text) == ("CMPE 30 Quiz 25a (100 points)", "ID:")
    assert label.box[0] == 0.43 and title.box[2] == 0.43


def _field_image(label: str, written: str, beside: str = "") -> "Image.Image":
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(str(CMU_REGULAR_FILE), 34)
    image = Image.new("RGB", (1000, 130), "white")
    pen = ImageDraw.Draw(image)
    pen.text((20, 30), f"{label} {written}", fill="black", font=font)
    if beside:
        pen.text((560, 70), beside, fill="black", font=font)
    return image


def test_a_field_reads_what_was_written_without_its_label_or_the_print_beside_it():
    from opengrader.core.pages.printed import read_field

    title = (0.55, 0.5, 0.95, 0.85)
    name, _ = read_field(_field_image("Name:", "Ann Lee", beside="SP26 CMPE 142 Exam 2"), "name", printed=(title,))

    assert name == "Ann Lee"
    assert read_field(_field_image("Student ID", "123 456 789"), "sid")[0] == "123456789"


def test_the_first_letter_of_the_next_label_is_not_read_as_writing():
    from PIL import Image, ImageDraw, ImageFont

    from opengrader.core.pages.printed import read_field

    font = ImageFont.truetype(str(CMU_REGULAR_FILE), 34)
    image = Image.new("RGB", (420, 130), "white")
    pen = ImageDraw.Draw(image)
    pen.text((20, 30), "Name: Ann Lee", fill="black", font=font)
    pen.text((400, 30), "ID:", fill="black", font=font)

    assert read_field(image, "name")[0] != "Ann Lee"
    assert read_field(image, "name", written_width=400 / 420)[0] == "Ann Lee"


def test_a_tall_handwritten_answer_beside_a_printed_line_is_not_joined_to_it():
    fragments = [
        TextLine("d) (5 pts) Which data structure uses FIFO?", (0.20, 0.700, 0.60, 0.714)),
        TextLine("queue", (0.62, 0.690, 0.70, 0.724)),
    ]

    assert sorted(line.text for line in merge_lines(fragments)) == ["d) (5 pts) Which data structure uses FIFO?", "queue"]


def test_app_thread_budget_leaves_most_cores_to_the_user():
    from opengrader.core.pages.printed import app_thread_budget

    assert [app_thread_budget(cpus) for cpus in (1, 4, 8, 16, 64)] == [1, 1, 2, 4, 4]


def test_changing_the_thread_limit_drops_an_engine_built_with_the_old_one(monkeypatch):
    from opengrader.core.pages import printed

    monkeypatch.setattr(printed, "_threads", -1)
    sentinel = object()
    monkeypatch.setattr(printed, "_engine", sentinel)

    printed.limit_threads(-1)
    assert printed._engine is sentinel  # same count: keep it

    printed.limit_threads(2)
    assert printed._engine is None
    assert printed._threads == 2

import fitz
import numpy as np
import pytest
from PIL import Image

from opengrader.core.bubble_sheet import BubbleQuestion, build_bubble_sheet_layout
from opengrader.core.ocr import omr
from opengrader.core.pdf.templates.bubble_sheet import render_bubble_sheet
from opengrader.core.pdf_utils import render_pdf_page

QUESTIONS = [BubbleQuestion(question_id=f"q{i}") for i in range(1, 11)]


def _sheet(tmp_path, marks=None, *, shade=0.0, radius_scale=0.92):  # noqa: ANN001
    layout = build_bubble_sheet_layout(QUESTIONS)
    doc = fitz.Document()
    render_bubble_sheet(doc, layout, title="Test Quiz", student_id="1001", version="A")
    if marks:
        page = doc[0]
        width, height = page.rect.width, page.rect.height
        for row in layout.rows:
            wanted = marks.get(row.question_id)
            if not wanted:
                continue
            wanted = (wanted,) if isinstance(wanted, str) else wanted
            for bubble in row.bubbles:
                if bubble.choice_label not in wanted:
                    continue
                colour = (shade, shade, shade)
                page.draw_circle(
                    fitz.Point(bubble.cx * width, bubble.cy * height),
                    bubble.radius * width * radius_scale,
                    color=colour,
                    fill=colour,
                    width=0,
                )
    path = tmp_path / "sheet.pdf"
    doc.save(str(path))
    doc.close()
    return layout, path


def _read(path, layout, dpi=200, transform=None):  # noqa: ANN001
    image, _width, _height = render_pdf_page(str(path), 1, dpi=dpi)
    if transform is not None:
        image = transform(image)
    return omr.read_page_image(image, layout, 1)


def _pad(image, fraction=0.06):
    width, height = image.size
    px, py = int(width * fraction), int(height * fraction)
    canvas = Image.new("L", (width + 2 * px, height + 2 * py), 255)
    canvas.paste(image.convert("L"), (px, py))
    return canvas


def _answers(result):  # noqa: ANN001
    return {r.question_id: (r.status, r.choice_label) for r in result.readings}


def test_a_blank_sheet_reads_as_every_question_blank(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path)
    result = _read(path, layout)
    assert result.ok and result.registered
    assert all(reading.status == "blank" for reading in result.readings)


def test_marks_read_back_as_the_choices_that_were_filled(tmp_path) -> None:  # noqa: ANN001
    marks = {"q1": "A", "q2": "C", "q3": "E", "q7": "B"}
    layout, path = _sheet(tmp_path, marks)
    answers = _answers(_read(path, layout))
    for question_id, choice in marks.items():
        assert answers[question_id] == ("marked", choice)
    assert answers["q4"] == ("blank", "")


def test_every_choice_position_is_read_correctly(tmp_path) -> None:  # noqa: ANN001
    marks = {f"q{i}": "ABCDE"[i % 5] for i in range(1, 11)}
    layout, path = _sheet(tmp_path, marks)
    answers = _answers(_read(path, layout))
    assert {q: c for q, (_status, c) in answers.items()} == marks


def test_an_unmarked_bubble_measures_far_below_the_decision_threshold(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path)
    image, _width, _height = render_pdf_page(str(path), 1, dpi=200)
    binary = omr._binarize(omr._to_gray(image))
    height, width = binary.shape[:2]
    transform = omr.build_page_transform(binary, layout, 1)
    fills = [
        omr.measure_bubble_fill(binary, bubble, transform, width, height)
        for row in layout.rows
        for bubble in row.bubbles
    ]
    assert max(fills) <= omr.EMPTY_BUBBLE_FILL_CEILING


def test_a_filled_bubble_measures_far_above_the_threshold(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path, {"q1": "B"})
    image, _width, _height = render_pdf_page(str(path), 1, dpi=200)
    binary = omr._binarize(omr._to_gray(image))
    height, width = binary.shape[:2]
    transform = omr.build_page_transform(binary, layout, 1)
    bubble = next(b for b in layout.rows[0].bubbles if b.choice_label == "B")
    assert omr.measure_bubble_fill(binary, bubble, transform, width, height) > 0.8


def test_a_light_pencil_mark_still_reads(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path, {"q1": "D"}, shade=0.55, radius_scale=0.75)
    assert _answers(_read(path, layout))["q1"] == ("marked", "D")


def test_two_marks_on_one_question_are_refused_not_guessed(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path, {"q1": ("B", "D"), "q2": "A"})
    answers = _answers(_read(path, layout))
    assert answers["q1"] == ("ambiguous", "")
    assert answers["q2"] == ("marked", "A")


@pytest.mark.parametrize("degrees", [1, 3, 5, 8])
def test_a_skewed_scan_still_reads_correctly(tmp_path, degrees) -> None:  # noqa: ANN001
    marks = {"q1": "A", "q5": "C", "q10": "E"}
    layout, path = _sheet(tmp_path, marks)

    def skew(image):
        return _pad(image).rotate(
            degrees, resample=Image.BICUBIC, fillcolor=255, expand=True
        )

    answers = _answers(_read(path, layout, transform=skew))
    for question_id, choice in marks.items():
        assert answers[question_id] == ("marked", choice)


def test_a_noisy_low_contrast_scan_still_reads_correctly(tmp_path) -> None:  # noqa: ANN001
    marks = {"q2": "B", "q6": "D"}
    layout, path = _sheet(tmp_path, marks)

    def scanify(image):
        array = np.asarray(_pad(image), dtype=np.float32) * 0.85 + 25
        noise = np.random.default_rng(11).normal(0, 8, array.shape)
        return Image.fromarray(np.clip(array + noise, 0, 255).astype(np.uint8))

    answers = _answers(_read(path, layout, transform=scanify))
    for question_id, choice in marks.items():
        assert answers[question_id] == ("marked", choice)


@pytest.mark.parametrize("dpi", [100, 150, 300])
def test_reading_is_stable_across_scan_resolutions(tmp_path, dpi) -> None:  # noqa: ANN001
    marks = {"q3": "C", "q9": "A"}
    layout, path = _sheet(tmp_path, marks)
    answers = _answers(_read(path, layout, dpi=dpi, transform=_pad))
    for question_id, choice in marks.items():
        assert answers[question_id] == ("marked", choice)


def test_a_page_that_is_not_a_bubble_sheet_is_refused_not_read(tmp_path) -> None:  # noqa: ANN001
    layout, _path = _sheet(tmp_path)
    blank = fitz.Document()
    blank.new_page(width=612, height=792)
    other = tmp_path / "other.pdf"
    blank.save(str(other))
    blank.close()

    result = _read(other, layout)
    assert not result.ok
    assert not result.registered
    assert result.readings == ()
    assert "registration" in result.reason


def test_registration_fails_when_the_corners_are_cropped_off(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path, {"q1": "A"})

    def crop(image):
        width, height = image.size
        return image.crop((int(width * 0.15), int(height * 0.15), width, height))

    assert not _read(path, layout, transform=crop).ok


def test_corner_marks_are_assigned_by_position_not_image_corner(tmp_path) -> None:  # noqa: ANN001
    layout, path = _sheet(tmp_path, {"q4": "B"})

    def offset(image):
        width, height = image.size
        canvas = Image.new("L", (int(width * 1.4), int(height * 1.25)), 255)
        canvas.paste(image.convert("L"), (int(width * 0.35), int(height * 0.2)))
        return canvas

    assert _answers(_read(path, layout, transform=offset))["q4"] == ("marked", "B")


def test_a_multi_page_sheet_reads_every_page(tmp_path) -> None:  # noqa: ANN001
    questions = [BubbleQuestion(question_id=f"q{i}") for i in range(1, 80)]
    layout = build_bubble_sheet_layout(questions)
    assert layout.page_count == 2

    doc = fitz.Document()
    render_bubble_sheet(doc, layout, title="Long Quiz", student_id="1001")
    marks = {"q1": "A", "q70": "D"}
    for page_number in range(1, layout.page_count + 1):
        page = doc[page_number - 1]
        width, height = page.rect.width, page.rect.height
        for row in layout.rows_on_page(page_number):
            wanted = marks.get(row.question_id)
            if not wanted:
                continue
            for bubble in row.bubbles:
                if bubble.choice_label == wanted:
                    page.draw_circle(
                        fitz.Point(bubble.cx * width, bubble.cy * height),
                        bubble.radius * width * 0.92,
                        color=(0, 0, 0), fill=(0, 0, 0), width=0,
                    )
    path = tmp_path / "long.pdf"
    doc.save(str(path))
    doc.close()

    result = omr.read_sheet_pdf(path, layout)
    assert result.ok
    answers = {r.question_id: (r.status, r.choice_label) for r in result.readings}
    assert answers["q1"] == ("marked", "A")
    assert answers["q70"] == ("marked", "D")
    assert len(result.readings) == len(questions)


def test_a_sheet_shorter_than_the_layout_reads_what_is_there(tmp_path) -> None:  # noqa: ANN001
    questions = [BubbleQuestion(question_id=f"q{i}") for i in range(1, 80)]
    layout = build_bubble_sheet_layout(questions)
    doc = fitz.Document()
    render_bubble_sheet(doc, layout, title="Long Quiz", student_id="1001")
    doc.delete_page(1)
    path = tmp_path / "short.pdf"
    doc.save(str(path))
    doc.close()

    result = omr.read_sheet_pdf(path, layout)
    assert result.ok
    read_ids = {reading.question_id for reading in result.readings}
    assert read_ids == {row.question_id for row in layout.rows_on_page(1)}


def test_a_scrambled_sheet_reads_back_to_the_right_questions(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.bubble_sheet import form_key_for, shuffle_questions

    key = {"q1": "A", "q2": "C", "q3": "E", "q4": "B", "q5": "D",
           "q6": "A", "q7": "C", "q8": "B", "q9": "E", "q10": "D"}

    form_key = form_key_for("quiz|student-1")
    scrambled = shuffle_questions(QUESTIONS, form_key)
    assert [q.question_id for q in scrambled] != [q.question_id for q in QUESTIONS]

    layout = build_bubble_sheet_layout(scrambled)
    doc = fitz.Document()
    render_bubble_sheet(doc, layout, title="Scrambled", student_id="1", form_key=form_key)
    page = doc[0]
    width, height = page.rect.width, page.rect.height
    for row in layout.rows:
        wanted = key[row.question_id]
        for bubble in row.bubbles:
            if bubble.choice_label == wanted:
                page.draw_circle(
                    fitz.Point(bubble.cx * width, bubble.cy * height),
                    bubble.radius * width * 0.92,
                    color=(0, 0, 0), fill=(0, 0, 0), width=0,
                )
    path = tmp_path / "scrambled.pdf"
    doc.save(str(path))
    doc.close()

    answers = _answers(_read(path, layout))
    for question_id, choice in key.items():
        assert answers[question_id] == ("marked", choice), question_id


def test_a_scrambled_sheet_read_against_the_wrong_order_is_wrong(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.bubble_sheet import form_key_for, shuffle_questions

    form_key = form_key_for("quiz|student-1")
    scrambled = shuffle_questions(QUESTIONS, form_key)
    scrambled_layout = build_bubble_sheet_layout(scrambled)
    base_layout = build_bubble_sheet_layout(QUESTIONS)

    doc = fitz.Document()
    render_bubble_sheet(doc, scrambled_layout, title="Scrambled", student_id="1")
    page = doc[0]
    width, height = page.rect.width, page.rect.height
    for row in scrambled_layout.rows:
        for bubble in row.bubbles:
            if bubble.choice_label == "A":
                page.draw_circle(
                    fitz.Point(bubble.cx * width, bubble.cy * height),
                    bubble.radius * width * 0.92,
                    color=(0, 0, 0), fill=(0, 0, 0), width=0,
                )
    path = tmp_path / "scrambled.pdf"
    doc.save(str(path))
    doc.close()

    correct = _read(path, scrambled_layout)
    assert correct.ok
    assert [r.question_id for r in correct.readings] == [
        row.question_id for row in scrambled_layout.rows
    ]
    assert [r.question_id for r in correct.readings] != [
        row.question_id for row in base_layout.rows
    ]


def test_a_scrambled_sheets_qr_carries_its_form_key(tmp_path) -> None:  # noqa: ANN001
    from opengrader.core.bubble_sheet import form_key_for, shuffle_questions
    from opengrader.core.ocr.qr_reader import read_code_from_image

    form_key = form_key_for("quiz|student-9")
    layout = build_bubble_sheet_layout(shuffle_questions(QUESTIONS, form_key))
    doc = fitz.Document()
    render_bubble_sheet(
        doc, layout, title="Scrambled", student_id="1009", form_key=form_key
    )
    path = tmp_path / "form.pdf"
    doc.save(str(path))
    doc.close()

    for dpi in (150, 300):
        image, _w, _h = render_pdf_page(str(path), 1, dpi=dpi)
        code = read_code_from_image(image)
        if code is not None and code.form == form_key:
            break
    else:  # pragma: no cover
        raise AssertionError("form key could not be read at any resolution")
    assert code.student_id == "1009"

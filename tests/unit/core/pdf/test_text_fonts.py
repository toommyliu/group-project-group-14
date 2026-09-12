from __future__ import annotations

from opengrader.core.pdf.text_fonts import load_text_fonts


def test_latin_text_uses_the_document_typeface() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("Ada Lovelace")
    assert drawable.fontname == fonts.regular[0].fontname
    assert drawable.text == "Ada Lovelace"


def test_accented_and_cyrillic_stay_in_the_document_typeface() -> None:
    fonts = load_text_fonts()
    for name in ("José García", "Nguyễn Văn A", "Дмитрий Иванов", "Ελένη"):
        drawable = fonts.choose(name)
        assert drawable.fontname == fonts.regular[0].fontname
        assert drawable.text == name


def test_han_text_falls_back_to_a_font_that_can_draw_it() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("李明")
    assert drawable.fontname != fonts.regular[0].fontname
    assert drawable.text == "李明"


def test_mixed_latin_and_han_resolves_to_one_font() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("李明 · ID: s4")
    assert drawable.text == "李明 · ID: s4"


def test_unrenderable_characters_become_visible_placeholders() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("عبد")
    assert drawable.text == "???"


def test_unrenderable_characters_do_not_damage_their_neighbours() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("Student عبد ID 7")
    assert drawable.text == "Student ??? ID 7"


def test_bold_and_regular_are_resolved_separately() -> None:
    fonts = load_text_fonts()
    assert fonts.choose("Total", bold=True).fontname == fonts.bold[0].fontname
    assert fonts.choose("Total", bold=False).fontname == fonts.regular[0].fontname


def test_width_grows_with_font_size() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("Question 1: 8 / 10")
    assert drawable.width(20) > drawable.width(10) > 0


def test_empty_text_is_measurable() -> None:
    fonts = load_text_fonts()
    assert fonts.choose("").width(11) == 0


def test_fontfile_lookup_distinguishes_embedded_from_builtin() -> None:
    fonts = load_text_fonts()
    embedded = fonts.fontfile_for(fonts.regular[0].fontname)
    assert embedded is None or embedded.endswith(".ttf")
    assert fonts.fontfile_for("no-such-font") is None

"""Font selection for generated PDF text (`core/pdf/text_fonts.py`)."""

from __future__ import annotations

from opengrader.core.pdf.text_fonts import load_text_fonts


def test_latin_text_uses_the_document_typeface() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("Ada Lovelace")
    # The first candidate is the bundled Computer Modern face the exam template
    # is set in; plain Latin must never fall through to a fallback.
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
    assert drawable.text == "李明"  # unchanged: the fallback really covers it


def test_mixed_latin_and_han_resolves_to_one_font() -> None:
    # A whole line is drawn in a single font, so the fallback has to cover the
    # Latin around the Han characters as well — otherwise "ID: s4" would vanish.
    fonts = load_text_fonts()
    drawable = fonts.choose("李明 · ID: s4")
    assert drawable.text == "李明 · ID: s4"


def test_unrenderable_characters_become_visible_placeholders() -> None:
    fonts = load_text_fonts()
    drawable = fonts.choose("عبد")
    # Every character is accounted for — nothing is silently dropped.
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
    # Wrapping calls width() on empty runs (a question with no comment), so this
    # must be a plain zero rather than an exception.
    fonts = load_text_fonts()
    assert fonts.choose("").width(11) == 0


def test_fontfile_lookup_distinguishes_embedded_from_builtin() -> None:
    fonts = load_text_fonts()
    embedded = fonts.fontfile_for(fonts.regular[0].fontname)
    # The bundled TTF is embedded from a file; the CJK fallback is a built-in
    # that PyMuPDF names rather than reads off disk.
    assert embedded is None or embedded.endswith(".ttf")
    assert fonts.fontfile_for("no-such-font") is None

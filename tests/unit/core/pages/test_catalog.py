from pathlib import Path

import numpy as np

from opengrader.core.pages.catalog import (
    PageCatalog,
    PageGroup,
    PageMatch,
    ScanPage,
    consensus_lines,
    load_catalog,
    save_catalog,
    turn_homography,
)
from opengrader.core.pages.layout import page_layout
from opengrader.core.pages.printed import TextLine


def _line(text, y, x=0.1):
    return TextLine(text, (x, y, x + 0.4, y + 0.012))


def test_text_seen_on_two_copies_is_printed_and_one_students_writing_is_not():
    copies = [
        [_line("3. (10 pts) What is the MSB?", 0.5), _line("maybe", 0.55)],
        [_line("3. (10 pts) What is the MSB?", 0.501), _line("yes", 0.56, x=0.6)],
        [_line("3. (1O pts) What is the MSB?", 0.499)],
    ]

    printed = consensus_lines(copies)

    assert [line.text for line in printed] == ["3. (10 pts) What is the MSB?"]


def test_quarter_turns_map_the_page_corners_onto_the_turned_page():
    corner = np.array([0.0, 0.0, 1.0])
    assert np.allclose(turn_homography(1) @ corner, [1.0, 0.0, 1.0])
    assert np.allclose(turn_homography(2) @ corner, [1.0, 1.0, 1.0])
    assert np.allclose(turn_homography(3) @ corner, [0.0, 1.0, 1.0])


def test_a_catalog_survives_a_save_and_load(tmp_path: Path):
    lines = (_line("Name:", 0.1), _line("1. (5 pts) Define a heap.", 0.3))
    group = PageGroup(id="g01", reference=ScanPage(tmp_path / "scan.pdf", 0), reference_turns=2,
                      layout=page_layout(lines, printed_only=True), page_number=1, members=3, copies_read=3,
                      question_types={"q1": "free_response"})
    catalog = PageCatalog(
        groups={"g01": group},
        matches=[PageMatch(ScanPage(tmp_path / "scan.pdf", 0), "g01", np.eye(3), 900),
                 PageMatch(ScanPage(tmp_path / "scan.pdf", 1), None, None)],
    )

    save_catalog(catalog, tmp_path / "catalog")
    loaded = load_catalog(tmp_path / "catalog")

    restored = loaded.groups["g01"]
    assert restored.reference_turns == 2 and restored.page_number == 1
    assert [label.key for label in restored.layout.labels] == ["q1"]
    assert restored.question_types == {"q1": "free_response"}
    assert loaded.matches[1].group is None and loaded.matches[0].inliers == 900


def _numbering(printed: list[int | None], identity: set[int], scan: list[int]) -> list[int | None]:
    from opengrader.core.pages.catalog import _number_pages

    header = (_line("Name:", 0.05),)
    groups = {
        str(i): PageGroup(id=str(i), reference=ScanPage(Path("s.pdf"), i), reference_turns=0,
                          layout=page_layout(header if i in identity else (), printed_only=True), page_number=n)
        for i, n in enumerate(printed)
    }
    _number_pages(groups, [PageMatch(ScanPage(Path("s.pdf"), k), str(g), np.eye(3)) for k, g in enumerate(scan)])
    return [groups[str(i)].page_number for i in range(len(printed))]


def test_an_unnumbered_last_page_follows_the_page_before_it_even_with_a_name_header():
    numbers = _numbering([1, 2, 3, 4, 5, None], identity={0, 1, 2, 3, 4, 5}, scan=[0, 1, 2, 3, 4, 5] * 3)

    assert numbers == [1, 2, 3, 4, 5, 6]


def test_an_unnumbered_first_page_comes_before_page_two():
    numbers = _numbering([None, 2], identity={0}, scan=[0, 1, 0, 1, 0, 1])

    assert numbers == [1, 2]


def test_a_moved_folder_still_finds_its_scans(tmp_path: Path):
    folder = tmp_path / "class"
    (folder / "scans").mkdir(parents=True)
    scan = folder / "scans" / "bulk.pdf"
    scan.write_bytes(b"%PDF-1.4")
    group = PageGroup(id="g01", reference=ScanPage(scan, 0), reference_turns=0, layout=page_layout((), printed_only=True))
    save_catalog(PageCatalog(groups={"g01": group}, matches=[PageMatch(ScanPage(scan, 0), "g01", np.eye(3))]),
                 folder / "split" / ".opengrader-pages")

    moved = tmp_path / "moved-class"
    folder.rename(moved)
    loaded = load_catalog(moved / "split" / ".opengrader-pages")

    assert loaded.groups["g01"].reference.pdf == (moved / "scans" / "bulk.pdf").resolve()
    assert loaded.groups["g01"].reference.pdf.exists()


def test_labels_read_only_on_the_reference_copy_are_printed_but_its_handwriting_is_not():
    reference = [_line("ID:", 0.05), _line("Ann Lee", 0.051, x=0.3), _line("1. (5 pts) Define a heap.", 0.3)]
    others = [[_line("1. (5 pts) Define a heap.", 0.301)], [_line("1. (5 pts) Define a heap.", 0.299), _line("Bob", 0.05, x=0.3)]]

    printed = consensus_lines([reference, *others])

    assert [line.text for line in printed] == ["ID:", "1. (5 pts) Define a heap."]


def test_a_sideways_page_is_turned_upright_even_though_its_vertical_text_can_be_read(monkeypatch):
    from PIL import Image

    from opengrader.core.pages import catalog

    upright = [TextLine("4. (10 pts) Which numbers are even?", (0.1, 0.10, 0.6, 0.115)),
               TextLine("5. (25 pts) Answer all parts.", (0.1, 0.40, 0.6, 0.415))]
    sideways = [TextLine(l.text, (l.box[1], 1 - l.box[2], l.box[3], 1 - l.box[0])) for l in upright]
    def render(page, dpi, *, turns=0):
        return Image.new("L", (850, 1100) if turns % 2 else (1100, 850))

    monkeypatch.setattr(catalog, "render", render)
    monkeypatch.setattr(catalog, "read_lines", lambda image: upright if image.height > image.width else sideways)

    turns, lines = catalog._upright_lines(ScanPage(Path("scan.pdf"), 0))

    assert turns == 1 and lines == upright


def test_an_answer_that_two_of_five_students_wrote_in_the_same_place_is_not_print():
    label = _line("4. (10 pts) Which of these numbers are even?", 0.13)
    copies = [[label, _line("50", 0.36, x=0.35)], [label, _line("50", 0.361, x=0.35)], [label], [label], [label]]

    assert [line.text for line in consensus_lines(copies)] == ["4. (10 pts) Which of these numbers are even?"]

import json
from pathlib import Path

import fitz

from opengrader.core.pages import order_review
from opengrader.core.pages.order_review import (
    FIXED,
    KEPT,
    PENDING,
    SORTED,
    OrderFinding,
    keep_pages,
    pending_findings,
    read_order_review,
    review_in_terminal,
    review_path,
    sort_pages,
    write_order_review,
)
from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions.scan_notes import note_path
from opengrader.core.submissions.extra_pages import read_extra_pages_map, write_extra_pages_map
from opengrader.core.submissions.splitter import read_scan_warnings, write_scan_warnings

WARNING = "Pages look out of order in the scan (page numbers read as 1, 3, 2); they were left as scanned."


def _split_pdf(directory: Path, name: str, numbers: list[int | None]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    pdf = directory / f"{name}.pdf"
    doc = fitz.open()
    for number in numbers:
        page = doc.new_page(width=612, height=792)
        page.insert_text((280, 770), f"Page {number}" if number else "scratch")
    doc.save(str(pdf))
    doc.close()
    pdf.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": f"g{number}" if number else None, "page_number": number} for number in numbers],
    }))
    return pdf


def _footers(pdf: Path) -> list[str]:
    with fitz.open(str(pdf)) as doc:
        return [page.get_text().strip() for page in doc]


def _flagged(directory: Path, name: str, numbers: list[int | None], **fields) -> Path:
    pdf = _split_pdf(directory, name, numbers)
    write_scan_warnings(pdf, (WARNING, "Missing page(s) 4."))
    existing, positions = read_order_review(directory)
    write_order_review(directory, [*existing, OrderFinding(name=name, keys=tuple(numbers), warning=WARNING, **fields)], positions)
    return pdf


def test_nothing_recorded_means_nothing_to_review(tmp_path: Path):
    assert pending_findings(tmp_path) == []
    review_path(tmp_path).parent.mkdir(parents=True)
    review_path(tmp_path).write_text("{not json")
    assert pending_findings(tmp_path) == []


def test_sorting_moves_the_pages_and_everything_that_addresses_them_by_position(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, None, 2])
    write_extra_pages_map(tmp_path, {"submission-0001": (3,)})

    assert sort_pages(tmp_path, ["submission-0001"]) == ["submission-0001"]

    assert _footers(pdf) == ["Page 1", "Page 2", "Page 3", "scratch"]
    assert [page["group"] for page in json.loads(note_path(pdf, SIDECAR_SUFFIX).read_text())["pages"]] == ["g1", "g2", "g3", None]
    assert not pdf.with_suffix(SIDECAR_SUFFIX).exists()
    assert read_extra_pages_map(tmp_path) == {"submission-0001": (4,)}
    assert read_scan_warnings(pdf) == ("Missing page(s) 4.",)
    assert read_order_review(tmp_path)[0][0].status == SORTED
    assert pending_findings(tmp_path) == []


def test_keeping_moves_nothing_and_stops_asking(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])

    assert keep_pages(tmp_path, ["submission-0001"]) == ["submission-0001"]

    assert _footers(pdf) == ["Page 1", "Page 3", "Page 2"]
    assert read_scan_warnings(pdf) == ("Missing page(s) 4.",)
    assert read_order_review(tmp_path)[0][0].status == KEPT
    assert pending_findings(tmp_path) == []


def test_only_the_named_submissions_are_touched(tmp_path: Path):
    first = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    second = _flagged(tmp_path, "submission-0002", [2, 1, 3])

    assert sort_pages(tmp_path, ["submission-0002", "submission-0009"]) == ["submission-0002"]

    assert _footers(first) == ["Page 1", "Page 3", "Page 2"]
    assert _footers(second) == ["Page 1", "Page 2", "Page 3"]
    assert [finding.name for finding in pending_findings(tmp_path)] == ["submission-0001"]


def test_a_decision_is_not_applied_twice(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    sort_pages(tmp_path, ["submission-0001"])

    assert sort_pages(tmp_path, ["submission-0001"]) == []
    assert _footers(pdf) == ["Page 1", "Page 2", "Page 3"]


def test_only_a_template_makes_sorting_the_recommendation():
    assert OrderFinding("s", (1, 3, 2), by_template=True).recommended
    assert not OrderFinding("s", (1, 3, 2)).recommended
    assert not OrderFinding("s", (1, 3, 2), by_template=True, shared=True).recommended


def _hand_edit(monkeypatch, pdf: Path, groups: list[str | None]) -> None:
    from opengrader.core.pages import catalog, extract
    from opengrader.core.pages.catalog import PageCatalog, PageGroup, ScanPage
    from opengrader.core.pages.layout import PageLayout

    known = {
        f"g{n}": PageGroup(id=f"g{n}", reference=ScanPage(pdf, 0), reference_turns=0, layout=PageLayout(), page_number=n)
        for n in (1, 2, 3)
    }
    monkeypatch.setattr(catalog, "load_catalog", lambda _directory: PageCatalog(groups=known, matches=[]))
    monkeypatch.setattr(extract, "page_groups", lambda _pdf, _catalog, **_kwargs: groups)
    with fitz.open(str(pdf)) as doc:
        doc.new_page()
        doc.save(str(pdf) + ".edited")
    Path(str(pdf) + ".edited").replace(pdf)


def test_pages_put_in_order_by_hand_are_no_longer_asked_about(tmp_path: Path, monkeypatch):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    _hand_edit(monkeypatch, pdf, ["g1", "g2", "g3"])

    assert pending_findings(tmp_path) == []

    assert read_order_review(tmp_path)[0][0].status == FIXED
    assert read_scan_warnings(pdf) == ("Missing page(s) 4.",)
    assert [page["group"] for page in json.loads(note_path(pdf, SIDECAR_SUFFIX).read_text())["pages"]] == ["g1", "g2", "g3"]


def test_a_hand_edit_that_is_still_out_of_order_is_asked_about_as_it_now_reads(tmp_path: Path, monkeypatch):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    _hand_edit(monkeypatch, pdf, ["g2", "g1", "g3"])

    (finding,) = pending_findings(tmp_path)

    assert finding.keys == (2, 1, 3) and finding.status == PENDING
    assert "2, 1, 3" in read_scan_warnings(pdf)[0]


def test_an_untouched_pdf_is_not_read_again(tmp_path: Path, monkeypatch):
    _flagged(tmp_path, "submission-0001", [1, 3, 2])
    monkeypatch.setattr(order_review, "_write_groups", lambda *_args: (_ for _ in ()).throw(AssertionError("re-read")))

    assert len(pending_findings(tmp_path)) == 1


def test_a_submission_whose_pdf_is_gone_is_dropped(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    pdf.unlink()

    assert pending_findings(tmp_path) == []


def _answers(*answers: str):
    remaining = list(answers)
    return lambda _prompt: remaining.pop(0)


def test_the_default_answer_sorts_only_what_the_template_recommends(tmp_path: Path):
    by_template = _flagged(tmp_path, "submission-0001", [1, 3, 2], by_template=True)
    by_number = _flagged(tmp_path, "submission-0002", [2, 1, 3])
    lines: list[str] = []

    assert review_in_terminal(tmp_path, ask=_answers(""), write=lines.append)

    assert _footers(by_template) == ["Page 1", "Page 2", "Page 3"]
    assert _footers(by_number) == ["Page 2", "Page 1", "Page 3"]
    assert [finding.status for finding in read_order_review(tmp_path)[0]] == [SORTED, KEPT]
    assert "sort recommended" in "\n".join(lines)


def test_without_a_recommendation_the_default_answer_moves_nothing(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2])

    assert review_in_terminal(tmp_path, ask=_answers(""), write=lambda _line: None)

    assert _footers(pdf) == ["Page 1", "Page 3", "Page 2"]
    assert read_order_review(tmp_path)[0][0].status == KEPT


def test_choosing_sorts_the_picked_submissions_and_keeps_the_rest(tmp_path: Path):
    first = _flagged(tmp_path, "submission-0001", [1, 3, 2])
    second = _flagged(tmp_path, "submission-0002", [2, 1, 3])

    assert review_in_terminal(tmp_path, ask=_answers("x", "c", "2"), write=lambda _line: None)

    assert _footers(first) == ["Page 1", "Page 3", "Page 2"]
    assert _footers(second) == ["Page 1", "Page 2", "Page 3"]


def test_stopping_leaves_everything_pending(tmp_path: Path):
    pdf = _flagged(tmp_path, "submission-0001", [1, 3, 2], by_template=True)

    def closed(_prompt: str) -> str:
        raise EOFError

    assert not review_in_terminal(tmp_path, ask=_answers("q"), write=lambda _line: None)
    assert not review_in_terminal(tmp_path, ask=closed, write=lambda _line: None)

    assert _footers(pdf) == ["Page 1", "Page 3", "Page 2"]
    assert len(pending_findings(tmp_path)) == 1

import json
from pathlib import Path

import fitz

from opengrader.core.pages.split import SIDECAR_SUFFIX
from opengrader.core.submissions.scan_notes import SCAN_NOTES_DIRNAME, note_path
from opengrader.core.pages.split_review import (
    APPLIED,
    FIXED,
    KEPT,
    PENDING,
    SplitPage,
    SplitPart,
    apply_split_fixes,
    find_split_problems,
    keep_split,
    pending_split_findings,
    read_part,
    read_split_review,
    record_split_review,
    review_path,
    review_split_in_terminal,
)
from opengrader.core.submissions.extra_pages import read_extra_pages_map, write_extra_pages_map
from opengrader.core.submissions.splitter import read_scan_warnings

SIX = [1, 2, 3, 4, 5, 6]


def _part(name: str, numbers: list[int | None], *, extra: int = 0) -> SplitPart:
    pages = [SplitPage(number) for number in numbers]
    pages += [SplitPage(None, matched=False)] * extra
    return SplitPart(name, tuple(pages))


def _split_pdf(directory: Path, name: str, labels: list[str], numbers: list[int | None]) -> Path:
    """A split PDF whose pages say who they belong to, with the splitter's sidecar."""
    directory.mkdir(parents=True, exist_ok=True)
    pdf = directory / f"{name}.pdf"
    doc = fitz.open()
    for label in labels:
        doc.new_page(width=612, height=792).insert_text((72, 72), label)
    doc.save(str(pdf))
    doc.close()
    pdf.with_suffix(SIDECAR_SUFFIX).write_text(json.dumps({
        "pages": [{"group": f"g{n}" if n else None, "page_number": n} for n in numbers],
    }))
    return pdf


def _labels(pdf: Path) -> list[str]:
    with fitz.open(str(pdf)) as doc:
        return [page.get_text().strip() for page in doc]


def _five_plus_one(directory: Path) -> None:
    """The tester's scan: the first student's page 6 was taken for a new submission."""
    _split_pdf(directory, "submission-0001", [f"A{n}" for n in range(1, 6)], [1, 2, 3, 4, 5])
    _split_pdf(directory, "submission-0002", ["A6"], [6])
    _split_pdf(directory, "submission-0003", [f"B{n}" for n in range(1, 7)], SIX)
    parts = [read_part(directory, name) for name in ("submission-0001", "submission-0002", "submission-0003")]
    record_split_review(directory, parts, 6, SIX)


# ---------------------------------------------------------------- detection


def test_five_plus_one_is_one_finding_with_a_merge_proposed():
    parts = [_part("s1", [1, 2, 3, 4, 5]), _part("s2", [6]), _part("s3", SIX)]

    [finding] = find_split_problems(parts, 6, SIX)

    assert finding.names == ("s1", "s2")
    assert finding.counts == (5, 1)
    assert finding.proposal == (6,)
    assert "s1: 5 page(s) where 6 are expected" in finding.problems
    assert finding.describe_fix() == "Merge s1 and s2 (5 + 1 pages) into one 6-page submission."


def test_a_boundary_one_page_late_is_cut_again_into_two():
    parts = [_part("s1", [1, 2, 3, 4, 5, 6, 1]), _part("s2", [2, 3, 4, 5, 6])]

    [finding] = find_split_problems(parts, 6, SIX)

    assert finding.proposal == (6, 6)
    assert finding.describe_fix().startswith("Cut s1 and s2 (7 + 5 pages) again into 2 submissions")


def test_a_correct_split_and_scratch_paper_are_not_problems():
    parts = [_part("s1", SIX), _part("s2", SIX, extra=1)]

    assert find_split_problems(parts, 6, SIX) == []


def test_a_short_last_submission_is_reported_with_no_fix():
    parts = [_part("s1", SIX), _part("s2", [1, 2, 3, 4])]

    [finding] = find_split_problems(parts, 6, SIX)

    assert finding.names == ("s2",)
    assert finding.proposal == ()
    assert "printed page(s) 5, 6 missing" in " ".join(finding.problems)


def test_no_fix_when_a_new_piece_would_start_mid_assignment():
    # 5 + 1 adds up, but merged they would begin on page 2: whatever went
    # wrong here, cutting every six pages does not mend it.
    parts = [_part("s1", [2, 3, 4, 5, 6]), _part("s2", [1])]

    findings = find_split_problems(parts, 6, SIX)

    assert [f.proposal for f in findings] == [(), ()]


def test_repeated_and_missing_printed_pages_are_reported_without_a_count_problem():
    [finding] = find_split_problems([_part("s1", [1, 2, 3, 3, 5, 6])], 6, SIX)

    assert finding.problems == (
        "s1: printed page(s) 3 appear more than once",
        "s1: printed page(s) 4 missing",
    )
    assert finding.proposal == ()


def test_a_page_whose_number_was_not_read_may_be_the_missing_one():
    assert find_split_problems([_part("s1", [1, 2, None, 4, 5, 6])], 6, SIX) == []


# ------------------------------------------------------------------- files


def test_nothing_recorded_means_nothing_to_review(tmp_path: Path):
    assert pending_split_findings(tmp_path) == []
    review_path(tmp_path).parent.mkdir(parents=True)
    review_path(tmp_path).write_text("{not json")
    assert pending_split_findings(tmp_path) == []


def test_applying_the_merge_rewrites_the_files_and_removes_the_empty_one(tmp_path: Path):
    _five_plus_one(tmp_path)
    write_extra_pages_map(tmp_path, {"submission-0001": (), "submission-0002": (), "submission-0003": ()})
    [finding] = pending_split_findings(tmp_path)

    assert apply_split_fixes(tmp_path, [finding.key]) == ["submission-0001"]

    first = tmp_path / "submission-0001.pdf"
    assert _labels(first) == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert [p["page_number"] for p in json.loads(note_path(first, SIDECAR_SUFFIX).read_text())["pages"]] == SIX
    assert not first.with_suffix(SIDECAR_SUFFIX).exists()
    assert read_scan_warnings(first) == ()
    for leftover in ("submission-0002.pdf", "submission-0002.scan.json", "submission-0002" + SIDECAR_SUFFIX):
        assert not (tmp_path / leftover).exists()
        assert not (tmp_path / SCAN_NOTES_DIRNAME / leftover).exists()
    assert "submission-0002" not in read_extra_pages_map(tmp_path)
    assert _labels(tmp_path / "submission-0003.pdf")[0] == "B1"
    assert pending_split_findings(tmp_path) == []
    [record] = read_split_review(tmp_path)[0]
    assert record.status == APPLIED


def test_scratch_paper_stays_with_the_page_before_it(tmp_path: Path):
    _split_pdf(tmp_path, "submission-0001", ["A1", "A2", "A3", "A4", "A5"], [1, 2, 3, 4, 5])
    _split_pdf(tmp_path, "submission-0002", ["A6", "scratch"], [6, None])
    _split_pdf(tmp_path, "submission-0003", ["B1", "B2", "B3", "B4", "B5", "B6"], SIX)
    write_extra_pages_map(tmp_path, {"submission-0002": (2,)})
    parts = [read_part(tmp_path, f"submission-000{n}") for n in (1, 2, 3)]
    [finding] = record_split_review(tmp_path, parts, 6, SIX)

    apply_split_fixes(tmp_path, [finding.key])

    assert _labels(tmp_path / "submission-0001.pdf")[-2:] == ["A6", "scratch"]
    assert read_extra_pages_map(tmp_path)["submission-0001"] == (7,)


def test_keeping_the_split_changes_no_file(tmp_path: Path):
    _five_plus_one(tmp_path)
    before = (tmp_path / "submission-0002.pdf").read_bytes()

    assert keep_split(tmp_path, ["submission-0001"]) == ["submission-0001"]

    assert (tmp_path / "submission-0002.pdf").read_bytes() == before
    assert pending_split_findings(tmp_path) == []
    assert read_split_review(tmp_path)[0][0].status == KEPT


def test_a_split_mended_by_hand_is_not_asked_about_again(tmp_path: Path):
    _five_plus_one(tmp_path)
    # The teacher merges the two PDFs in a PDF editor and deletes the second.
    doc = fitz.open(str(tmp_path / "submission-0001.pdf"))
    with fitz.open(str(tmp_path / "submission-0002.pdf")) as last:
        doc.insert_pdf(last)
    doc.save(str(tmp_path / "merged.tmp"))
    doc.close()
    (tmp_path / "merged.tmp").replace(tmp_path / "submission-0001.pdf")
    (tmp_path / "submission-0002.pdf").unlink()

    # No page catalog here, so the six pages are counted, not read: still right.
    assert pending_split_findings(tmp_path) == []
    assert read_split_review(tmp_path)[0][0].status == FIXED


def test_a_hand_edit_that_leaves_it_wrong_is_asked_about_again(tmp_path: Path):
    _five_plus_one(tmp_path)
    keep_split(tmp_path, ["submission-0001"])
    # A page removed from the third submission makes it short too.
    with fitz.open(str(tmp_path / "submission-0003.pdf")) as doc:
        doc.delete_page(5)
        doc.save(str(tmp_path / "short.tmp"))
    (tmp_path / "short.tmp").replace(tmp_path / "submission-0003.pdf")

    pending = pending_split_findings(tmp_path)

    assert [f.names for f in pending] == [("submission-0003",)]
    # The earlier answer stands: those two files did not change.
    assert any(f.status == KEPT for f in read_split_review(tmp_path)[0])


def test_terminal_review_applies_the_proposed_fix_by_default(tmp_path: Path):
    _five_plus_one(tmp_path)
    lines: list[str] = []

    assert review_split_in_terminal(tmp_path, ask=lambda _prompt: "", write=lines.append)

    assert "Fixed the split at: submission-0001" in lines
    assert not (tmp_path / "submission-0002.pdf").exists()


def test_terminal_review_can_stop_without_changing_anything(tmp_path: Path):
    _five_plus_one(tmp_path)
    lines: list[str] = []

    assert not review_split_in_terminal(tmp_path, ask=lambda _prompt: "q", write=lines.append)

    assert (tmp_path / "submission-0002.pdf").exists()
    assert [f.status for f in pending_split_findings(tmp_path)] == [PENDING]

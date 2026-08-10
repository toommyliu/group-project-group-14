"""The ``region-map`` CLI, and the end-to-end claim the whole feature makes.

The last test in this file is the one that earns the feature: a blank "assignment"
with **no text layer** — the file every other structural mode refuses — a
hand-written region map for it, a fake filled-in scan, and a real extraction
that produces one question per mapped region with a crop whose geometry is
exactly the mapped box.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import fitz
import pytest
from PIL import Image, ImageDraw

from opengrader.cli.region_map import run_cli
from opengrader.core.region_map import (
    MappedQuestion,
    RegionBox,
    blank_region_map,
    load_region_map,
    save_region_map,
)


def _image_only_pdf(path: Path, pages: int = 2, marker: str = "ASSIGNMENT") -> Path:
    """A PDF whose pages are pictures — no text layer to read anything off."""
    doc = fitz.open()
    for index in range(pages):
        canvas = Image.new("RGB", (850, 1100), "white")
        draw = ImageDraw.Draw(canvas)
        draw.text((60, 70), f"{marker} page {index + 1}", fill="black")
        draw.rectangle([60, 300, 780, 520], outline="black")
        raster = path.parent / f"{path.stem}-{index}.png"
        canvas.save(raster)
        page = doc.new_page(width=612, height=792)
        page.insert_image(page.rect, filename=str(raster))
    doc.save(str(path))
    doc.close()
    return path


def _write_map(assignment: Path, destination: Path | None = None) -> Path:
    region_map = replace(
        blank_region_map(assignment, assignment_title="Paper Midterm"),
        name_field=RegionBox(1, (0.55, 0.03, 0.40, 0.06)),
        student_id_field=RegionBox(1, (0.08, 0.03, 0.40, 0.06)),
        questions=(
            MappedQuestion("q1", 1, 1, (0.08, 0.30, 0.84, 0.20), possible_points=10.0),
            MappedQuestion("q2", 2, 2, (0.08, 0.55, 0.84, 0.25), possible_points=5.0),
        ),
    )
    return save_region_map(region_map, destination or (assignment.parent / "midterm.regions.json"))


@pytest.fixture()
def assignment(tmp_path: Path) -> Path:
    return _image_only_pdf(tmp_path / "midterm.pdf")


# ── propose ────────────────────────────────────────────────────────────────


def test_propose_grid_writes_a_map_and_reports_it(assignment: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "grid.regions.json"
    run_cli(["propose", "--input", str(assignment), "--output", str(output), "--grid", "3"])

    assert output.exists()
    region_map = load_region_map(output)
    assert len(region_map.questions) == 6

    out = capsys.readouterr().out
    assert "page 1: q1, q2, q3" in out
    assert "region-map preview" in out  # tells you the exact next command


def test_propose_blank_writes_an_empty_map(assignment: Path, tmp_path: Path) -> None:
    output = tmp_path / "empty.regions.json"
    run_cli(["propose", "--input", str(assignment), "--output", str(output), "--blank"])
    assert load_region_map(output).questions == ()


def test_propose_defaults_the_output_next_to_the_assignment(assignment: Path) -> None:
    run_cli(["propose", "--input", str(assignment), "--grid", "1"])
    assert (assignment.parent / "midterm.regions.json").exists()


def test_propose_refuses_to_clobber_hand_corrected_boxes(
    assignment: Path, tmp_path: Path, capsys
) -> None:
    output = tmp_path / "m.regions.json"
    run_cli(["propose", "--input", str(assignment), "--output", str(output), "--grid", "1"])

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["propose", "--input", str(assignment), "--output", str(output), "--grid", "9"])

    assert exit_info.value.code == 1
    assert "--force" in capsys.readouterr().err
    assert len(load_region_map(output).questions) == 2  # untouched


def test_propose_force_replaces_the_map(assignment: Path, tmp_path: Path) -> None:
    output = tmp_path / "m.regions.json"
    run_cli(["propose", "--input", str(assignment), "--output", str(output), "--grid", "1"])
    run_cli(
        ["propose", "--input", str(assignment), "--output", str(output), "--grid", "3", "--force"]
    )
    assert len(load_region_map(output).questions) == 6


def test_propose_refuses_a_missing_assignment(tmp_path: Path, capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            [
                "propose",
                "--input",
                str(tmp_path / "nope.pdf"),
                "--output",
                str(tmp_path / "m.json"),
            ]
        )
    assert exit_info.value.code == 1
    assert "not found" in capsys.readouterr().err


# ── validate ───────────────────────────────────────────────────────────────


def test_validate_passes_a_good_map(assignment: Path, capsys) -> None:
    run_cli(["validate", "--map", str(_write_map(assignment)), "--input", str(assignment)])
    assert "No problems found" in capsys.readouterr().out


def test_validate_exits_two_on_an_error_level_finding(assignment: Path, tmp_path: Path) -> None:
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    data["questions"][1]["id"] = "q1"  # duplicate id — overwrites the other crop
    path.write_text(json.dumps(data))

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["validate", "--map", str(path)])
    assert exit_info.value.code == 2


def test_validate_does_not_fail_on_warnings_alone(assignment: Path, tmp_path: Path) -> None:
    """An unmapped extra-credit question is ordinary, not a failure."""
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    data["fields"]["name"] = None  # a warning-level finding
    path.write_text(json.dumps(data))

    run_cli(["validate", "--map", str(path)])  # no SystemExit


def test_validate_refuses_a_map_for_a_different_assignment(assignment: Path, tmp_path: Path, capsys) -> None:
    other = _image_only_pdf(tmp_path / "final.pdf", marker="OTHER")
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["validate", "--map", str(_write_map(assignment)), "--input", str(other)])

    assert exit_info.value.code == 2
    assert "final.pdf" in capsys.readouterr().out


def test_validate_json_lists_findings_machine_readably(assignment: Path, tmp_path: Path, capsys) -> None:
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    data["questions"][0]["bbox"] = [0.5, 0.5, 0.0, 0.0]
    path.write_text(json.dumps(data))

    with pytest.raises(SystemExit):
        run_cli(["validate", "--map", str(path), "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert any(finding["kind"] == "empty_region" for finding in payload["findings"])
    assert all("severity" in finding for finding in payload["findings"])


def test_validate_refuses_an_unreadable_map(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{oops")
    with pytest.raises(SystemExit) as exit_info:
        run_cli(["validate", "--map", str(broken)])
    assert exit_info.value.code == 1
    assert "not valid JSON" in capsys.readouterr().err


# ── preview ────────────────────────────────────────────────────────────────


def test_preview_writes_a_pdf(assignment: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "preview.pdf"
    run_cli(
        [
            "preview",
            "--map",
            str(_write_map(assignment)),
            "--input",
            str(assignment),
            "--output",
            str(output),
            "--dpi",
            "72",
        ]
    )

    assert output.exists() and output.stat().st_size > 0
    with fitz.open(str(output)) as doc:
        assert len(doc) == 2
    assert "Wrote" in capsys.readouterr().out


def test_preview_writes_pngs_for_a_png_output(assignment: Path, tmp_path: Path) -> None:
    run_cli(
        [
            "preview",
            "--map",
            str(_write_map(assignment)),
            "--input",
            str(assignment),
            "--output",
            str(tmp_path / "look.png"),
            "--dpi",
            "72",
        ]
    )
    assert (tmp_path / "look-page-1.png").exists()
    assert (tmp_path / "look-page-2.png").exists()


def test_preview_refuses_the_wrong_assignment(assignment: Path, tmp_path: Path, capsys) -> None:
    other = _image_only_pdf(tmp_path / "final.pdf", marker="OTHER")
    with pytest.raises(SystemExit) as exit_info:
        run_cli(
            [
                "preview",
                "--map",
                str(_write_map(assignment)),
                "--input",
                str(other),
                "--output",
                str(tmp_path / "p.pdf"),
            ]
        )
    assert exit_info.value.code == 1
    assert "not the file this region map was drawn on" in capsys.readouterr().err


# ── from-qa ────────────────────────────────────────────────────────────────


def test_from_qa_seeds_points_in_place(assignment: Path, tmp_path: Path, capsys) -> None:
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    for question in data["questions"]:
        question["points"] = None
    path.write_text(json.dumps(data))

    qa_file = tmp_path / "qa.md"
    qa_file.write_text(
        "\n".join(
            [
                "- (12 points) What is a semaphore?",
                "  - **Correct**",
                "    - a counter",
                "",
                "- (4 points) And a mutex?",
                "  - **Correct**",
                "    - a lock",
            ]
        )
    )

    run_cli(["from-qa", "--map", str(path), "--qa-file", str(qa_file)])

    seeded = load_region_map(path)
    assert [question.possible_points for question in seeded.questions] == [12.0, 4.0]
    assert "Seeded 2 question(s)" in capsys.readouterr().out


def test_from_qa_can_write_elsewhere(assignment: Path, tmp_path: Path) -> None:
    path = _write_map(assignment)
    qa_file = tmp_path / "qa.md"
    qa_file.write_text("- (7 points) One\n  - **Correct**\n    - x\n")
    output = tmp_path / "seeded.json"

    run_cli(["from-qa", "--map", str(path), "--qa-file", str(qa_file), "--output", str(output)])

    assert output.exists()
    assert load_region_map(output).questions[0].possible_points == 7.0


# ── the end-to-end claim ───────────────────────────────────────────────────


def test_an_assignment_with_no_text_layer_extracts_against_its_region_map(
    tmp_path: Path, monkeypatch
) -> None:
    """The promise of the whole feature, asserted directly.

    A blank assignment that ``--blank-template`` would refuse outright (no text layer),
    a map drawn on it by hand, and a scan that is only pixels. The result must
    hold one question per mapped region, and each crop must be exactly the
    rectangle that was mapped — because "the box you drew is the box you get" is
    the only thing that makes drawing them worth doing.
    """
    import opengrader.cli.submission_extractor as extractor_cli

    monkeypatch.setattr(extractor_cli, "ensure_model_available", lambda _c: None)

    assignment = _image_only_pdf(tmp_path / "paper-assignment.pdf", pages=2, marker="BLANK")
    scans = tmp_path / "scans"
    scans.mkdir()
    _image_only_pdf(scans / "submission-0001.pdf", pages=2, marker="STUDENT")

    map_path = _write_map(assignment, tmp_path / "paper-assignment.regions.json")
    output = tmp_path / "out"

    extractor_cli.run_cli(
        [
            "--input",
            str(scans),
            "--output",
            str(output),
            "--region-map",
            str(map_path),
            "--no-ollama-qa",
            "--dpi",
            "150",
        ]
    )

    markdown = output / "submission-0001" / "submission-0001.submission.md"
    assert markdown.exists()

    from opengrader.core.submissions.student_file import load_student_submission_markdown

    document = load_student_submission_markdown(markdown)
    assert document.assignment_title == "Paper Midterm"
    assert [question.question_id for question in document.questions] == ["q1", "q2"]

    expected = {
        "q1": (1, (0.08, 0.30, 0.84, 0.20)),
        "q2": (2, (0.08, 0.55, 0.84, 0.25)),
    }
    # A US-Letter page (612x792pt) at 150 DPI.
    page_w, page_h = 1275, 1650
    for question in document.questions:
        page, bbox = expected[question.question_id]

        # The recorded provenance is the mapped box, unmoved.
        assert len(question.source_segments) == 1
        assert question.source_segments[0].page_number == page
        assert question.source_segments[0].bbox_norm == bbox

        # And so are the pixels on disk.
        crop_path = markdown.parent / question.response_image_path
        assert crop_path.exists()
        with Image.open(crop_path) as crop:
            assert crop.size == (
                round(bbox[2] * page_w),
                round(bbox[3] * page_h),
            )

    # The identity fields were cropped too, so this submission is not doomed to
    # the needs-review queue for want of a name.
    assert (markdown.parent / "identifiers" / "name.png").exists()
    assert (markdown.parent / "identifiers" / "id.png").exists()

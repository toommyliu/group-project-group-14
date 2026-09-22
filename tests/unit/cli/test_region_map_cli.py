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


def test_propose_grid_writes_a_map_and_reports_it(assignment: Path, tmp_path: Path, capsys) -> None:
    output = tmp_path / "grid.regions.json"
    run_cli(["propose", "--input", str(assignment), "--output", str(output), "--grid", "3"])

    assert output.exists()
    region_map = load_region_map(output)
    assert len(region_map.questions) == 6

    out = capsys.readouterr().out
    assert "page 1: q1, q2, q3" in out
    assert "region-map preview" in out


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
    assert len(load_region_map(output).questions) == 2


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


def test_validate_passes_a_good_map(assignment: Path, capsys) -> None:
    run_cli(["validate", "--map", str(_write_map(assignment)), "--input", str(assignment)])
    assert "No problems found" in capsys.readouterr().out


def test_validate_exits_two_on_an_error_level_finding(assignment: Path, tmp_path: Path) -> None:
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    data["questions"][1]["id"] = "q1"
    path.write_text(json.dumps(data))

    with pytest.raises(SystemExit) as exit_info:
        run_cli(["validate", "--map", str(path)])
    assert exit_info.value.code == 2


def test_validate_does_not_fail_on_warnings_alone(assignment: Path, tmp_path: Path) -> None:
    path = _write_map(assignment)
    data = json.loads(path.read_text())
    data["fields"]["name"] = None
    path.write_text(json.dumps(data))

    run_cli(["validate", "--map", str(path)])


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


def test_an_assignment_with_no_text_layer_extracts_against_its_region_map(
    tmp_path: Path, monkeypatch
) -> None:
    import opengrader.cli.submission_extractor as extractor_cli
    from opengrader.core import app_config

    monkeypatch.setattr(app_config, "ensure_ready", lambda *_a, **_k: None)

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
    page_w, page_h = 1275, 1650
    for question in document.questions:
        page, bbox = expected[question.question_id]

        assert len(question.source_segments) == 1
        assert question.source_segments[0].page_number == page
        assert question.source_segments[0].bbox_norm == bbox

        crop_path = markdown.parent / question.response_image_path
        assert crop_path.exists()
        with Image.open(crop_path) as crop:
            assert crop.size == (
                round(bbox[2] * page_w),
                round(bbox[3] * page_h),
            )

    assert (markdown.parent / "identifiers" / "name.png").exists()
    assert (markdown.parent / "identifiers" / "id.png").exists()

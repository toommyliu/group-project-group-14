from __future__ import annotations

import json

import numpy as np
import pytest
from PIL import Image

from opengrader.cli import run_command
from opengrader.cli import photo_import


def _save_plain_photo(path, *, size=(300, 400)) -> None:  # noqa: ANN001
    array = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    array[:6, :] = 0
    array[-6:, :] = 0
    array[:, :6] = 0
    array[:, -6:] = 0
    Image.fromarray(array).save(path)


def test_run_cli_writes_pdfs_and_prints_summary(tmp_path, capsys) -> None:  # noqa: ANN001
    input_dir = tmp_path / "photos"
    input_dir.mkdir()
    _save_plain_photo(input_dir / "s1-p1.jpg")
    _save_plain_photo(input_dir / "s1-p2.jpg")
    output_dir = tmp_path / "out"

    run_command(
        photo_import.command,
        ["--input", str(input_dir), "--output", str(output_dir), "--no-perspective"]
    )

    captured = capsys.readouterr()
    assert "Grouping: filename token" in captured.out
    assert "s1" in captured.out
    assert "submission-extractor" in captured.out
    assert (output_dir / "s1.pdf").exists()


def test_run_cli_json_output_is_valid_json(tmp_path, capsys) -> None:  # noqa: ANN001
    input_dir = tmp_path / "photos"
    input_dir.mkdir()
    _save_plain_photo(input_dir / "photo1.jpg")
    output_dir = tmp_path / "out"

    run_command(
        photo_import.command,
        [
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--no-perspective",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["grouping_mode"] == "ungrouped"
    assert len(payload["submissions"]) == 1


def test_run_cli_pages_per_photo_set(tmp_path, capsys) -> None:  # noqa: ANN001
    input_dir = tmp_path / "photos"
    input_dir.mkdir()
    for name in ("a1.jpg", "a2.jpg", "a3.jpg", "a4.jpg"):
        _save_plain_photo(input_dir / name)
    output_dir = tmp_path / "out"

    run_command(
        photo_import.command,
        [
            "--input",
            str(input_dir),
            "--output",
            str(output_dir),
            "--pages-per-photo-set",
            "2",
            "--no-perspective",
        ]
    )

    pdfs = sorted(output_dir.glob("*.pdf"))
    assert [p.name for p in pdfs] == ["submission-0001.pdf", "submission-0002.pdf"]


def test_run_cli_missing_input_exits_nonzero(tmp_path, capsys) -> None:  # noqa: ANN001
    with pytest.raises(SystemExit) as excinfo:
        run_command(
            photo_import.command,
            ["--input", str(tmp_path / "nope"), "--output", str(tmp_path / "out")]
        )
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "not found" in captured.err


def test_run_cli_heic_without_extra_exits_nonzero(tmp_path, capsys) -> None:  # noqa: ANN001
    import importlib.util

    if importlib.util.find_spec("pillow_heif") is not None:
        pytest.skip("pillow-heif is installed; this checks the absence path")

    input_dir = tmp_path / "photos"
    input_dir.mkdir()
    (input_dir / "page1.heic").write_bytes(b"not a real heic file")

    with pytest.raises(SystemExit) as excinfo:
        run_command(
            photo_import.command,
            ["--input", str(input_dir), "--output", str(tmp_path / "out")]
        )
    assert excinfo.value.code != 0
    captured = capsys.readouterr()
    assert "pillow-heif" in captured.err
    assert "uv sync --extra photos" in captured.err

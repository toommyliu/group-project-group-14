from pathlib import Path

from PIL import Image as PILImage
from textual.geometry import Size

from opengrader.tui.ui.widgets.sharp_image import SharpImage


def _write_image(path: Path, size: tuple[int, int]) -> None:
    PILImage.new("RGB", size, color=(255, 255, 255)).save(path)


def test_sharp_image_uses_native_width_when_container_is_wider(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "small.png"
    _write_image(image_path, (200, 100))

    image = SharpImage(str(image_path))

    assert image.get_content_width(Size(80, 20), Size(80, 20)) == 20


def test_sharp_image_fits_to_container_when_source_is_wider(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "wide.png"
    _write_image(image_path, (1200, 400))

    image = SharpImage(str(image_path))

    assert image.get_content_width(Size(80, 20), Size(80, 20)) == 80


def test_sharp_image_can_reserve_a_fraction_of_container_width(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "preview.png"
    _write_image(image_path, (1200, 400))

    image = SharpImage(str(image_path), fit_width_fraction=0.9)

    assert image.get_content_width(Size(80, 20), Size(80, 20)) == 72


def test_sharp_image_can_limit_width_to_keep_height_bounded(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "chart.png"
    _write_image(image_path, (1200, 400))

    image = SharpImage(str(image_path), max_cell_height=6)
    width = image.get_content_width(Size(80, 20), Size(80, 20))

    assert width < 80
    assert image.get_content_height(Size(80, 20), Size(80, 20), width) <= 6

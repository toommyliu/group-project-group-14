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


def test_tgp_cleanup_sends_spec_correct_delete(monkeypatch) -> None:
    """The library's cleanup sends `a=d,I=<id>`, which spec-following
    terminals ignore because the image was registered under `i=<id>`; the
    stale image then ghosts through leftover placeholder cells. Our subclass
    must send `a=d,d=I,i=<id>` with responses suppressed (q=2)."""
    from opengrader.tui.ui.widgets import sharp_image as module

    sent: list[dict] = []
    monkeypatch.setattr(
        module._tgp,
        "_send_tgp_message",
        lambda **kwargs: sent.append(kwargs),
    )

    renderable = module._TGPImageWithValidDelete.__new__(
        module._TGPImageWithValidDelete
    )
    renderable.terminal_image_id = 42

    renderable.cleanup()
    assert sent == [{"a": "d", "d": "I", "i": 42, "q": 2}]
    assert renderable.terminal_image_id is None

    # Idempotent: a second cleanup must not send another delete.
    renderable.cleanup()
    assert len(sent) == 1


def test_sharp_image_cleans_up_renderable_on_unmount(tmp_path: Path) -> None:
    import asyncio

    from textual.app import App, ComposeResult

    image_path = tmp_path / "img.png"
    _write_image(image_path, (40, 20))

    class CleanupSpy:
        def __init__(self) -> None:
            self.calls = 0

        def cleanup(self) -> None:
            self.calls += 1

    class ImageApp(App):
        def compose(self) -> ComposeResult:
            yield SharpImage(str(image_path), id="img")

    async def run() -> None:
        app = ImageApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            widget = app.query_one("#img", SharpImage)
            spy = CleanupSpy()
            widget._renderable = spy
            await widget.remove()
            await pilot.pause()
            assert spy.calls == 1
            assert widget._renderable is None

    asyncio.run(run())

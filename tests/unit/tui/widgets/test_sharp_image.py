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


def test_renderable_is_chosen_from_the_detected_protocol(tmp_path: Path) -> None:
    from opengrader.tui.terminal_graphics import GraphicsCapability
    from opengrader.tui.ui.widgets import sharp_image as module

    image_path = tmp_path / "img.png"
    _write_image(image_path, (40, 20))

    expected = {
        "kitty": module._TGPImageWithValidDelete,
        "halfcell": module.HalfcellRenderable,
        "unicode": module.UnicodeRenderable,
    }
    for protocol, renderable in expected.items():
        widget = SharpImage(
            str(image_path),
            capability=GraphicsCapability(protocol, source="test"),
        )
        assert widget._Renderable is renderable
        assert not widget.uses_inline_graphics


def test_inline_protocols_compose_a_graphics_pane_instead_of_rendering(
    tmp_path: Path,
) -> None:
    from opengrader.tui.terminal_graphics import GraphicsCapability
    from opengrader.tui.ui.widgets.inline_graphics import InlineGraphicsPane

    image_path = tmp_path / "img.png"
    _write_image(image_path, (40, 20))

    for protocol in ("sixel", "iterm2"):
        widget = SharpImage(
            str(image_path),
            capability=GraphicsCapability(protocol, source="test"),
        )
        assert widget.uses_inline_graphics
        panes = list(widget.compose())
        assert len(panes) == 1
        assert isinstance(panes[0], InlineGraphicsPane)
        assert widget.render() == ""


def test_inline_pane_reuses_the_widgets_scaling_pipeline(tmp_path: Path) -> None:
    from opengrader.tui.terminal_graphics import GraphicsCapability

    image_path = tmp_path / "img.png"
    _write_image(image_path, (400, 200))

    widget = SharpImage(
        str(image_path),
        capability=GraphicsCapability("sixel", source="test"),
    )
    pane = next(iter(widget.compose()))

    prepared = pane._prepare(120, 60)
    assert prepared is not None
    assert prepared.size == (120, 60)
    assert pane._prepare(120, 60) is prepared


def test_inline_pane_tolerates_a_widget_with_no_image() -> None:
    from opengrader.tui.terminal_graphics import GraphicsCapability

    widget = SharpImage(
        None, capability=GraphicsCapability("iterm2", source="test")
    )
    pane = next(iter(widget.compose()))

    assert pane._prepare(10, 10) is None

from __future__ import annotations

import asyncio
from pathlib import Path

import fitz
from textual.app import App, ComposeResult
from textual.widgets import Static
from opengrader.tui.ui.widgets.sharp_image import SharpImage

from opengrader.tui.ui.widgets.template_preview import TemplatePreview


def _create_dummy_pdf(path: Path, pages: int = 3) -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page()
        page.insert_text((50, 50), f"Page {index + 1}")
    doc.save(path)
    doc.close()


def test_template_preview_reuses_the_same_image_widget_when_paging(
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "template.pdf"
    _create_dummy_pdf(pdf_path, pages=3)

    class PreviewApp(App):
        def compose(self) -> ComposeResult:
            yield TemplatePreview(pdf_path)

    async def run() -> None:
        app = PreviewApp()

        async with app.run_test() as pilot:
            await pilot.pause()

            preview = app.query_one(TemplatePreview)
            image = preview.query_one(SharpImage)
            header = preview.query_one(Static)

            assert "Page 1 of 3" in str(header.content)

            preview.next_page()
            await pilot.pause()

            assert preview.query_one(SharpImage) is image
            assert "Page 2 of 3" in str(header.content)

            preview.prev_page()
            await pilot.pause()

            assert preview.query_one(SharpImage) is image
            assert "Page 1 of 3" in str(header.content)

    asyncio.run(run())

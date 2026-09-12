import asyncio

from textual.app import App, ComposeResult

from opengrader.tui.ui.widgets.shortcut_input import ShortcutInput


class ShortcutInputApp(App[None]):
    def compose(self) -> ComposeResult:
        yield ShortcutInput(value="alpha beta gamma", id="input")

    def on_mount(self) -> None:
        input_widget = self.query_one("#input", ShortcutInput)
        input_widget.focus()
        input_widget.cursor_position = len(input_widget.value)


def test_alt_b_moves_to_previous_word_start() -> None:
    async def run() -> None:
        app = ShortcutInputApp()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", ShortcutInput)

            await pilot.press("alt+b")
            await pilot.pause()

            assert input_widget.cursor_position == 11

    asyncio.run(run())


def test_alt_f_moves_to_next_word_boundary() -> None:
    async def run() -> None:
        app = ShortcutInputApp()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", ShortcutInput)
            input_widget.cursor_position = 6

            await pilot.press("alt+f")
            await pilot.pause()

            assert input_widget.cursor_position == 11

    asyncio.run(run())


def test_alt_d_deletes_right_to_next_word_boundary() -> None:
    async def run() -> None:
        app = ShortcutInputApp()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", ShortcutInput)
            input_widget.cursor_position = 6

            await pilot.press("alt+d")
            await pilot.pause()

            assert input_widget.value == "alpha gamma"
            assert input_widget.cursor_position == 6

    asyncio.run(run())


def test_existing_textual_input_shortcuts_are_preserved() -> None:
    async def run() -> None:
        app = ShortcutInputApp()
        async with app.run_test() as pilot:
            input_widget = app.query_one("#input", ShortcutInput)

            await pilot.press("ctrl+w")
            await pilot.pause()
            assert input_widget.value == "alpha beta "
            assert input_widget.cursor_position == len("alpha beta ")

            await pilot.press("ctrl+u")
            await pilot.pause()
            assert input_widget.value == ""
            assert input_widget.cursor_position == 0

            input_widget.value = "alpha beta gamma"
            input_widget.cursor_position = 6
            await pilot.press("ctrl+k")
            await pilot.pause()
            assert input_widget.value == "alpha "
            assert input_widget.cursor_position == 6

    asyncio.run(run())

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static

from opengrader.tui.ui.widgets.breadcrumb import Breadcrumb
from opengrader.tui.ui.widgets.nav_item import NavItem


class _QAListItem(ListItem):
    def __init__(self, nav: NavItem) -> None:
        self.nav = nav
        meta = f" [{nav.meta}]" if nav.meta else ""
        super().__init__(Label(f"{nav.label}{meta}"))

    def on_click(self, event: events.Click) -> None:
        self.post_message(QAPickerScreen.QASelected(self.nav))
        event.stop()


class QAPickerScreen(Vertical):
    class QAHighlighted(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    class QASelected(Message):
        def __init__(self, item: NavItem) -> None:
            self.item = item
            super().__init__()

    def __init__(self, *, id: str | None = None) -> None:
        self._items: list[NavItem] = []
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        yield Breadcrumb(id="qa-breadcrumb")
        yield Label("Choose a question & answer to grade", id="qa-picker-title")
        yield Label(
            "Grade one QA across every student, Gradescope-style.",
            id="qa-picker-subtitle",
        )
        with Horizontal(id="qa-picker-workspace"):
            yield ListView(id="qa-list")
            with Vertical(id="qa-details"):
                yield Label("Question", classes="sidebar-section-title")
                yield Static("Highlight a QA to preview it.", id="qa-preview")
                yield Static(
                    "j/k or up/down  Move\n"
                    "click or enter  Grade selected QA\n"
                    "escape          Back to assignments",
                    id="qa-keybinding-ref",
                )
        yield Static("", id="qa-status")

    def bind_assignment(
        self,
        questions: list[NavItem],
        *,
        breadcrumb_labels: list[str],
        assignment_name: str,
    ) -> None:
        self._items = list(questions)
        self.query_one("#qa-breadcrumb", Breadcrumb).set_path(breadcrumb_labels)
        self.query_one("#qa-picker-title", Label).update(
            f"Choose QA for {assignment_name}"
        )

        qa_list = self.query_one("#qa-list", ListView)
        qa_list.clear()
        for question in questions:
            qa_list.append(_QAListItem(question))
        if questions:
            qa_list.index = 0
            self.show_question_details(questions[0])
        else:
            self.query_one("#qa-preview", Static).update(
                "No questions were found for this assignment."
            )

    def focus_questions(self) -> None:
        qa_list = self.query_one("#qa-list", ListView)
        if qa_list.index is None and self._items:
            qa_list.index = 0
        qa_list.focus()

    def selected_question(self) -> NavItem | None:
        qa_list = self.query_one("#qa-list", ListView)
        child = qa_list.highlighted_child
        if isinstance(child, _QAListItem):
            return child.nav
        if qa_list.index is not None and 0 <= qa_list.index < len(self._items):
            return self._items[qa_list.index]
        return None

    def show_question_details(self, item: NavItem) -> None:
        status = f"\nStatus: {item.meta}" if item.meta else ""
        self.query_one("#qa-preview", Static).update(
            f"{item.label}\n\nSelect this QA to grade it for all students.{status}"
        )

    # Moves the UI selection in the QA list to the given index and updates the preview panel.
    def move_to_index(self, index: int) -> None:

        # Locate the QA list UI component by its selector and expected type
        qa_list = self.query_one("#qa-list", ListView)

        # Validate the provided index is within the current list bounds
        if 0 <= index < len(qa_list._nodes):
            qa_list.index = index

            # This ensures the preview panel on the right updates too
            item = self.selected_question()
            if item:
                self.show_question_details(item)


    def set_status(self, text: str) -> None:
        self.query_one("#qa-status", Static).update(text)

    def move_cursor(self, direction: int) -> None:
        qa_list = self.query_one("#qa-list", ListView)
        if direction < 0:
            qa_list.action_cursor_up()
        else:
            qa_list.action_cursor_down()
        item = self.selected_question()
        if item is not None:
            self.show_question_details(item)

    def confirm(self) -> None:
        item = self.selected_question()
        if item is not None:
            self.post_message(self.QASelected(item))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if not isinstance(event.item, _QAListItem):
            return
        self.show_question_details(event.item.nav)
        self.post_message(self.QAHighlighted(event.item.nav))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, _QAListItem):
            self.post_message(self.QASelected(event.item.nav))

    def on_click(self, event: events.Click) -> None:
        item = self._clicked_qa_item(event.widget)
        if item is None:
            return
        self.post_message(self.QASelected(item.nav))
        event.stop()

    def on_key(self, event: events.Key) -> None:
        if event.key in ("j", "down"):
            self.move_cursor(1)
            event.stop()
            return
        if event.key in ("k", "up"):
            self.move_cursor(-1)
            event.stop()
            return
        if event.key == "enter":
            self.confirm()
            event.stop()

    @staticmethod
    def _clicked_qa_item(widget: Widget | None) -> _QAListItem | None:
        current = widget
        while current is not None:
            if isinstance(current, _QAListItem):
                return current
            current = current.parent
        return None

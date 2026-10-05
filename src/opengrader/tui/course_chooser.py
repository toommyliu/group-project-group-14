from pathlib import Path
from typing import get_args

from rich.table import Table
from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from opengrader.core.recents import short_path
from opengrader.core.workspace.models import Term
from opengrader.core.workspace.store import DATABASE
from opengrader.server.registry import RegisteredWorkspace

TERMS: tuple[Term, ...] = get_args(Term)


def available(course: RegisteredWorkspace) -> bool:
    return (Path(course.path) / DATABASE).is_file()


def term_label(course: RegisteredWorkspace) -> str:
    return " ".join(
        str(part) for part in (course.term and course.term.title(), course.year) if part
    ) or "No term"


def by_term(
    courses: list[RegisteredWorkspace],
) -> list[tuple[str, list[RegisteredWorkspace]]]:
    groups: dict[str, list[RegisteredWorkspace]] = {}
    for course in courses:
        groups.setdefault(term_label(course), []).append(course)

    def rank(course: RegisteredWorkspace) -> int:
        return (course.year or 0) * 10 + (TERMS.index(course.term) + 1 if course.term else 0)

    return sorted(groups.items(), key=lambda group: rank(group[1][0]), reverse=True)


def matches(course: RegisteredWorkspace, query: str) -> bool:
    text = f"{course.number} {course.name or ''} {term_label(course)}".casefold()
    return all(word in text for word in query.casefold().split())


class CourseChooser(App[Path | None]):
    TITLE = "Opengrader"
    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "filter", "Filter"),
        Binding("escape", "clear_filter", "Clear filter", show=False),
        Binding("down", "focus_list", show=False),
    ]
    DEFAULT_CSS = """
    #courses-home {
        max-width: 100;
        height: 1fr;
        padding: 1 3;
    }

    #courses-title {
        text-style: bold;
    }

    #courses-hint {
        color: $text-muted;
        margin-bottom: 1;
    }

    #courses, #courses:focus {
        height: 1fr;
        border: none;
        padding: 0;
        background: transparent;
        background-tint: transparent;
    }

    #courses > .option-list--option-disabled {
        color: $text-accent;
        text-style: bold;
    }

    #courses-empty {
        color: $text-muted;
    }

    #filter-bar {
        dock: bottom;
        height: 1;
        background: $footer-background;
    }

    #filter-bar Label {
        width: auto;
        padding: 0 1;
    }

    #filter-slash {
        color: $footer-key-foreground;
        text-style: bold;
    }

    #filter, #filter:focus {
        width: 1fr;
        height: 1;
        border: none;
        padding: 0;
        background: transparent;
        background-tint: transparent;
    }

    #filter-count {
        color: $text-muted;
    }
    """

    def __init__(self, courses: list[RegisteredWorkspace]) -> None:
        super().__init__()
        self.courses = list(courses)

    def compose(self) -> ComposeResult:
        with Vertical(id="courses-home"):
            yield Label("Courses", id="courses-title")
            yield Static("Pick a course to open.", id="courses-hint")
            yield OptionList(id="courses")
            yield Static("No courses match.", id="courses-empty")
        with Horizontal(id="filter-bar"):
            yield Label("/", id="filter-slash")
            yield Input(id="filter", placeholder="number, name, or term")
            yield Label(id="filter-count")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#filter-bar").display = False
        self.show(self.courses)
        self.query_one(OptionList).focus()

    def show(self, courses: list[RegisteredWorkspace], *, filtered: bool = False) -> None:
        width = max(len(course.number) for course in self.courses)
        groups = by_term(courses)
        options: list[Option] = []
        for label, members in groups:
            if options:
                options.append(Option("", disabled=True))
            options.append(Option(label, disabled=True))
            options.extend(
                Option(self._prompt(course, width), id=course.id) for course in members
            )
        listing = self.query_one(OptionList)
        listing.clear_options()
        listing.add_options(options)
        listing.display = bool(courses)
        self.query_one("#courses-empty").display = not courses
        if filtered:
            courses = [course for _, members in groups for course in members]
        start = next((course for course in courses if available(course)), None)
        if start is not None:
            listing.highlighted = listing.get_option_index(start.id)
        self.query_one("#filter-count", Label).update(
            f"{len(courses)} of {len(self.courses)}"
        )

    def filtering(self, active: bool) -> None:
        self.query_one("#filter-bar").display = active
        self.query_one(Footer).display = not active

    def action_filter(self) -> None:
        self.filtering(True)
        self.query_one(Input).focus()

    def action_clear_filter(self) -> None:
        self.query_one(Input).value = ""
        self.filtering(False)
        self.query_one(OptionList).focus()

    def action_focus_list(self) -> None:
        self.query_one(OptionList).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        self.show(
            [course for course in self.courses if matches(course, event.value)],
            filtered=bool(event.value.strip()),
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.value.strip():
            self.query_one(OptionList).focus()
        else:
            self.action_clear_filter()

    @staticmethod
    def _prompt(course: RegisteredWorkspace, width: int) -> Table:
        where = short_path(course.path)
        if not available(course):
            detail = f"Folder unavailable · {where}"
        else:
            detail = where
        missing = "" if available(course) else " strike dim"
        prompt = Table.grid(padding=(0, 3))
        prompt.add_column(min_width=width, no_wrap=True)
        prompt.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
        prompt.add_row(
            Text(course.number, style="bold" + missing),
            Text(course.name or "", style=missing.strip()),
        )
        prompt.add_row("", Text(detail, style="dim"))
        return prompt

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        course = next(course for course in self.courses if course.id == event.option_id)
        if available(course):
            self.exit(Path(course.path))
        else:
            self.notify(
                f"{course} is no longer available ({course.path}).",
                severity="warning",
            )

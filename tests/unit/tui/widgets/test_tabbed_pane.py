from textual.widgets import Static

from opengrader.tui.ui.widgets.tabbed_pane import TabbedPane, TabbedPaneTab


def _make_tab(label: str) -> TabbedPaneTab:
    return TabbedPaneTab(label=label, build=lambda: Static())


def test_border_title_renders_click_actions_for_visible_tabs() -> None:
    pane = TabbedPane("Pane")
    pane.set_tabs([_make_tab("Info"), _make_tab("Preview")])

    border_title = pane._build_border_title()

    assert "@click=pane_activate_tab(0)" in border_title
    assert "@click=pane_activate_tab(1)" in border_title


def test_action_pane_activate_tab_switches_active_tab() -> None:
    pane = TabbedPane("Pane")
    pane.set_tabs([_make_tab("Info"), _make_tab("Preview")])

    pane.action_pane_activate_tab(1)

    assert pane.active_tab_index == 1


def test_action_pane_activate_tab_ignores_invalid_index() -> None:
    pane = TabbedPane("Pane")
    pane.set_tabs([_make_tab("Info"), _make_tab("Preview")])

    pane.action_pane_activate_tab(99)

    assert pane.active_tab_index == 0


def test_title_acts_as_clickable_first_tab_when_first_tab_label_is_hidden() -> None:
    pane = TabbedPane("Assignment HW 2")
    pane.set_tabs([_make_tab(""), _make_tab("Template A"), _make_tab("Template B")])

    pane.action_pane_activate_tab(1)
    border_title = pane._build_border_title()

    assert pane.active_tab_index == 1
    assert "@click=pane_activate_tab(0)" in border_title
    assert border_title.count("[$text-muted] - [/]") == 2

    pane.action_pane_activate_tab(0)

    assert pane.active_tab_index == 0

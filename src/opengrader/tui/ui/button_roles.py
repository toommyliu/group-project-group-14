"""What a button does, as a CSS class, so its colour tells the user.

The classic Tools page has dozens of buttons, and before these roles they were
all the same colour: a user could not tell "this starts the work" from "this
opens another screen" without reading every label. Each role is a class whose
colours live in ``tui.tcss`` (the ``Button.outline`` rules), so a button opts in
with ``classes=`` and no screen carries its own copy of the palette:

- ``LAUNCH`` (green): starts or saves work right here: Launch Grading, Run
  Pipeline, Generate Template.
- ``OPEN`` (blue): opens another screen or dialog: AI setup, Doctor, every Browse.
- ``BROWSER`` (purple): anything to do with the browser app.

A destructive button (Stop server, Remove) keeps ``variant="error"`` and is
drawn red; a secondary one (Preview, Cancel, Close) gets no role and is drawn
grey, so the main action beside it stands out.

**The screens the Tools page opens** (file picker, AI setup, providers, Doctor,
backup, …) look the same: each takes ``OutlineButtons``. There a button without
a role takes one from the emphasis it already has: ``variant="primary"`` or
``"success"`` (a dialog's main action: Save, Run, Continue) is drawn green as
``LAUNCH``. So only a button that opens something (Browse, *Set up a
provider…*) or belongs to the browser app has to name its role. The grading
screen and the reports opened while grading keep Textual's one-line buttons:
they share rows with the answer being graded.

**One look for every button** (``apply_outline_style``): a rounded outline in
the role colour with no fill, three lines tall. Hovered, it fills with the role
colour; pressed, the fill darkens and the label is highlighted. Hover and press
use a half-block border (``inner``) instead of the rounded one: a rounded line
sits in the middle of its character cell, while a fill colours whole cells, so
a fill under the rounded border would stand half a line taller and wider than
the outline it replaces. It is the same in every terminal: the outline is plain
box-drawing characters, which every terminal draws.
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Button

LAUNCH = "role-launch"
OPEN = "role-open"
BROWSER = "role-browser"

OUTLINE = "outline"


_ROLES = (LAUNCH, OPEN, BROWSER)


def apply_outline_style(root: Widget) -> None:
    """Draw every button under ``root`` as an outline button.

    Buttons are composed compact (one line, no border) and become full height
    here, so a screen's buttons cannot differ by which ones remembered to ask.
    A button with no role and a main-action variant becomes ``LAUNCH``
    (module docstring). It only changes how they look, never what they do.
    """
    for button in root.query(Button):
        button.compact = False
        button.add_class(OUTLINE)
        if button.variant in ("primary", "success") and not any(
            button.has_class(role) for role in _ROLES
        ):
            button.add_class(LAUNCH)


class OutlineButtons:
    """Mixin: this screen's (or widget's) buttons are outline buttons.

    Put it first in the bases: ``class DoctorScreen(OutlineButtons, ModalScreen)``.
    Textual calls every ``on_mount`` up the class chain, so the class's own
    ``on_mount`` still runs (first) and needs no ``super()`` call. A widget that
    mounts buttons after its screen opened takes the mixin itself, as
    ``course_builder_screen._AssignmentRow`` does.
    """

    def on_mount(self) -> None:
        assert isinstance(self, Widget)
        apply_outline_style(self)

"""The persistent one-line banner that says where AI calls go.

Every surface that must say a cloud provider is active renders the SAME
sentence — `opengrader.core.app_config.consent.egress_statement(resolved)` —
so no screen invents its own wording (see `consent.py`'s module docstring).
This widget is that rendering: hidden for a local provider, and shown with
the app's warning styling for a cloud one. Student names and student IDs are
never part of what a cloud provider receives, so this banner never needs a
more severe style than that.
"""

from __future__ import annotations

from textual.widgets import Static

from opengrader.core.app_config import ResolvedAI, consent


class AIEgressBanner(Static):
    """A single-sentence banner; call `update_for(resolved)` whenever AI config changes."""

    DEFAULT_CSS = """
    AIEgressBanner {
        width: 100%;
        height: auto;
        margin-bottom: 1;
        padding: 0 1;
        background: $warning-muted;
        color: $text-warning;
        border: round $warning;
        display: none;
    }

    AIEgressBanner.-visible {
        display: block;
    }
    """

    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("markup", False)
        super().__init__(*args, **kwargs)

    def update_for(self, resolved: ResolvedAI) -> None:
        """Show the canonical egress sentence, or hide the banner for a local provider."""
        if resolved.kind == "local":
            self.remove_class("-visible")
            self.update("")
            return
        self.update(consent.egress_statement(resolved))
        self.add_class("-visible")

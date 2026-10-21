"""What every part of the assistant workflow shares: names, the error, the guides."""

from __future__ import annotations

import json
import shlex
import sys
from importlib import resources
from pathlib import Path
from typing import Mapping

from opengrader.core.qa_file import QADocument, load_qa_markdown

# The provider half of the AI tag and of `Read by`: what the review queue and
# the grading app show as where an AI grade or reading came from.
AGENT_PROVIDER = "agent"
DEFAULT_AGENT_MODEL = "assistant"

AGENT_MARKER_FILENAME = "agent.json"
AGENT_FORMAT = 1
INSTRUCTIONS_FILENAME = "AGENTS.md"
# Claude Code reads CLAUDE.md, Codex and most others read AGENTS.md; one
# imports the other so there is a single text.
CLAUDE_FILENAME = "CLAUDE.md"
DEFAULT_CONFIDENCE = 0.8

_GUIDE_PACKAGE = "opengrader.guides"
_NAME_REMOVED = "[name removed]"
_ID_REMOVED = "[id removed]"
_MIN_NAME_PART = 3
_MIN_ID_DIGITS = 4


class AgentError(ValueError):
    """Refused; the message is written for whoever typed the command."""


def self_argv() -> list[str]:
    """`self_command` as an argument list, for a client that starts it itself."""
    return [sys.executable, "-m", "opengrader"]


def self_command() -> str:
    """How an assistant reaches this same install from any folder.

    The desktop app's Python, not whatever `opengrader` is on the PATH (often
    none): the packet's instructions must name a command that works.
    """
    return f"{shlex.quote(sys.executable)} -m opengrader"


def guide_text(name: str) -> str:
    """A guide shipped inside the package (`ai.md`, `local-llm.md`, …)."""
    from opengrader.core.app_config import DEFAULT_TEXT_MODEL, DEFAULT_VISION_MODEL

    text = resources.files(_GUIDE_PACKAGE).joinpath(name).read_text(encoding="utf-8")
    # The model names are written once, in app_config.
    return text.replace("{vision_model}", DEFAULT_VISION_MODEL).replace(
        "{text_model}", DEFAULT_TEXT_MODEL
    )


# What the teacher says to the assistant once it is open in its folder; the
# folder's AGENTS.md carries the rules, this only points at it. Shown by the
# terminal app and the browser alike.
GRADING_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: grade the answers "
    "that are still open, then give me a short summary."
)
KEY_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: do the task it gives "
    "you on qa.md, check it, then tell me what you were unsure of."
)
RUBRIC_PROMPT = (
    "Read AGENTS.md in this folder and follow it exactly: draft partial-credit "
    "rubrics from the answers, then give me a short summary."
)


# The two kinds of folder an assistant works in. A grading packet holds
# (redacted) student work; an answer-key workspace holds none at all.
KIND_GRADING = "grading"
KIND_ANSWER_KEY = "answer-key"

# What a grading packet is for: grading, or only drafting rubrics (no grades).
PURPOSE_GRADING = "grading"
PURPOSE_RUBRICS = "rubrics"


def packet_purpose(packet: Path) -> str:
    return str(read_marker(packet).get("purpose", PURPOSE_GRADING))


def find_packet_root(start: str | Path | None = None, *, kind: str = KIND_GRADING) -> Path:
    here = Path(start or Path.cwd()).resolve()
    for candidate in (here, *here.parents):
        marker = candidate / ".opengrader" / AGENT_MARKER_FILENAME
        if marker.is_file():
            found = str(read_marker(candidate).get("kind", KIND_GRADING))
            if found != kind:
                raise AgentError(
                    f"{candidate} is an {found} folder; this command works in an "
                    f"{kind} one"
                )
            return candidate
    raise AgentError(
        f"{here} is not inside an assistant packet. Make one with "
        "'opengrader agent prepare', then run this from inside it."
    )


def read_marker(packet: Path) -> Mapping[str, object]:
    return json.loads(
        (packet / ".opengrader" / AGENT_MARKER_FILENAME).read_text(encoding="utf-8")
    )


def packet_qa_path(packet: Path) -> Path:
    return packet / str(read_marker(packet).get("qa_file", ""))


def packet_qa(packet: Path) -> QADocument:
    return load_qa_markdown(packet_qa_path(packet))


# A workspace (`opengrader tui`, the browser app) keeps its grades in this
# database, not in `.submission.md` files: an assistant's grades merged into
# files there would never reach it, so its copies are made and merged back
# through the workspace instead (`core/agent/workspace.py`).
WORKSPACE_DATABASE = Path(".opengrader") / "workspace.sqlite3"


def workspace_root(path: str | Path) -> Path | None:
    """The workspace `path` is in (itself or a folder above it), or None.

    A class folder once opened in the browser has the database too, but it
    is still graded from its files (`class_folder.is_workspace`), so the
    class-folder workflow is the right one there.
    """
    from opengrader.core.class_folder import is_workspace

    here = Path(path).expanduser().resolve()
    for candidate in (here, *here.parents):
        if (candidate / WORKSPACE_DATABASE).is_file():
            return candidate if is_workspace(candidate) else None
    return None


def refuse_workspace(path: str | Path) -> None:
    root = workspace_root(path)
    if root is not None:
        raise AgentError(
            f"{root} is a workspace: its grades live in its database, not in "
            ".submission.md files, so a copy made from its files would never "
            "reach it. Make the copy from the workspace instead: "
            f"opengrader agent prepare --workspace {shlex.quote(str(root))} "
            "(or press A on its assignment list)."
        )

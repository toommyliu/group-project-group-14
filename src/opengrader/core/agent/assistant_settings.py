"""Settings that fence an assistant into its packet, where the assistant reads them.

Claude Code reads `.claude/settings.json` from the folder it starts in, and
its rules are the strongest fence a file can put around an assistant short
of the MCP server (where it has no file access at all). Written per packet
because they name the class folder by its absolute path:

* `permissions.blockReadsOutsideWorkingDirectories` — its file tools refuse
  anything outside the packet, in every permission mode;
* `Read`/`Edit` deny rules for the class folder (`//` marks an absolute path
  in these rules) — which also cover `cat`, `head`, `sed` and redirections;
* the OS-level Bash sandbox with the class folder in `filesystem.denyRead`
  (a single `/` is absolute there) — for shell commands the rules above do
  not recognise. It runs on macOS, Linux and WSL2, not native Windows;
* `allow` for `opengrader agent …`, so the assistant is not asked about the
  one command it is meant to run.

Syntax checked against the Claude Code documentation (code.claude.com/docs:
permissions, sandboxing, settings reference) in October 2026. A project file
can only tighten what the teacher's own Claude Code settings allow, and deny
rules apply even before the folder is trusted. Other assistants have no
equivalent file: Codex's sandbox limits writes, not reads.
"""

from __future__ import annotations

import json
from pathlib import Path

__all__ = ["SETTINGS_RELATIVE_PATH", "assistant_settings", "write_assistant_settings"]

SETTINGS_RELATIVE_PATH = Path(".claude") / "settings.json"


def _permission_path(path: Path) -> str:
    # Read/Edit rules: "//abs/path" is absolute, "/x" would be project-relative.
    return "//" + path.as_posix().lstrip("/")


def assistant_settings(*, class_folder: Path, run_as: str) -> dict:
    folders = {class_folder, class_folder.resolve()}
    deny: list[str] = []
    for folder in sorted(folders, key=str):
        pattern = _permission_path(folder) + "/**"
        deny += [f"Read({pattern})", f"Edit({pattern})"]
    return {
        "$schema": "https://json.schemastore.org/claude-code-settings.json",
        "permissions": {
            "blockReadsOutsideWorkingDirectories": True,
            "allow": [f"Bash({run_as} agent *)"],
            "deny": deny,
        },
        "sandbox": {
            "enabled": True,
            "filesystem": {"denyRead": sorted({folder.as_posix() for folder in folders})},
        },
    }


def write_assistant_settings(packet: Path, *, class_folder: Path, run_as: str) -> Path:
    target = packet / SETTINGS_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(assistant_settings(class_folder=class_folder, run_as=run_as), indent=2) + "\n",
        encoding="utf-8",
    )
    return target

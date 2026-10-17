from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:  # pragma: no cover
    from opengrader.core.app_config import ResolvedAI

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


class Group(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return list(self.commands)

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        rows = [
            (name, command.get_short_help_str(limit=sys.maxsize))
            for name, command in self.commands.items()
            if not command.hidden
        ]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def run_command(
    command: click.Command, argv: Sequence[str], *, obj: object = None
) -> Any:
    try:
        with command.make_context(command.name, list(argv), obj=obj) as ctx:
            return command.invoke(ctx)
    except click.exceptions.Exit as exc:
        if exc.exit_code:
            raise SystemExit(exc.exit_code) from None
        return None
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from None


def resolved_ai() -> ResolvedAI:
    meta = click.get_current_context().meta
    if "opengrader.ai" not in meta:
        from opengrader.core import app_config

        meta["opengrader.ai"] = app_config.resolve()
    return meta["opengrader.ai"]


def ai_default(field: str) -> Callable[[], Any]:
    return lambda: getattr(resolved_ai(), field)


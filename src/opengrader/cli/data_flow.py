"""`opengrader data-flow` — printable, handable-to-a-department report.

Renders `app_config.consent.data_flow_report(app_config.resolve())`: for the
CURRENT configuration, exactly which categories of student data would leave
this machine, to which host, whether the cloud kill switch is in force, and
where the egress log lives. It changes nothing and sends nothing — this is a
read of the configuration, not a run.
"""

from __future__ import annotations

import json
import sys
from argparse import ArgumentParser

from opengrader.core import app_config
from opengrader.core import egress


def _build_parser() -> ArgumentParser:
    parser = ArgumentParser(
        description=(
            "Report which categories of student data would leave this machine "
            "under the current AI configuration, and to which host — printable "
            "and handable to a department."
        ),
    )
    parser.add_argument(
        "--json", action="store_true", dest="as_json", help="Machine-readable output."
    )
    return parser


def _kill_switch_lines(resolved: "app_config.ResolvedAI") -> list[str]:
    lines = []
    if resolved.cloud_forbidden or not app_config.consent.cloud_allowed():
        lines.append(
            f"Kill switch: {app_config.consent.ALLOW_CLOUD_ENV}=0 is set on this "
            "machine — cloud AI providers are turned off here, machine-wide."
        )
    else:
        lines.append("Kill switch: none set on this machine.")
    return lines


def run_cli(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    resolved = app_config.resolve()
    report = app_config.data_flow_report(resolved)
    log_path = egress.active_log_path()

    if args.as_json:
        body = report.to_dict()
        body["kill_switch"] = {
            "cloud_forbidden": resolved.cloud_forbidden or not app_config.consent.cloud_allowed(),
        }
        body["egress_log_path"] = str(log_path)
        print(json.dumps(body, indent=2))
        return

    for line in report.lines():
        print(line)
    print()
    for line in _kill_switch_lines(resolved):
        print(line)
    print(f"Egress log: {log_path}")


def main() -> None:
    run_cli()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

"""`python -m opengrader` — the same front door as the `opengrader` command.

Worth the four lines: a checkout that has not been `uv sync`ed has no console
scripts on `PATH`, and a Windows environment where the scripts directory is not
on `PATH` is common enough to be worth an escape hatch. `python -m opengrader
doctor` works in both, and is the form to ask for in a bug report.
"""

from opengrader.cli.main import main

if __name__ == "__main__":
    main()

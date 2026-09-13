"""Print the API contract without reading the instructor's workspace."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from opengrader.server.app import create_app


def main() -> None:
    with TemporaryDirectory(prefix="opengrader-schema-") as home:
        print(json.dumps(create_app(Path(home)).openapi()))


if __name__ == "__main__":
    main()

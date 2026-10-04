"""Print the API contract without reading the instructor's workspace."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from opengrader.server.workspace.app import create_app
from opengrader.server.app import create_app as create_server_app


def main() -> None:
    with TemporaryDirectory(prefix="opengrader-schema-") as home:
        schema = create_app(Path(home) / "workspace").openapi()
        server_schema = create_server_app(Path(home) / "server", process_jobs=False).openapi()
        schema["paths"].update(server_schema["paths"])
        schema["components"]["schemas"].update(server_schema["components"]["schemas"])
        print(json.dumps(schema))


if __name__ == "__main__":
    main()

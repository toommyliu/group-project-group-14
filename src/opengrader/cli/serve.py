"""Launch the localhost grading API and web app."""

from argparse import ArgumentParser
from copy import deepcopy
import locale
import logging.config

from opengrader.server.home import resolve_data_home


def main(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(
        description="Serve assignments in the Opengrader data home on localhost."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Local port, default 8000."
    )
    parser.add_argument(
        "--dev", action="store_true", help="Hot-reload the frontend and restart API and Vite on Python edits."
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    try:
        locale.setlocale(locale.LC_TIME, "")
    except locale.Error:
        # An unavailable shell locale should not prevent the server from starting.
        pass

    log_config = deepcopy(LOGGING_CONFIG)
    for formatter in log_config["formatters"].values():
        formatter["fmt"] = "%(asctime)s " + formatter["fmt"]
        formatter["datefmt"] = "%X"
    log_config["formatters"]["access"]["()"] = "opengrader.server.request_logging.TimedAccessFormatter"
    log_config["loggers"]["opengrader"] = {
        "handlers": ["default"], "level": "INFO", "propagate": False,
    }
    logging.config.dictConfig(log_config)

    if args.dev:
        if args.port == 0:
            parser.error("--dev requires a fixed, nonzero API port")
        from opengrader.server.dev import run_dev

        try:
            code = run_dev(args.port)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"error: {exc}\n")
        parser.exit(code)

    from opengrader.server.app import create_app
    from opengrader.server.request_logging import RequestTiming

    try:
        data_home = resolve_data_home()
        data_home.mkdir(parents=True, exist_ok=True)
        app = create_app(data_home)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")

    logging.getLogger("opengrader.server").info("Server data home: %s", data_home)
    uvicorn.run(
        RequestTiming(app), host="127.0.0.1", port=args.port,
        proxy_headers=False, log_config=log_config,
    )


if __name__ == "__main__":
    main()

"""Launch the read-only localhost grading API and web app."""

from argparse import ArgumentParser

from opengrader.server.home import resolve_data_home


def main(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(
        description="Serve assignments in the Opengrader data home on localhost."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Local port, default 8000."
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")

    import uvicorn

    from opengrader.server.app import create_app

    try:
        data_home = resolve_data_home()
        data_home.mkdir(parents=True, exist_ok=True)
        app = create_app(data_home)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"error: {exc}\n")

    print(f"Server data home: {data_home}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port, proxy_headers=False)


if __name__ == "__main__":
    main()

"""Launch the localhost grading API and web app."""

from argparse import ArgumentParser

from opengrader.server.home import resolve_data_home


def main(argv: list[str] | None = None) -> None:
    parser = ArgumentParser(
        description="Serve assignments in the Opengrader data home on localhost."
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Local port, default 8000."
    )
    parser.add_argument(
        "--dev", action="store_true", help="Start Vite with hot reload and the API."
    )
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    if args.dev:
        if args.port == 0:
            parser.error("--dev requires a fixed, nonzero API port")
        from opengrader.server.dev import run_dev

        try:
            code = run_dev(args.port)
        except (OSError, ValueError) as exc:
            parser.exit(2, f"error: {exc}\n")
        parser.exit(code)

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

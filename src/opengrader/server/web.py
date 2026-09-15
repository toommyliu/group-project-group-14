"""Serve the optional Vite build without exposing source or assignment files."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles


def add_web_routes(app: FastAPI, dist: Path) -> None:
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.get("/assignment", include_in_schema=False, response_model=None)
    @app.get("/scans", include_in_schema=False, response_model=None)
    @app.get("/submissions", include_in_schema=False, response_model=None)
    @app.get("/edit-assignment", include_in_schema=False, response_model=None)
    @app.get("/new-assignment", include_in_schema=False, response_model=None)
    @app.get("/", include_in_schema=False, response_model=None)
    def index() -> FileResponse | HTMLResponse:
        if not (dist / "index.html").is_file():
            return HTMLResponse(
                "<html lang='en'><title>Opengrader</title>"
                "<h1>The web app has not been built</h1>"
                "<p>From the repository, run <code>pnpm --dir "
                "src/opengrader/web install --frozen-lockfile</code>, then "
                "<code>pnpm --dir src/opengrader/web build</code> "
                "and restart the server.</p>"
                "<p>The <a href='/docs'>API documentation</a> is available.</p></html>",
                status_code=503,
            )
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

"""Serve the optional Vite build without exposing source or assignment files."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

# Paths owned by the server; unknown paths under them are errors, not app pages.
RESERVED = ("api", "auth", "assets")


def add_web_routes(app: FastAPI, dist: Path) -> None:
    """Register last so API and auth routes match before the app fallback."""
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="web-assets")

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    def index(path: str) -> FileResponse | HTMLResponse:
        if path.split("/", 1)[0] in RESERVED:
            raise HTTPException(404)
        if not (dist / "index.html").is_file():
            return HTMLResponse(
                "<html lang='en'><title>Opengrader</title>"
                "<h1>The web app has not been built</h1>"
                "<p>From the repository, run <code>npm --prefix "
                "src/opengrader/web ci</code>, then "
                "<code>npm --prefix src/opengrader/web run build</code> "
                "and restart the server.</p>"
                "<p>The <a href='/docs'>API documentation</a> is available.</p></html>",
                status_code=503,
            )
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

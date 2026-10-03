"""Shared JSON transport for local server and workspace clients."""

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import BaseModel

from opengrader.core.workspace.models import Answer


class ServiceError(RuntimeError):
    def __init__(self, status: int, message: str, current: Answer | None = None):
        super().__init__(message)
        self.status = status
        self.current = current


def request(
    url: str,
    token: str,
    method: str,
    path: str,
    body: BaseModel | None = None,
    *,
    timeout: float = 10,
) -> bytes:
    if not path.startswith("/") or path.startswith("//"):
        raise ValueError("Expected a workspace API path.")
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(
        url + path,
        data=body.model_dump_json().encode() if body else None,
        method=method,
        headers=headers,
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        try:
            data = json.loads(exc.read())
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        current = (
            Answer.model_validate(data["current"]) if data.get("current") else None
        )
        raise ServiceError(
            exc.code, str(data.get("detail", "Workspace request failed.")), current
        ) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ServiceError(
            0,
            "Could not reach the Opengrader server. Your pending edits are preserved; reconnect and retry.",
        ) from exc

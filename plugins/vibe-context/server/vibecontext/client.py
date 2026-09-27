"""Calls the backend API with the local token. Used by the CLI and the MCP server."""

import httpx

from vibecontext import runtime
from vibecontext.config import Paths, load_api_token


class ApiError(RuntimeError):
    def __init__(self, status_code: int, detail: str):
        super().__init__(f"{detail} (HTTP {status_code})")
        self.status_code = status_code
        self.detail = detail


def call(paths: Paths, method: str, path: str, timeout: float = 30.0, **kwargs) -> dict | None:
    settings = runtime.ensure_running(paths)
    response = httpx.request(
        method,
        runtime.backend_url(settings) + path,
        headers={"Authorization": f"Bearer {load_api_token(paths)}"},
        timeout=timeout,
        **kwargs,
    )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(response.status_code, str(detail))
    return response.json() if response.content else None

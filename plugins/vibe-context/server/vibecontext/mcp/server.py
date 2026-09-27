"""Stdio MCP server that Claude Code launches from .mcp.json.

Stays light: it holds no models and no index. It makes sure the backend is up,
then forwards each tool call to the backend API with the local token.
"""

import logging
import threading
from typing import Literal

from mcp.server.mcpserver import MCPServer

from vibecontext import client, runtime
from vibecontext.config import Paths

INSTRUCTIONS = (
    "VibeContext indexes documents that live outside the repository: specs, meeting notes, "
    "business PDFs, external docs. The user attaches them to a session or globally from the "
    "VibeContext dashboard. The SessionStart context gives the current VibeContext session id."
)

server = MCPServer(name="vibecontext", instructions=INSTRUCTIONS)
paths = Paths.from_env()
log = logging.getLogger("vibecontext.mcp")


@server.tool()
def list_sessions(status: Literal["open", "stale", "ended", "all"] = "open", limit: int = 20) -> dict:
    """List Claude Code sessions VibeContext has tracked, most recent activity first.

    "stale" means the session never reported its end and has been idle for hours,
    which usually means it crashed or was killed.
    """
    return client.call(paths, "GET", "/api/sessions", params={"status": status, "limit": limit})


def _warm_up() -> None:
    # Start services in the background so the first tool call does not pay for docker startup.
    try:
        runtime.ensure_running(paths)
    except Exception:
        log.exception("Background start failed; the next tool call will retry")


def main() -> None:
    paths.logs.mkdir(parents=True, exist_ok=True)
    # MCPServer configures the root logger first, so basicConfig would be a no-op.
    handler = logging.FileHandler(paths.logs / "mcp.log")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logging.getLogger("vibecontext").addHandler(handler)
    logging.getLogger("vibecontext").setLevel(logging.INFO)
    # httpx logs every request at INFO on stderr, which floods Claude Code's MCP log.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log.info("MCP server starting")
    threading.Thread(target=_warm_up, daemon=True).start()
    server.run("stdio")

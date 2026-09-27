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
    "business PDFs, external docs. The user attaches them to a session or globally. "
    "Call search_context when a task depends on requirements, decisions, business rules or "
    "domain knowledge the code does not explain, before asking the user. The SessionStart "
    "context gives the current VibeContext session id; pass it as session_id."
)

server = MCPServer(name="vibecontext", instructions=INSTRUCTIONS)
paths = Paths.from_env()
log = logging.getLogger("vibecontext.mcp")


@server.tool()
def search_context(query: str, session_id: str | None = None, top_k: int = 5) -> dict:
    """Search the documents the user attached to VibeContext: specs, meeting notes, business
    PDFs and external docs that are not in the repository.

    Use it when the task depends on requirements, decisions, business rules or domain
    knowledge that the code alone does not explain, and before asking the user about them.
    Global documents are always searched; pass the VibeContext session id from the session
    context to include the files attached to this session. Phrase the query as a question or
    keywords, in the language the documents are likely written in.

    Returns up to top_k (1-20) excerpts, best first, each with its file, location (heading,
    pages or lines) and score. Scores order the results; they are not a relevance verdict.
    Short notes that answer the question can score low (0.05 to 0.2), so read the excerpts
    rather than discarding by score. "notes" explains degraded results.
    """
    return client.call(
        paths, "POST", "/api/search", timeout=120,
        json={"query": query, "session_id": session_id, "top_k": top_k},
    )


@server.tool()
def list_documents(session_id: str | None = None) -> dict:
    """List the documents VibeContext can search: the global ones, plus the ones attached to
    the given session. Shows each file's status; failed files carry the reason."""
    fields = ("id", "filename", "scope", "kind", "status", "chunk_count", "error")
    found = client.call(paths, "GET", "/api/documents", params={"scope": "global"})["documents"]
    if session_id:
        found += client.call(paths, "GET", "/api/documents", params={"session_id": session_id})["documents"]
    return {"documents": [{key: doc[key] for key in fields} for doc in found]}


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

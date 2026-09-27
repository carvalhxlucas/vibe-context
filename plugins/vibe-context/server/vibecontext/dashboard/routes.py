"""Dashboard pages and the htmx fragments they swap in.

Pages and /ui/* fragments authenticate with the dashboard cookie (see auth.py);
the middleware in api/app.py enforces it, and requires the HX-Request header on
every change so a form on another site cannot submit here.
"""

import datetime
import sqlite3
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from vibecontext.api.deps import get_db, get_settings
from vibecontext.api.routes_documents import remove_document, store_upload
from vibecontext.config import Settings
from vibecontext.dashboard.auth import COOKIE_NAME
from vibecontext.db import documents, store
from vibecontext.ingest.errors import RetryLater

MAX_FILES_PER_UPLOAD = 10

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


def _ago(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    seconds = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(timestamp)).total_seconds()
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def _size(size_bytes: int) -> str:
    for unit, size in (("MB", 1024 * 1024), ("KB", 1024)):
        if size_bytes >= size:
            return f"{size_bytes / size:.1f} {unit}"
    return f"{size_bytes} B"


def _location(meta: dict) -> str:
    parts = []
    if meta.get("heading"):
        parts.append(meta["heading"])
    if meta.get("pages"):
        parts.append("p. " + ", ".join(str(p) for p in meta["pages"]))
    if meta.get("start_line"):
        parts.append(f"lines {meta['start_line']}-{meta['end_line']}")
    if meta.get("symbols"):
        parts.append(", ".join(meta["symbols"]))
    return " · ".join(parts)


templates.env.filters["ago"] = _ago
templates.env.filters["size"] = _size
templates.env.filters["location"] = _location


def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def login_required(request: Request, reason: str | None = None) -> HTMLResponse:
    response = render(request, "login_required.html", status_code=401, reason=reason)
    # An htmx fragment request would drop a 401 silently; reload so the page shows it.
    if request.headers.get("hx-request") == "true":
        response.headers["HX-Refresh"] = "true"
    return response


# --- login -----------------------------------------------------------------


@router.post("/api/dashboard/login-code")
def login_code(request: Request) -> dict:
    """Bearer-authenticated, like the rest of /api: only a local token holder gets a code."""
    return {"path": "/login?code=" + request.app.state.dashboard_auth.issue_code()}


@router.get("/login")
def login(request: Request, code: str = ""):
    session = request.app.state.dashboard_auth.redeem(code)
    if session is None:
        return login_required(request, "This login link is invalid, expired or already used.")
    # Redirect so the one-time code leaves the address bar and the history entry.
    response = RedirectResponse("/sessions", status_code=303)
    response.set_cookie(COOKIE_NAME, session, httponly=True, samesite="strict", path="/")
    return response


# --- pages -----------------------------------------------------------------


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/sessions", status_code=303)


@router.get("/sessions")
def sessions_page(
    request: Request, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)
):
    sessions = store.list_sessions(db, None, 100, settings.vibecontext_stale_after_hours)
    counts = store.document_counts_by_session(db)
    for session in sessions:
        session["documents"] = counts.get(session["id"], 0)
    return render(request, "sessions.html", page="sessions", sessions=sessions)


@router.get("/sessions/{session_id}")
def session_page(
    session_id: str,
    request: Request,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    session = store.get_session(db, session_id, settings.vibecontext_stale_after_hours)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    table = _table_context(db, scope=None, session_id=session_id, status=None)
    return render(request, "session.html", page="sessions", session=session, scope="session", **table)


@router.get("/global")
def global_page(request: Request, db: sqlite3.Connection = Depends(get_db)):
    table = _table_context(db, scope="global", session_id=None, status=None)
    return render(request, "global.html", page="global", scope="global", session=None, **table)


@router.get("/library")
def library_page(
    request: Request, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)
):
    table = _table_context(db, scope=None, session_id=None, status=None)
    sessions = store.list_sessions(db, None, 100, settings.vibecontext_stale_after_hours)
    return render(request, "library.html", page="library", sessions=sessions, **table)


# --- fragments -------------------------------------------------------------


def _table_context(db: sqlite3.Connection, scope: str | None, session_id: str | None, status: str | None) -> dict:
    filters = {k: v for k, v in (("scope", scope), ("session_id", session_id), ("status", status)) if v}
    docs = documents.list_documents(db, scope=scope, session_id=session_id, status=status, limit=500)
    return {
        "documents": docs,
        "table_query": urlencode(filters),
        # Poll only while something is queued; the response decides whether to keep polling.
        "busy": any(d["status"] in ("pending", "processing") for d in docs),
        "show_scope": scope is None and session_id is None,
    }


TableScope = Literal["session", "global"] | None
TableStatus = Literal["pending", "processing", "indexed", "failed"] | None


@router.get("/ui/documents")
def documents_table(
    request: Request,
    scope: TableScope = None,
    session_id: str | None = None,
    status: TableStatus = None,
    db: sqlite3.Connection = Depends(get_db),
):
    context = _table_context(db, scope or None, session_id or None, status or None)
    return render(request, "partials/documents_table.html", **context)


@router.post("/ui/documents/{doc_id}/reindex")
def reindex_document(
    doc_id: str,
    request: Request,
    scope: TableScope = None,
    session_id: str | None = None,
    status: TableStatus = None,
    db: sqlite3.Connection = Depends(get_db),
):
    if documents.request_reindex(db, doc_id):
        request.app.state.worker.wake()
    return render(request, "partials/documents_table.html", **_table_context(db, scope, session_id, status))


@router.delete("/ui/documents/{doc_id}")
def delete_document(
    doc_id: str,
    request: Request,
    scope: TableScope = None,
    session_id: str | None = None,
    status: TableStatus = None,
    db: sqlite3.Connection = Depends(get_db),
):
    remove_document(request, db, doc_id)
    return render(request, "partials/documents_table.html", **_table_context(db, scope, session_id, status))


@router.get("/ui/documents/{doc_id}/chunks")
def document_chunks(doc_id: str, request: Request, close: bool = False, db: sqlite3.Connection = Depends(get_db)):
    document = documents.get_document(db, doc_id)
    if document is None or close:
        return render(request, "partials/chunks.html", doc_id=doc_id, document=None, chunks=[])
    chunks = documents.list_chunks(db, doc_id, limit=200, offset=0)
    return render(request, "partials/chunks.html", doc_id=doc_id, document=document, chunks=chunks)


@router.post("/ui/upload")
def upload(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    scope: Annotated[Literal["session", "global"], Form()],
    session_id: Annotated[str | None, Form()] = None,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    messages = []
    for upload_file in files[:MAX_FILES_PER_UPLOAD]:
        if not upload_file.filename:
            continue
        try:
            document, duplicate = store_upload(request, db, settings, upload_file, scope, session_id or None)
        except HTTPException as error:
            messages.append(("error", f"{upload_file.filename}: {error.detail}"))
            continue
        if duplicate:
            messages.append(("info", f"{document['filename']}: already added ({document['status']})"))
        else:
            messages.append(("ok", f"{document['filename']}: queued for indexing"))
    if len(files) > MAX_FILES_PER_UPLOAD:
        messages.append(("error", f"Only the first {MAX_FILES_PER_UPLOAD} files were accepted."))
    if not messages:
        messages.append(("error", "Choose at least one file."))
    response = render(
        request, "partials/upload.html", scope=scope, session={"id": session_id} if session_id else None,
        messages=messages,
    )
    response.headers["HX-Trigger"] = "documents-changed"
    return response


@router.get("/ui/status")
def status_bar(request: Request, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    indexer = request.app.state.indexer
    searcher = request.app.state.searcher
    try:
        points = indexer.store.count(indexer.collection)
    except RetryLater:
        points = None
    return render(
        request,
        "partials/status.html",
        points=points,
        embedding=indexer.embedder.model_id,
        reranker=searcher.reranker_id,
        reranker_ready=searcher.reranker_ready,
        counts=documents.count_documents(db),
        auto_inject=settings.vibecontext_auto_inject,
    )


@router.post("/ui/search")
def search(
    request: Request,
    query: Annotated[str, Form(max_length=2000)] = "",
    session_id: Annotated[str | None, Form()] = None,
):
    if not query.strip():
        return render(request, "partials/search_results.html", response=None, error="Type a question.")
    try:
        response = request.app.state.searcher.search(query.strip(), session_id or None, 5)
    except RetryLater as error:
        return render(request, "partials/search_results.html", response=None, error=str(error))
    return render(request, "partials/search_results.html", response=response, error=None)

"""Dashboard pages and the htmx fragments they swap in.

Pages and /ui/* fragments authenticate with the dashboard cookie (see auth.py); the
middleware in api/app.py enforces it and requires the HX-Request header on every
change, so a form on another site cannot submit here. Changes answer with an
HX-Trigger header: "vc-changed" makes each region reload itself, "vc-toast" shows a
message, "vc-fed" plays the brain's absorbing animation.
"""

import json
import sqlite3
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from vibecontext.api.deps import get_db, get_settings
from vibecontext.api.routes_documents import attach_document, detach_document, remove_document, store_upload
from vibecontext.config import Settings
from vibecontext.dashboard import views
from vibecontext.dashboard.auth import COOKIE_NAME
from vibecontext.db import documents, store
from vibecontext.ingest.errors import RetryLater

MAX_FILES_PER_UPLOAD = 10

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
templates.env.filters["location"] = lambda meta: " · ".join(
    part for part in (
        meta.get("heading"),
        ("p. " + ", ".join(str(p) for p in meta["pages"])) if meta.get("pages") else None,
        f"linhas {meta['start_line']}-{meta['end_line']}" if meta.get("start_line") else None,
        ", ".join(meta["symbols"]) if meta.get("symbols") else None,
    ) if part
)


def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def changed(response: Response, toast: str | None = None, fed: bool = False) -> Response:
    events: dict = {"vc-changed": True}
    if toast:
        events["vc-toast"] = toast
    if fed:
        events["vc-fed"] = True
    response.headers["HX-Trigger"] = json.dumps(events)
    return response


def login_required(request: Request, reason: str | None = None) -> HTMLResponse:
    response = render(request, "login_required.html", status_code=401, reason=reason)
    # An htmx fragment request would drop a 401 silently; reload so the page shows it.
    if request.headers.get("hx-request") == "true":
        response.headers["HX-Refresh"] = "true"
    return response


def _snapshot(db: sqlite3.Connection, settings: Settings, selected: str | None = None) -> views.Snapshot:
    return views.Snapshot(db, settings.vibecontext_stale_after_hours, selected)


def _target(db: sqlite3.Connection, settings: Settings, target: str) -> str | None:
    """'global', 'library' (None) or a session id that exists."""
    if target == "library":
        return None
    if target == documents.GLOBAL:
        return target
    if store.get_session(db, target, settings.vibecontext_stale_after_hours) is None:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return target


def _target_name(snapshot: views.Snapshot, target: str | None) -> str:
    if target is None:
        return "biblioteca"
    if target == documents.GLOBAL:
        return "contexto global"
    session = snapshot.sessions_by_id.get(target)
    return session["name"] if session else target[:8]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# --- login -------------------------------------------------------------------


@router.post("/api/dashboard/login-code")
def login_code(request: Request) -> dict:
    """Bearer-authenticated, like the rest of /api: only a local token holder gets a code."""
    return {"path": "/login?code=" + request.app.state.dashboard_auth.issue_code()}


@router.get("/login")
def login(request: Request, code: str = ""):
    session = request.app.state.dashboard_auth.redeem(code)
    if session is None:
        return login_required(request, "Este link de acesso é inválido, expirou ou já foi usado.")
    # Redirect so the one-time code leaves the address bar and the history entry.
    response = RedirectResponse("/sessions", status_code=303)
    response.set_cookie(COOKIE_NAME, session, httponly=True, samesite="strict", path="/")
    return response


# --- pages -------------------------------------------------------------------


@router.get("/")
def home() -> RedirectResponse:
    return RedirectResponse("/sessions", status_code=303)


@router.get("/sessions")
def sessions_page(
    request: Request,
    s: str | None = None,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snap = _snapshot(db, settings, s)
    if s is not None and snap.selected is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return render(request, "sessions.html", view="sessions", **_session_context(snap))


@router.get("/global")
def global_page(request: Request, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)):
    snap = _snapshot(db, settings)
    return render(request, "global.html", view="global", **_global_context(snap))


@router.get("/library")
def library_page(
    request: Request,
    q: str = "",
    kind: str = "all",
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    snap = _snapshot(db, settings)
    return render(request, "library.html", view="library", **_library_context(snap, q, kind))


# --- regions -----------------------------------------------------------------


def _shell_context(snap: views.Snapshot) -> dict:
    return {
        "snap": snap,
        "global_tokens_fmt": views.fmt_tokens(snap.global_tokens),
        "nav_counts": {
            "sessions": len(snap.live_sessions),
            "global": len(snap.global_docs),
            "library": len(snap.docs),
        },
    }


def _session_context(snap: views.Snapshot) -> dict:
    sel = snap.selected
    context = {**_shell_context(snap), "sel": sel}
    if sel is not None:
        context["ctx"] = snap.session_context(sel)
        context["sess_files"] = snap.files(snap.session_docs(sel["id"]))
        context["global_files"] = snap.files(snap.global_docs)
    return context


def _global_context(snap: views.Snapshot) -> dict:
    return {
        **_shell_context(snap),
        "gctx": snap.global_context(),
        "global_files": snap.files(snap.global_docs),
    }


def _library_context(snap: views.Snapshot, q: str, kind: str) -> dict:
    rows = snap.files(snap.docs)
    counts = {k: sum(1 for r in rows if r["kind"] == k) for k in views.KINDS}
    query = q.strip().lower()
    shown = [r for r in rows if (kind == "all" or r["kind"] == kind) and (not query or query in r["name"].lower())]
    chips = [("all", "Todos", len(rows)), ("code", "Código", counts["code"]), ("doc", "Docs", counts["doc"]),
             ("spec", "Specs", counts["spec"])]
    return {
        **_shell_context(snap),
        "rows": shown,
        "q": q,
        "kind": kind if kind in ("all", *views.KINDS) else "all",
        "chips": chips,
        "busy": any(r["state"] == "waiting" for r in rows),
    }


@router.get("/ui/sidebar")
def sidebar(request: Request, view: str = "sessions", db=Depends(get_db), settings: Settings = Depends(get_settings)):
    snap = _snapshot(db, settings)
    return render(request, "partials/sidebar.html", view=view, **_shell_context(snap))


@router.get("/ui/sessions/{session_id}/panel")
def session_panel(session_id: str, request: Request, db=Depends(get_db), settings: Settings = Depends(get_settings)):
    snap = _snapshot(db, settings, session_id)
    if snap.selected is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return render(request, "partials/session_panel.html", view="sessions", oob=True, **_session_context(snap))


@router.get("/ui/global/panel")
def global_panel(request: Request, db=Depends(get_db), settings: Settings = Depends(get_settings)):
    snap = _snapshot(db, settings)
    return render(request, "partials/global_panel.html", view="global", oob=True, **_global_context(snap))


@router.get("/ui/library/table")
def library_table(
    request: Request, q: str = "", kind: str = "all", db=Depends(get_db), settings: Settings = Depends(get_settings)
):
    snap = _snapshot(db, settings)
    return render(request, "partials/library_table.html", **_library_context(snap, q, kind))


@router.get("/ui/status")
def status_box(request: Request, db=Depends(get_db), settings: Settings = Depends(get_settings)):
    indexer = request.app.state.indexer
    searcher = request.app.state.searcher
    try:
        points = indexer.store.count(indexer.collection)
    except RetryLater:
        points = None
    return render(
        request,
        "partials/status.html",
        snap=_snapshot(db, settings),
        port=settings.vibecontext_port,
        qdrant_ok=points is not None,
        embedding=indexer.embedder.model_id.split("/", 1)[-1],
        reranker=searcher.reranker_id,
        reranker_ready=searcher.reranker_ready,
        counts=documents.count_documents(db),
        auto_inject=settings.vibecontext_auto_inject,
    )


# --- attaching ---------------------------------------------------------------


@router.post("/ui/attach")
def attach(
    request: Request,
    doc_id: Annotated[str, Form()],
    target: Annotated[str, Form()],
    db=Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    resolved = _target(db, settings, target)
    document = documents.get_document(db, doc_id)
    if document is None or resolved is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    attach_document(request, db, doc_id, resolved)
    snap = _snapshot(db, settings)
    toast = f"{document['filename']} anexado a {_target_name(snap, resolved)}"
    if document["status"] == "indexed":
        toast += f" · +{views.fmt_tokens(document['token_count'])} tokens"
    return changed(Response(status_code=200), toast, fed=True)


@router.post("/ui/detach")
def detach(
    request: Request,
    doc_id: Annotated[str, Form()],
    target: Annotated[str, Form()],
    db=Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    document = documents.get_document(db, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    detach_document(request, db, doc_id, target)
    snap = _snapshot(db, settings)
    return changed(Response(status_code=200), f"{document['filename']} desanexado de {_target_name(snap, target)}")


@router.post("/ui/upload")
def upload(
    request: Request,
    files: Annotated[list[UploadFile], File()],
    target: Annotated[str, Form()],
    db=Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    resolved = _target(db, settings, target)
    accepted, messages = 0, []
    for upload_file in files[:MAX_FILES_PER_UPLOAD]:
        if not upload_file.filename:
            continue
        try:
            document, duplicate = store_upload(request, db, settings, upload_file, resolved)
        except HTTPException as error:
            messages.append({"kind": "error", "text": f"{upload_file.filename}: {error.detail}"})
            continue
        accepted += 1
        note = "já estava na biblioteca" if duplicate else "na fila de indexação"
        messages.append({"kind": "ok", "text": f"{document['filename']}: {note}"})
    if len(files) > MAX_FILES_PER_UPLOAD:
        messages.append({"kind": "error", "text": f"Só os primeiros {MAX_FILES_PER_UPLOAD} arquivos foram aceitos."})

    name = _target_name(_snapshot(db, settings), resolved)
    rejected = sum(1 for m in messages if m["kind"] == "error")
    toast = (
        f"{_plural(accepted, 'arquivo anexado', 'arquivos anexados')} a {name}" if resolved
        else f"{_plural(accepted, 'arquivo importado', 'arquivos importados')} na biblioteca"
    )
    if rejected:
        toast += f" · {_plural(rejected, 'recusado', 'recusados')}"
    response = JSONResponse({"messages": messages, "toast": toast})
    return changed(response, toast, fed=bool(accepted and resolved))


# --- library actions ---------------------------------------------------------


@router.post("/ui/documents/{doc_id}/reindex")
def reindex(doc_id: str, request: Request, db=Depends(get_db)):
    if documents.request_reindex(db, doc_id):
        request.app.state.worker.wake()
    return changed(Response(status_code=200), "Reindexação na fila")


@router.delete("/ui/documents/{doc_id}")
def delete(doc_id: str, request: Request, db=Depends(get_db)):
    document = documents.get_document(db, doc_id)
    remove_document(request, db, doc_id)
    name = document["filename"] if document else "Arquivo"
    return changed(Response(status_code=200), f"{name} removido da biblioteca")


# --- modals ------------------------------------------------------------------


@router.get("/ui/picker")
def picker(
    request: Request, target: str, q: str = "", rows_only: bool = False, db=Depends(get_db),
    settings: Settings = Depends(get_settings),
):
    resolved = _target(db, settings, target)
    if resolved is None:
        raise HTTPException(status_code=404, detail="Destino inválido")
    snap = _snapshot(db, settings)
    query = q.strip().lower()
    rows = [r for r in snap.files(snap.docs) if not query or query in r["name"].lower()]
    for row in rows:
        row["attached"] = row["global"] if resolved == documents.GLOBAL else resolved in row["sessions"]
    name = "partials/picker_rows.html" if rows_only else "partials/picker.html"
    return render(request, name, rows=rows, target=resolved, target_name=_target_name(snap, resolved), q=q)


@router.get("/ui/documents/{doc_id}/chunks")
def chunks(doc_id: str, request: Request, db=Depends(get_db)):
    document = documents.get_document(db, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return render(
        request, "partials/chunks.html", document=document, chunks=documents.list_chunks(db, doc_id, 200, 0)
    )


@router.get("/ui/search")
def search_modal(request: Request, db=Depends(get_db), settings: Settings = Depends(get_settings)):
    return render(request, "partials/search.html", sessions=_snapshot(db, settings).live_sessions)


@router.post("/ui/search")
def search(
    request: Request,
    query: Annotated[str, Form(max_length=2000)] = "",
    session_id: Annotated[str | None, Form()] = None,
):
    if not query.strip():
        return render(request, "partials/search_results.html", response=None, error="Escreva uma pergunta.")
    try:
        response = request.app.state.searcher.search(query.strip(), session_id or None, 5)
    except RetryLater as error:
        return render(request, "partials/search_results.html", response=None, error=str(error))
    return render(request, "partials/search_results.html", response=response, error=None)

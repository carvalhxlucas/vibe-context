import sqlite3
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile

from vibecontext.api.deps import get_db, get_settings
from vibecontext.config import Settings
from vibecontext.db import documents, store
from vibecontext.ingest import filetypes, storage
from vibecontext.ingest.errors import IngestError

router = APIRouter(prefix="/api/documents")


def _require_document(db: sqlite3.Connection, doc_id: str) -> dict:
    document = documents.get_document(db, doc_id)
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return document


def _check_scope(db: sqlite3.Connection, settings: Settings, scope: str, session_id: str | None) -> None:
    if scope == "session":
        if session_id is None:
            raise HTTPException(status_code=422, detail="session_id is required when scope is 'session'")
        if store.get_session(db, session_id, settings.vibecontext_stale_after_hours) is None:
            raise HTTPException(status_code=404, detail="Session not found")
    elif session_id is not None:
        raise HTTPException(status_code=422, detail="session_id must be empty when scope is 'global'")


def store_upload(
    request: Request, db: sqlite3.Connection, settings: Settings, upload: UploadFile, scope: str, session_id: str | None
) -> tuple[dict, bool]:
    """Validate, save and queue one uploaded file. Returns (document, duplicate).

    Shared by the JSON API and the dashboard. Raises HTTPException on rejection and
    leaves no file behind when it does.
    """
    _check_scope(db, settings, scope, session_id)
    try:
        filename = storage.display_name(upload.filename)
    except IngestError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        file_type = filetypes.from_filename(filename)
    except IngestError as error:
        raise HTTPException(status_code=415, detail=str(error)) from error

    doc_id = uuid.uuid4().hex
    stored_path = doc_id + file_type.extension
    destination = storage.file_path(request.app.state.paths.files, stored_path)
    try:
        size, sha256, head = storage.save(upload.file, destination, settings.max_upload_mb * 1024 * 1024)
    except storage.UploadTooLarge as error:
        raise HTTPException(
            status_code=413, detail=f"File is larger than MAX_UPLOAD_MB ({settings.max_upload_mb} MB)"
        ) from error

    try:
        if size == 0:
            raise HTTPException(status_code=422, detail="File is empty.")
        try:
            filetypes.confirm_content(file_type, head)
        except IngestError as error:
            raise HTTPException(status_code=415, detail=str(error)) from error
        duplicate = documents.find_duplicate(db, sha256, scope, session_id)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise

    if duplicate is not None:
        destination.unlink(missing_ok=True)
        return duplicate, True

    document = documents.insert_document(
        db,
        doc_id=doc_id,
        scope=scope,
        session_id=session_id,
        filename=filename,
        stored_path=stored_path,
        kind=file_type.kind,
        language=file_type.language,
        size_bytes=size,
        sha256=sha256,
    )
    request.app.state.worker.wake()
    return document, False


def remove_document(request: Request, db: sqlite3.Connection, doc_id: str) -> bool:
    deleted = documents.delete_document(db, doc_id)
    if deleted is None:
        return False
    storage.file_path(request.app.state.paths.files, deleted["stored_path"]).unlink(missing_ok=True)
    # Best effort: with Qdrant down the points stay behind, and search drops
    # results whose chunk no longer exists in SQLite.
    request.app.state.indexer.remove(doc_id, deleted["index_key"])
    return True


@router.post("")
def upload(
    request: Request,
    response: Response,
    file: Annotated[UploadFile, File()],
    scope: Annotated[Literal["session", "global"], Form()],
    session_id: Annotated[str | None, Form()] = None,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    document, duplicate = store_upload(request, db, settings, file, scope, session_id or None)
    response.status_code = 200 if duplicate else 201
    return {"document": document, "duplicate": duplicate}


@router.get("")
def list_documents(
    scope: Literal["session", "global"] | None = None,
    session_id: str | None = None,
    status: Literal["pending", "processing", "indexed", "failed"] | None = None,
    limit: int = Query(200, ge=1, le=1000),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"documents": documents.list_documents(db, scope=scope, session_id=session_id, status=status, limit=limit)}


@router.get("/{doc_id}")
def get_document(doc_id: str, db: sqlite3.Connection = Depends(get_db)) -> dict:
    return _require_document(db, doc_id)


@router.get("/{doc_id}/chunks")
def list_chunks(
    doc_id: str,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: sqlite3.Connection = Depends(get_db),
) -> dict:
    _require_document(db, doc_id)
    return {"chunks": documents.list_chunks(db, doc_id, limit, offset)}


@router.delete("/{doc_id}", status_code=204)
def delete_document(doc_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)) -> Response:
    if not remove_document(request, db, doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return Response(status_code=204)


@router.post("/{doc_id}/reindex", status_code=202)
def reindex(doc_id: str, request: Request, db: sqlite3.Connection = Depends(get_db)) -> dict:
    if not documents.request_reindex(db, doc_id):
        raise HTTPException(status_code=404, detail="Document not found")
    request.app.state.worker.wake()
    return documents.get_document(db, doc_id)

import sqlite3

from fastapi import APIRouter, Depends, Request

from vibecontext import __version__
from vibecontext.api.deps import get_db, get_settings
from vibecontext.config import Settings
from vibecontext.db import documents, store
from vibecontext.ingest.errors import RetryLater

router = APIRouter()


@router.get("/health")
def health() -> dict:
    # Unauthenticated on purpose: runtime.backend_healthy probes it. Reveals nothing.
    return {"status": "ok", "service": "vibecontext"}


@router.get("/api/status")
def status(
    request: Request, db: sqlite3.Connection = Depends(get_db), settings: Settings = Depends(get_settings)
) -> dict:
    indexer = request.app.state.indexer
    try:
        points = indexer.store.count(indexer.collection)
    except RetryLater:
        points = None
    return {
        "version": __version__,
        "qdrant": {"url": settings.qdrant_url, "reachable": points is not None},
        "embedding": {"model": indexer.embedder.model_id, "collection": indexer.collection, "points": points},
        "sessions": store.count_sessions(db, settings.vibecontext_stale_after_hours),
        "documents": documents.count_documents(db),
    }

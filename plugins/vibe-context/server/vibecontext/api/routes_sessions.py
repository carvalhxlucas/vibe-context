import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query

from vibecontext.api.deps import get_db, get_settings
from vibecontext.config import Settings
from vibecontext.db import store

router = APIRouter(prefix="/api/sessions")


@router.get("")
def list_sessions(
    status: Literal["open", "stale", "ended", "all"] = "all",
    limit: int = Query(50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    sessions = store.list_sessions(
        db, None if status == "all" else status, limit, settings.vibecontext_stale_after_hours
    )
    return {"sessions": sessions}


@router.get("/{session_id}")
def get_session(
    session_id: str,
    db: sqlite3.Connection = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    session = store.get_session(db, session_id, settings.vibecontext_stale_after_hours)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session

from collections.abc import Iterator
import sqlite3

from fastapi import Request

from vibecontext.config import Settings
from vibecontext.db import store


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = store.connect(request.app.state.paths.db)
    try:
        yield conn
    finally:
        conn.close()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings

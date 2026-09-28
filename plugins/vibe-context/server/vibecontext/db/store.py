"""SQLite access. The hook script writes sessions with its own stdlib copy of this logic."""

import datetime
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

SESSION_STATUSES = ("open", "stale", "ended")


def connect(db_path: Path) -> sqlite3.Connection:
    # FastAPI may open a request's connection on one threadpool thread and close it on
    # another. Each connection still serves a single request at a time, so allow that.
    conn = sqlite3.connect(db_path, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    # schema.sql creates new databases at the latest version; this brings older ones up.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    if "index_key" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN index_key TEXT")
    if "scope" in columns:
        _move_scope_to_attachments(conn)


def _move_scope_to_attachments(conn: sqlite3.Connection) -> None:
    """Before attachments, each document had one scope column. Turn it into an attachment
    and rebuild the table without it (SQLite cannot drop a column behind a CHECK)."""
    conn.execute("PRAGMA foreign_keys = OFF")  # only takes effect outside a transaction
    try:
        with conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO attachments (document_id, target, created_at)
                SELECT id, CASE scope WHEN 'global' THEN 'global' ELSE session_id END, created_at FROM documents
                """
            )
            conn.execute(
                """
                CREATE TABLE documents_new (
                    id          TEXT PRIMARY KEY,
                    filename    TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    kind        TEXT NOT NULL CHECK (kind IN ('pdf', 'docx', 'markdown', 'text', 'code')),
                    language    TEXT,
                    size_bytes  INTEGER NOT NULL,
                    sha256      TEXT NOT NULL,
                    status      TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
                    error       TEXT,
                    chunk_count INTEGER NOT NULL DEFAULT 0,
                    token_count INTEGER NOT NULL DEFAULT 0,
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL,
                    indexed_at  TEXT,
                    index_key   TEXT
                )
                """
            )
            conn.execute(
                """
                INSERT INTO documents_new
                SELECT id, filename, stored_path, kind, language, size_bytes, sha256, status, error, chunk_count,
                       (SELECT COALESCE(SUM(token_count), 0) FROM chunks WHERE document_id = documents.id),
                       created_at, updated_at, indexed_at, index_key
                FROM documents
                """
            )
            conn.execute("DROP TABLE documents")
            conn.execute("ALTER TABLE documents_new RENAME TO documents")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_queue ON documents (status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256)")
    finally:
        conn.execute("PRAGMA foreign_keys = ON")


def _stale_cutoff(stale_after_hours: int) -> str:
    cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=stale_after_hours)
    # Same format the hook writes, so the timestamps compare as strings.
    return cutoff.isoformat(timespec="seconds")


def _to_dict(row: sqlite3.Row, cutoff: str) -> dict:
    session = dict(row)
    # A session that crashed never fires SessionEnd; report it as stale instead of open.
    if session["status"] == "open" and session["last_activity_at"] < cutoff:
        session["status"] = "stale"
    return session


def list_sessions(conn: sqlite3.Connection, status: str | None, limit: int, stale_after_hours: int) -> list[dict]:
    cutoff = _stale_cutoff(stale_after_hours)
    where, params = "", []
    if status == "open":
        where, params = "WHERE status = 'open' AND last_activity_at >= ?", [cutoff]
    elif status == "stale":
        where, params = "WHERE status = 'open' AND last_activity_at < ?", [cutoff]
    elif status == "ended":
        where = "WHERE status = 'ended'"
    rows = conn.execute(
        f"SELECT * FROM sessions {where} ORDER BY last_activity_at DESC LIMIT ?", [*params, limit]
    ).fetchall()
    return [_to_dict(row, cutoff) for row in rows]


def get_session(conn: sqlite3.Connection, session_id: str, stale_after_hours: int) -> dict | None:
    row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return _to_dict(row, _stale_cutoff(stale_after_hours)) if row else None


def count_sessions(conn: sqlite3.Connection, stale_after_hours: int) -> dict[str, int]:
    row = conn.execute(
        """
        SELECT
            SUM(status = 'open' AND last_activity_at >= :cutoff) AS open,
            SUM(status = 'open' AND last_activity_at < :cutoff) AS stale,
            SUM(status = 'ended') AS ended
        FROM sessions
        """,
        {"cutoff": _stale_cutoff(stale_after_hours)},
    ).fetchone()
    return {key: row[key] or 0 for key in SESSION_STATUSES}


def document_counts_by_session(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT target, COUNT(*) AS n FROM attachments WHERE target != 'global' GROUP BY target")
    return {row["target"]: row["n"] for row in rows}

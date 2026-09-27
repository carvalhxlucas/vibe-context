"""SQLite access. The hook script writes sessions with its own stdlib copy of this logic."""

import datetime
import sqlite3
from pathlib import Path

SCHEMA = Path(__file__).with_name("schema.sql")

SESSION_STATUSES = ("open", "stale", "ended")


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    _migrate(conn)
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    # schema.sql creates new databases at the latest version; this brings older ones up.
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    if "index_key" not in columns:
        conn.execute("ALTER TABLE documents ADD COLUMN index_key TEXT")


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
    rows = conn.execute(
        "SELECT session_id, COUNT(*) AS n FROM documents WHERE session_id IS NOT NULL GROUP BY session_id"
    )
    return {row["session_id"]: row["n"] for row in rows}

"""Documents and chunks. The documents table is also the ingestion queue."""

import datetime
import json
import sqlite3
import uuid
from dataclasses import dataclass, field

DOCUMENT_STATUSES = ("pending", "processing", "indexed", "failed")

# stored_path stays internal: callers outside the backend never need the on-disk name.
PUBLIC_COLUMNS = (
    "id, scope, session_id, filename, kind, language, size_bytes, sha256, status, error, "
    "chunk_count, created_at, updated_at, indexed_at, index_key"
)


@dataclass
class Chunk:
    text: str
    token_count: int
    meta: dict = field(default_factory=dict)
    # Shared by the SQLite row and the Qdrant point.
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def insert_document(
    conn: sqlite3.Connection,
    *,
    doc_id: str,
    scope: str,
    session_id: str | None,
    filename: str,
    stored_path: str,
    kind: str,
    language: str | None,
    size_bytes: int,
    sha256: str,
) -> dict:
    ts = _now()
    with conn:
        conn.execute(
            """
            INSERT INTO documents (id, scope, session_id, filename, stored_path, kind, language,
                                   size_bytes, sha256, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (doc_id, scope, session_id, filename, stored_path, kind, language, size_bytes, sha256, ts, ts),
        )
    return get_document(conn, doc_id)


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return dict(row) if row else None


def find_duplicate(conn: sqlite3.Connection, sha256: str, scope: str, session_id: str | None) -> dict | None:
    # "IS" matches NULL to NULL, so one query covers global and session documents.
    row = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM documents WHERE sha256 = ? AND scope = ? AND session_id IS ?",
        (sha256, scope, session_id),
    ).fetchone()
    return dict(row) if row else None


def list_documents(
    conn: sqlite3.Connection,
    *,
    scope: str | None = None,
    session_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> list[dict]:
    clauses, params = [], []
    if scope is not None:
        clauses.append("scope = ?")
        params.append(scope)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM documents {where} ORDER BY created_at DESC LIMIT ?", [*params, limit]
    ).fetchall()
    return [dict(row) for row in rows]


def count_documents(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(DOCUMENT_STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def delete_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    """Delete the row and its chunks. Returns stored_path and index_key, or None when missing."""
    with conn:
        row = conn.execute(
            "DELETE FROM documents WHERE id = ? RETURNING stored_path, index_key", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


def request_reindex(conn: sqlite3.Connection, doc_id: str) -> bool:
    with conn:
        cursor = conn.execute(
            "UPDATE documents SET status = 'pending', error = NULL, updated_at = ? WHERE id = ?", (_now(), doc_id)
        )
    return cursor.rowcount == 1


def reset_interrupted(conn: sqlite3.Connection) -> int:
    """Requeue documents a previous backend process was working on when it died."""
    with conn:
        cursor = conn.execute(
            "UPDATE documents SET status = 'pending', updated_at = ? WHERE status = 'processing'", (_now(),)
        )
    return cursor.rowcount


def requeue_outdated(conn: sqlite3.Connection, index_key: str) -> int:
    """Requeue documents indexed into another collection, after the embedding model changed."""
    with conn:
        cursor = conn.execute(
            """
            UPDATE documents SET status = 'pending', error = NULL, updated_at = ?
            WHERE status = 'indexed' AND (index_key IS NULL OR index_key != ?)
            """,
            (_now(), index_key),
        )
    return cursor.rowcount


def defer(conn: sqlite3.Connection, doc_id: str, reason: str) -> None:
    """Put a document back in the queue after an environment failure, keeping the reason visible."""
    with conn:
        conn.execute(
            "UPDATE documents SET status = 'pending', error = ?, updated_at = ? WHERE id = ? AND status = 'processing'",
            (reason, _now(), doc_id),
        )


def claim_next(conn: sqlite3.Connection) -> dict | None:
    with conn:
        row = conn.execute(
            """
            UPDATE documents SET status = 'processing', error = NULL, updated_at = ?
            WHERE id = (SELECT id FROM documents WHERE status = 'pending' ORDER BY created_at LIMIT 1)
            RETURNING id, scope, session_id, kind, language, filename, stored_path, index_key
            """,
            (_now(),),
        ).fetchone()
    return dict(row) if row else None


def complete(conn: sqlite3.Connection, doc_id: str, chunks: list[Chunk], index_key: str | None = None) -> bool:
    """Store the chunks and mark the document indexed, in one transaction.

    Returns False, writing nothing, when the document was deleted or requeued for
    reindexing while the worker was processing it.
    """
    ts = _now()
    with conn:
        cursor = conn.execute(
            """
            UPDATE documents
            SET status = 'indexed', error = NULL, chunk_count = ?, indexed_at = ?, updated_at = ?, index_key = ?
            WHERE id = ? AND status = 'processing'
            """,
            (len(chunks), ts, ts, index_key, doc_id),
        )
        if cursor.rowcount != 1:
            return False
        conn.execute("DELETE FROM chunks WHERE document_id = ?", (doc_id,))
        conn.executemany(
            "INSERT INTO chunks (id, document_id, ordinal, text, token_count, meta) VALUES (?, ?, ?, ?, ?, ?)",
            [
                (chunk.id, doc_id, ordinal, chunk.text, chunk.token_count, json.dumps(chunk.meta))
                for ordinal, chunk in enumerate(chunks)
            ],
        )
    return True


def fail(conn: sqlite3.Connection, doc_id: str, message: str) -> None:
    with conn:
        conn.execute(
            "UPDATE documents SET status = 'failed', error = ?, updated_at = ? WHERE id = ? AND status = 'processing'",
            (message, _now(), doc_id),
        )


def list_chunks(conn: sqlite3.Connection, doc_id: str, limit: int, offset: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT id, ordinal, text, token_count, meta FROM chunks
        WHERE document_id = ? ORDER BY ordinal LIMIT ? OFFSET ?
        """,
        (doc_id, limit, offset),
    ).fetchall()
    return [{**dict(row), "meta": json.loads(row["meta"])} for row in rows]

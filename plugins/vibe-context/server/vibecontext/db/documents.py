"""The library (documents), their chunks, and where each one is attached.

A document is stored and indexed once per distinct content. Attachments decide who
can search it: 'global' for every session, or one or more Claude Code session ids.
The documents table is also the ingestion queue.
"""

import datetime
import json
import sqlite3
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

DOCUMENT_STATUSES = ("pending", "processing", "indexed", "failed")
GLOBAL = "global"

# stored_path stays internal: callers outside the backend never need the on-disk name.
PUBLIC_COLUMNS = (
    "id, filename, kind, language, size_bytes, sha256, status, error, chunk_count, token_count, "
    "created_at, updated_at, indexed_at, index_key"
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


def _placeholders(values) -> str:
    return ",".join("?" * len(values))


# --- attachments -----------------------------------------------------------------


def targets_of(conn: sqlite3.Connection, doc_id: str) -> list[str]:
    rows = conn.execute("SELECT target FROM attachments WHERE document_id = ? ORDER BY created_at", (doc_id,))
    return [row["target"] for row in rows]


def _attachments_by_document(conn: sqlite3.Connection, doc_ids: list[str]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    if doc_ids:
        rows = conn.execute(
            f"SELECT document_id, target FROM attachments WHERE document_id IN ({_placeholders(doc_ids)}) "
            "ORDER BY created_at",
            doc_ids,
        )
        for row in rows:
            found[row["document_id"]].append(row["target"])
    return found


def describe_targets(targets: list[str]) -> dict:
    return {"global": GLOBAL in targets, "sessions": [t for t in targets if t != GLOBAL]}


def _with_attachments(conn: sqlite3.Connection, docs: list[dict]) -> list[dict]:
    targets = _attachments_by_document(conn, [d["id"] for d in docs])
    for doc in docs:
        doc["attachments"] = describe_targets(targets.get(doc["id"], []))
    return docs


def attach(conn: sqlite3.Connection, doc_id: str, target: str) -> bool:
    """Returns True when the attachment is new."""
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO attachments (document_id, target, created_at) VALUES (?, ?, ?)",
            (doc_id, target, _now()),
        )
    return cursor.rowcount == 1


def detach(conn: sqlite3.Connection, doc_id: str, target: str) -> bool:
    with conn:
        cursor = conn.execute("DELETE FROM attachments WHERE document_id = ? AND target = ?", (doc_id, target))
    return cursor.rowcount == 1


def visible_chunks(conn: sqlite3.Connection, chunk_ids: list[str], session_id: str | None) -> dict[str, str]:
    """Of these chunks, the ones search may return to this caller, mapped to why:
    'session' when the document is attached to the caller's session, else 'global'.

    SQLite is the source of truth: a chunk whose document was deleted or detached is
    dropped here even when the Qdrant payload has not caught up yet.
    """
    if not chunk_ids:
        return {}
    targets = [GLOBAL] + ([session_id] if session_id else [])
    rows = conn.execute(
        f"""
        SELECT c.id AS chunk_id, MAX(a.target != 'global') AS via_session
        FROM chunks c JOIN attachments a ON a.document_id = c.document_id
        WHERE c.id IN ({_placeholders(chunk_ids)}) AND a.target IN ({_placeholders(targets)})
        GROUP BY c.id
        """,
        [*chunk_ids, *targets],
    )
    return {row["chunk_id"]: "session" if row["via_session"] else "global" for row in rows}


# --- documents -------------------------------------------------------------------


def insert_document(
    conn: sqlite3.Connection,
    *,
    doc_id: str,
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
            INSERT INTO documents (id, filename, stored_path, kind, language, size_bytes, sha256,
                                   status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """,
            (doc_id, filename, stored_path, kind, language, size_bytes, sha256, ts, ts),
        )
    return get_document(conn, doc_id)


def get_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    row = conn.execute(f"SELECT {PUBLIC_COLUMNS} FROM documents WHERE id = ?", (doc_id,)).fetchone()
    return _with_attachments(conn, [dict(row)])[0] if row else None


def find_by_sha256(conn: sqlite3.Connection, sha256: str) -> dict | None:
    row = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM documents WHERE sha256 = ? ORDER BY created_at LIMIT 1", (sha256,)
    ).fetchone()
    return _with_attachments(conn, [dict(row)])[0] if row else None


def list_documents(
    conn: sqlite3.Connection,
    *,
    attached_to: str | None = None,
    unattached: bool = False,
    status: str | None = None,
    limit: int = 500,
) -> list[dict]:
    clauses, params = [], []
    if attached_to is not None:
        clauses.append("EXISTS (SELECT 1 FROM attachments a WHERE a.document_id = documents.id AND a.target = ?)")
        params.append(attached_to)
    if unattached:
        clauses.append("NOT EXISTS (SELECT 1 FROM attachments a WHERE a.document_id = documents.id)")
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    rows = conn.execute(
        f"SELECT {PUBLIC_COLUMNS} FROM documents {where} ORDER BY created_at DESC LIMIT ?", [*params, limit]
    ).fetchall()
    return _with_attachments(conn, [dict(row) for row in rows])


def count_documents(conn: sqlite3.Connection) -> dict[str, int]:
    counts = dict.fromkeys(DOCUMENT_STATUSES, 0)
    for row in conn.execute("SELECT status, COUNT(*) AS n FROM documents GROUP BY status"):
        counts[row["status"]] = row["n"]
    return counts


def indexed_targets(conn: sqlite3.Connection) -> list[tuple[str, str, list[str]]]:
    """(document id, collection, targets) for every indexed document, to resync Qdrant payloads."""
    docs = conn.execute("SELECT id, index_key FROM documents WHERE status = 'indexed' AND index_key IS NOT NULL")
    docs = [(row["id"], row["index_key"]) for row in docs]
    targets = _attachments_by_document(conn, [doc_id for doc_id, _ in docs])
    return [(doc_id, key, targets.get(doc_id, [])) for doc_id, key in docs]


def delete_document(conn: sqlite3.Connection, doc_id: str) -> dict | None:
    """Delete the document, its chunks and attachments. Returns stored_path and index_key."""
    with conn:
        row = conn.execute(
            "DELETE FROM documents WHERE id = ? RETURNING stored_path, index_key", (doc_id,)
        ).fetchone()
    return dict(row) if row else None


# --- ingestion queue -------------------------------------------------------------


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
            RETURNING id, kind, language, filename, stored_path, index_key
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
            SET status = 'indexed', error = NULL, chunk_count = ?, token_count = ?, indexed_at = ?, updated_at = ?,
                index_key = ?
            WHERE id = ? AND status = 'processing'
            """,
            (len(chunks), sum(c.token_count for c in chunks), ts, ts, index_key, doc_id),
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

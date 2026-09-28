import sqlite3

from vibecontext.db import documents, store

# The documents table before attachments existed (plugin 0.1.0).
OLD_DOCUMENTS = """
CREATE TABLE documents (
    id          TEXT PRIMARY KEY,
    scope       TEXT NOT NULL CHECK (scope IN ('session', 'global')),
    session_id  TEXT,
    filename    TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('pdf', 'docx', 'markdown', 'text', 'code')),
    language    TEXT,
    size_bytes  INTEGER NOT NULL,
    sha256      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    indexed_at  TEXT,
    index_key   TEXT,
    CHECK ((scope = 'global') = (session_id IS NULL))
);
CREATE INDEX idx_documents_scope ON documents (scope, session_id);
CREATE TABLE chunks (
    id          TEXT PRIMARY KEY,
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',
    UNIQUE (document_id, ordinal)
);
"""


def test_old_scope_column_becomes_attachments(tmp_path):
    db = tmp_path / "vibecontext.db"
    old = sqlite3.connect(db)
    old.executescript(OLD_DOCUMENTS)
    old.executescript(
        """
        INSERT INTO documents VALUES
          ('g', 'global', NULL, 'spec.md', 'g.md', 'markdown', NULL, 10, 'aaa', 'indexed', NULL, 2,
           '2026-09-01T00:00:00+00:00', '2026-09-01T00:00:00+00:00', NULL, 'col'),
          ('s', 'session', 's1', 'ata.md', 's.md', 'markdown', NULL, 10, 'bbb', 'pending', NULL, 0,
           '2026-09-02T00:00:00+00:00', '2026-09-02T00:00:00+00:00', NULL, NULL);
        INSERT INTO chunks VALUES ('c1', 'g', 0, 'one', 30, '{}'), ('c2', 'g', 1, 'two', 12, '{}');
        """
    )
    old.commit()
    old.close()

    conn = store.connect(db)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(documents)")}
    assert "scope" not in columns and "session_id" not in columns
    assert documents.get_document(conn, "g")["attachments"] == {"global": True, "sessions": []}
    assert documents.get_document(conn, "s")["attachments"] == {"global": False, "sessions": ["s1"]}
    assert documents.get_document(conn, "g")["token_count"] == 42
    # Chunks survived the table rebuild and still cascade from their document.
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 2
    documents.delete_document(conn, "g")
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    conn.close()

    # A second connect finds nothing left to migrate.
    store.connect(db).close()

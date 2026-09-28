-- Shared by the hook script (stdlib sqlite3) and the backend.
-- Every statement must stay idempotent: both sides run this file on every connect.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sessions (
    id               TEXT PRIMARY KEY,
    cwd              TEXT NOT NULL,
    transcript_path  TEXT,
    start_source     TEXT,
    status           TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'ended')),
    started_at       TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    ended_at         TEXT,
    end_reason       TEXT,
    prompt_count     INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_sessions_status_activity
    ON sessions (status, last_activity_at DESC);

-- The library: one row per distinct file content. Doubles as the ingestion queue:
-- the worker claims rows in 'pending'.
CREATE TABLE IF NOT EXISTS documents (
    id          TEXT PRIMARY KEY,
    filename    TEXT NOT NULL,  -- original name, for display only; never used as a path
    stored_path TEXT NOT NULL,  -- "<id><ext>" inside the files directory
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
    index_key   TEXT  -- Qdrant collection holding this document's vectors
);

CREATE INDEX IF NOT EXISTS idx_documents_queue ON documents (status, created_at);
CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256);

-- Where search can see a document: 'global' (every session) or a Claude Code session id.
-- A document with no attachment stays in the library and is never searched.
CREATE TABLE IF NOT EXISTS attachments (
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    target      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (document_id, target)
);

CREATE INDEX IF NOT EXISTS idx_attachments_target ON attachments (target);

CREATE TABLE IF NOT EXISTS chunks (
    id          TEXT PRIMARY KEY,  -- also the Qdrant point id
    document_id TEXT NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    ordinal     INTEGER NOT NULL,
    text        TEXT NOT NULL,
    token_count INTEGER NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}',  -- JSON: pages, heading, symbols, start_line, end_line
    UNIQUE (document_id, ordinal)
);

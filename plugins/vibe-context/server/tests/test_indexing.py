import pytest
from qdrant_client import models

from vibecontext.config import Settings
from vibecontext.db import documents, store
from vibecontext.embeddings.base import collection_name
from vibecontext.embeddings.local_embedder import prompts_for
from vibecontext.embeddings.openai_embedder import OpenAIEmbedder
from vibecontext.ingest.errors import RetryLater
from vibecontext.ingest.indexer import embedding_text
from vibecontext.vectorstore.qdrant import DENSE, SPARSE


def add_session(paths, session_id):
    import datetime

    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES (?, '/repo', 'open', ?, ?)",
            (session_id, ts, ts),
        )
    conn.close()


def upload(client, auth, name, content, **form):
    form.setdefault("scope", "global")
    return client.post("/api/documents", headers=auth, data=form, files={"file": (name, content)}).json()["document"]


def points(app, doc_id):
    indexer = app.state.indexer
    return indexer.store.client.scroll(
        indexer.collection, with_payload=True, limit=1000,
        scroll_filter=models.Filter(
            must=[models.FieldCondition(key="document_id", match=models.MatchValue(value=doc_id))]
        ),
    )[0]


def chunk_ids(paths, doc_id):
    conn = store.connect(paths.db)
    try:
        return sorted(r[0] for r in conn.execute("SELECT id FROM chunks WHERE document_id = ?", (doc_id,)))
    finally:
        conn.close()


LONG_MD = b"# Billing\n\n" + b"\n\n".join(
    f"Paragraph {i} explains how invoices are generated and charged.".encode() for i in range(80)
)


def test_points_match_sqlite_chunks_and_carry_payload(client, auth, app, paths):
    document = upload(client, auth, "billing.md", LONG_MD)
    app.state.worker.run_once()
    indexed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert indexed["index_key"] == app.state.indexer.collection

    stored = points(app, document["id"])
    assert len(stored) == indexed["chunk_count"] > 1
    assert sorted(str(p.id) for p in stored) == chunk_ids(paths, document["id"])
    # scroll orders by point id, which is random; the first chunk is ordinal 0.
    payload = min(stored, key=lambda p: p.payload["ordinal"]).payload
    assert payload["global"] is True
    assert payload["sessions"] == []
    assert payload["filename"] == "billing.md"
    assert payload["meta"]["heading"] == "Billing"
    assert payload["text"].startswith("# Billing")


def test_collection_has_dense_and_bm25_idf_vectors(client, auth, app):
    upload(client, auth, "a.md", b"# A\n\ntext")
    app.state.worker.run_once()
    params = app.state.indexer.store.client.get_collection(app.state.indexer.collection).config.params
    assert params.vectors[DENSE].size == 16
    assert params.sparse_vectors[SPARSE].modifier.value == "idf"


def test_delete_and_reindex_update_points(client, auth, app, paths):
    document = upload(client, auth, "billing.md", LONG_MD)
    app.state.worker.run_once()
    before = {str(p.id) for p in points(app, document["id"])}

    client.post(f"/api/documents/{document['id']}/reindex", headers=auth)
    app.state.worker.run_once()
    after = {str(p.id) for p in points(app, document["id"])}
    assert len(after) == len(before)
    assert after.isdisjoint(before)

    client.delete(f"/api/documents/{document['id']}", headers=auth)
    assert points(app, document["id"]) == []


def test_environment_failure_defers_instead_of_failing(client, auth, app):
    embedder = app.state.indexer.embedder
    embedder.unavailable = "OPENAI_API_KEY is not set."
    document = upload(client, auth, "a.md", b"# A\n\ntext")

    assert app.state.worker.run_once() == "deferred"
    waiting = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert waiting["status"] == "pending"
    assert waiting["error"] == "Waiting to retry: OPENAI_API_KEY is not set."

    embedder.unavailable = None
    assert app.state.worker.run_once() == "done"
    indexed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert (indexed["status"], indexed["error"]) == ("indexed", None)


def test_points_written_for_a_deleted_document_are_removed(client, auth, app, paths):
    document = upload(client, auth, "a.md", b"# A\n\ntext")
    original_index = app.state.indexer.index

    def index_then_delete(job, chunks, targets):
        original_index(job, chunks, targets)
        conn = store.connect(paths.db)
        documents.delete_document(conn, job["id"])
        conn.close()

    app.state.indexer.index = index_then_delete
    app.state.worker.run_once()
    assert points(app, document["id"]) == []


def test_model_change_requeues_and_cleans_old_collection(client, auth, app, paths):
    document = upload(client, auth, "a.md", b"# A\n\ntext")
    app.state.worker.run_once()
    old_collection = app.state.indexer.collection

    app.state.indexer.collection = "vibecontext__fake_new_model"
    app.state.worker.start()
    app.state.worker.stop()
    # start() requeued the document; process it now if the thread did not already.
    app.state.worker.run_once()

    indexed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert (indexed["status"], indexed["index_key"]) == ("indexed", "vibecontext__fake_new_model")
    assert app.state.indexer.store.count(old_collection, document["id"]) == 0
    assert app.state.indexer.store.count("vibecontext__fake_new_model", document["id"]) == 1


def test_status_reports_embedding_and_points(client, auth, app):
    upload(client, auth, "a.md", b"# A\n\ntext")
    app.state.worker.run_once()
    body = client.get("/api/status", headers=auth).json()
    assert body["embedding"] == {"model": "fake/hash-embedder", "collection": app.state.indexer.collection, "points": 1}


def test_embedding_text_adds_source_context():
    chunk = documents.Chunk("Entrega em outubro.", 5, {"heading": "Decisões > Prazos"})
    assert embedding_text({"filename": "decisoes.md"}, chunk) == "decisoes.md > Decisões > Prazos\n\nEntrega em outubro."
    code = documents.Chunk("def charge(): ...", 5, {"symbols": ["Billing.charge"]})
    assert embedding_text({"filename": "billing.py"}, code).startswith("billing.py > Billing.charge\n\n")


def test_collection_name_is_per_model():
    local = Settings(embedding_provider="local", local_embedding_model="intfloat/multilingual-e5-base")
    openai = Settings(embedding_provider="openai", openai_embedding_model="text-embedding-3-small")
    assert collection_name(local) == "vibecontext__local_intfloat_multilingual_e5_base"
    assert collection_name(openai) == "vibecontext__openai_text_embedding_3_small"


@pytest.mark.parametrize(
    "model, expected",
    [
        ("intfloat/multilingual-e5-base", ("query: ", "passage: ")),
        ("intfloat/e5-large-v2", ("query: ", "passage: ")),
        ("BAAI/bge-m3", (None, None)),
        ("sentence-transformers/paraphrase-multilingual-mpnet-base-v2", (None, None)),
    ],
)
def test_e5_models_get_their_prefixes(model, expected):
    assert prompts_for(model) == expected


def test_missing_openai_key_is_retry_later_with_instructions():
    with pytest.raises(RetryLater, match="OPENAI_API_KEY is not set"):
        OpenAIEmbedder("", "text-embedding-3-small", 64).embed_documents(["hi"])


def payload_of(app, doc_id):
    return {k: v for k, v in points(app, doc_id)[0].payload.items() if k in ("global", "sessions")}


def test_attach_and_detach_rewrite_the_payload_without_reembedding(client, auth, app, paths):
    add_session(paths, "s1")
    document = upload(client, auth, "a.md", b"# A\n\ntext", scope="library")
    app.state.worker.run_once()
    assert payload_of(app, document["id"]) == {"global": False, "sessions": []}
    calls = app.state.indexer.embedder.calls

    url = f"/api/documents/{document['id']}/attachments"
    client.post(url, headers=auth, json={"scope": "session", "session_id": "s1"})
    client.post(url, headers=auth, json={"scope": "global"})
    assert payload_of(app, document["id"]) == {"global": True, "sessions": ["s1"]}
    client.request("DELETE", url, headers=auth, params={"scope": "global"})
    assert payload_of(app, document["id"]) == {"global": False, "sessions": ["s1"]}
    assert app.state.indexer.embedder.calls == calls


def test_resync_repairs_payloads_written_while_qdrant_was_down(client, auth, app, paths):
    document = upload(client, auth, "a.md", b"# A\n\ntext", scope="library")
    app.state.worker.run_once()
    conn = store.connect(paths.db)
    documents.attach(conn, document["id"], "global")  # SQLite changed, Qdrant never told
    conn.close()
    assert payload_of(app, document["id"])["global"] is False
    assert app.state.worker.resync_targets() is True
    assert payload_of(app, document["id"])["global"] is True

import datetime

import pytest

from tests.helpers import make_docx, make_pdf
from vibecontext.db import documents, store


@pytest.fixture
def session(paths):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES ('s1', '/repo', 'open', ?, ?)",
            (ts, ts),
        )
    conn.close()
    return "s1"


def upload(client, auth, name, content, **form):
    form.setdefault("scope", "global")
    return client.post("/api/documents", headers=auth, data=form, files={"file": (name, content)})


def stored_files(paths):
    return sorted(p.name for p in paths.files.iterdir())


def test_upload_index_and_preview(client, auth, app, paths):
    response = upload(client, auth, "notes.md", b"# Billing\n\nCustomers pay monthly.\n\nRefunds take 5 days.")
    assert response.status_code == 201
    document = response.json()["document"]
    assert document["status"] == "pending"
    assert "stored_path" not in document
    assert stored_files(paths) == [document["id"] + ".md"]

    assert app.state.worker.run_once() == "done"
    assert app.state.worker.run_once() == "idle"

    indexed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert indexed["status"] == "indexed"
    assert indexed["chunk_count"] == 1
    chunks = client.get(f"/api/documents/{document['id']}/chunks", headers=auth).json()["chunks"]
    assert chunks[0]["meta"] == {"heading": "Billing"}
    assert "Refunds take 5 days." in chunks[0]["text"]


@pytest.mark.parametrize(
    "name, content, expected_chunks",
    [
        ("spec.pdf", make_pdf(["Page one.", "Page two."]), 1),
        ("ata.docx", make_docx(), 1),
        ("billing.py", b"def charge():\n    return 1\n", 1),
    ],
)
def test_each_kind_indexes(client, auth, app, name, content, expected_chunks):
    document = upload(client, auth, name, content).json()["document"]
    app.state.worker.run_once()
    indexed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert (indexed["status"], indexed["chunk_count"]) == ("indexed", expected_chunks)


def test_path_traversal_name_is_only_a_label(client, auth, paths):
    document = upload(client, auth, "../../../etc/passwd.md", b"hello").json()["document"]
    assert document["filename"] == "passwd.md"
    assert stored_files(paths) == [document["id"] + ".md"]
    assert not (paths.root.parent / "etc").exists()


def test_duplicate_upload_returns_existing_document(client, auth, paths):
    first = upload(client, auth, "a.txt", b"same content").json()
    second = upload(client, auth, "b.txt", b"same content")
    assert second.status_code == 200
    assert second.json() == {"document": first["document"], "duplicate": True}
    assert len(stored_files(paths)) == 1


def test_same_file_in_session_and_global_is_not_a_duplicate(client, auth, session):
    assert upload(client, auth, "a.txt", b"shared").status_code == 201
    assert upload(client, auth, "a.txt", b"shared", scope="session", session_id=session).status_code == 201


def test_scope_validation(client, auth, session):
    assert upload(client, auth, "a.txt", b"x", scope="session").status_code == 422
    assert upload(client, auth, "a.txt", b"x", scope="session", session_id="nope").status_code == 404
    assert upload(client, auth, "a.txt", b"x", scope="global", session_id=session).status_code == 422
    assert upload(client, auth, "a.txt", b"x", scope="team").status_code == 422


def test_rejects_unsupported_disguised_and_empty_files(client, auth, paths):
    assert upload(client, auth, "tool.exe", b"MZ").status_code == 415
    disguised = upload(client, auth, "report.pdf", b"MZ\x90\x00 not a pdf")
    assert disguised.status_code == 415
    assert "not a PDF" in disguised.json()["detail"]
    assert upload(client, auth, "empty.txt", b"").status_code == 422
    assert stored_files(paths) == []


def test_upload_requires_token_and_size_limit(client, auth, paths, app):
    assert client.post("/api/documents", files={"file": ("a.txt", b"x")}, data={"scope": "global"}).status_code == 401
    big = b"x" * (app.state.settings.max_upload_mb * 1024 * 1024 + 200 * 1024)
    assert upload(client, auth, "big.txt", big).status_code == 413
    assert stored_files(paths) == []


def test_corrupted_file_fails_with_readable_error(client, auth, app, paths):
    document = upload(client, auth, "broken.pdf", b"%PDF-1.4\nnot really a pdf").json()["document"]
    app.state.worker.run_once()
    failed = client.get(f"/api/documents/{document['id']}", headers=auth).json()
    assert failed["status"] == "failed"
    assert "corrupted or unreadable" in failed["error"]
    assert "broken.pdf" in (paths.logs / "ingest.log").read_text()


def test_delete_removes_file_row_and_chunks(client, auth, app, paths):
    document = upload(client, auth, "a.md", b"# A\n\ntext").json()["document"]
    app.state.worker.run_once()
    assert client.delete(f"/api/documents/{document['id']}", headers=auth).status_code == 204
    assert client.get(f"/api/documents/{document['id']}", headers=auth).status_code == 404
    assert stored_files(paths) == []
    conn = store.connect(paths.db)
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    conn.close()
    assert client.delete(f"/api/documents/{document['id']}", headers=auth).status_code == 404


def test_reindex_requeues_and_replaces_chunks(client, auth, app):
    document = upload(client, auth, "a.md", b"# A\n\ntext").json()["document"]
    app.state.worker.run_once()
    first = client.get(f"/api/documents/{document['id']}/chunks", headers=auth).json()["chunks"]
    response = client.post(f"/api/documents/{document['id']}/reindex", headers=auth)
    assert (response.status_code, response.json()["status"]) == (202, "pending")
    app.state.worker.run_once()
    second = client.get(f"/api/documents/{document['id']}/chunks", headers=auth).json()["chunks"]
    assert len(second) == len(first)
    assert second[0]["id"] != first[0]["id"]


def test_result_is_discarded_when_document_is_deleted_mid_processing(client, auth, paths):
    document = upload(client, auth, "a.txt", b"text").json()["document"]
    conn = store.connect(paths.db)
    job = documents.claim_next(conn)
    documents.delete_document(conn, job["id"])
    assert documents.complete(conn, job["id"], [documents.Chunk("text", 1)]) is False
    assert conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0] == 0
    conn.close()


def test_worker_requeues_interrupted_documents(client, auth, paths, app):
    document = upload(client, auth, "a.txt", b"text").json()["document"]
    conn = store.connect(paths.db)
    documents.claim_next(conn)
    conn.close()
    app.state.worker.start()
    app.state.worker.stop()
    # start() requeued it, and the thread may already have processed it.
    status = client.get(f"/api/documents/{document['id']}", headers=auth).json()["status"]
    assert status in ("pending", "indexed")


def test_list_filters(client, auth, app, session):
    upload(client, auth, "g.txt", b"global doc")
    upload(client, auth, "s.txt", b"session doc", scope="session", session_id=session)
    names = lambda **params: [
        d["filename"] for d in client.get("/api/documents", headers=auth, params=params).json()["documents"]
    ]
    assert names(scope="global") == ["g.txt"]
    assert names(session_id=session) == ["s.txt"]
    assert names(status="indexed") == []
    assert client.get("/api/status", headers=auth).json()["documents"]["pending"] == 2


def test_second_worker_for_same_home_refuses_to_start(paths, app):
    from vibecontext.ingest.worker import IngestWorker

    app.state.worker.start()
    try:
        with pytest.raises(RuntimeError, match="already running"):
            IngestWorker(paths, app.state.settings, app.state.indexer).start()
    finally:
        app.state.worker.stop()
    # Released on stop, so a restart works.
    replacement = IngestWorker(paths, app.state.settings, app.state.indexer)
    replacement.start()
    replacement.stop()

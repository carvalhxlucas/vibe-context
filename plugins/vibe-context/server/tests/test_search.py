import datetime
import json
import socket
import subprocess
import sys
import threading
import time

import pytest
import uvicorn
from fastapi.testclient import TestClient

from tests.fakes import FakeReranker, fake_indexer
from vibecontext.api.app import create_app
from vibecontext.config import PLUGIN_ROOT, load_settings
from vibecontext.db import store
from vibecontext.retrieval import injection
from vibecontext.retrieval.search import SearchResponse

HOOK = PLUGIN_ROOT / "hooks" / "scripts" / "session_hook.py"


def add_session(paths, session_id):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES (?, '/repo', 'open', ?, ?)",
            (session_id, ts, ts),
        )
    conn.close()


def index(client, auth, app, name, content, **form):
    form.setdefault("scope", "global")
    document = client.post(
        "/api/documents", headers=auth, data=form, files={"file": (name, content)}
    ).json()["document"]
    app.state.worker.run_once()
    return document


def search(client, auth, query, **body):
    return client.post("/api/search", headers=auth, json={"query": query, **body})


@pytest.fixture
def corpus(client, auth, app, paths):
    add_session(paths, "s1")
    add_session(paths, "s2")
    index(client, auth, app, "prazos.md", "# Prazos\n\nA entrega do projeto acontece em outubro.".encode())
    index(client, auth, app, "billing.md", b"# Billing\n\nRefunds are processed within five business days.")
    index(client, auth, app, "s1.md", b"# Session one\n\nThe refunds policy for session one.", scope="session", session_id="s1")
    index(client, auth, app, "s2.md", b"# Session two\n\nThe refunds policy for session two.", scope="session", session_id="s2")


def filenames(response):
    return [r["filename"] for r in response.json()["results"]]


def test_scope_isolation(client, auth, corpus):
    assert set(filenames(search(client, auth, "refunds policy session", top_k=20))) == {"prazos.md", "billing.md"}
    with_s1 = set(filenames(search(client, auth, "refunds policy session", session_id="s1", top_k=20)))
    assert with_s1 == {"prazos.md", "billing.md", "s1.md"}


def test_reranker_orders_results(client, auth, corpus):
    body = search(client, auth, "quando acontece a entrega do projeto").json()
    assert body["reranked"] is True
    assert body["results"][0]["filename"] == "prazos.md"
    assert body["results"][0]["location"] == {"heading": "Prazos"}
    scores = [r["score"] for r in body["results"]]
    assert scores == sorted(scores, reverse=True)
    assert all(0 <= s <= 1 for s in scores)


def test_top_k_limits_results(client, auth, corpus):
    assert len(search(client, auth, "refunds", top_k=1).json()["results"]) == 1


def test_reranker_still_loading_falls_back_to_hybrid(client, auth, app, corpus):
    reranker = app.state.searcher._reranker
    reranker.ready = False
    body = search(client, auth, "refunds").json()
    assert body["reranked"] is False
    assert body["results"]
    assert "still loading" in body["notes"][0]
    assert reranker.load_requests == 1


def test_reranker_failure_falls_back_to_hybrid(client, auth, app, corpus):
    app.state.searcher._reranker.broken = "Cohere rerank failed: 503"
    body = search(client, auth, "refunds").json()
    assert (body["reranked"], bool(body["results"])) == (False, True)
    assert body["notes"] == ["Cohere rerank failed: 503. Results use the hybrid ranking only."]


def test_without_reranker(paths, auth, monkeypatch):
    from vibecontext.ingest import resources

    monkeypatch.setattr(resources, "configure", lambda paths: None)
    app = create_app(paths, indexer=fake_indexer(load_settings(paths)), reranker=None)
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    index(client, auth, app, "billing.md", b"# Billing\n\nRefunds take five days.")
    body = search(client, auth, "refunds").json()
    assert (body["reranked"], body["notes"], len(body["results"])) == (False, [], 1)


def test_orphan_points_are_dropped(client, auth, app, paths, corpus):
    conn = store.connect(paths.db)
    with conn:
        conn.execute("DELETE FROM chunks WHERE document_id = (SELECT id FROM documents WHERE filename = 'billing.md')")
    conn.close()
    assert "billing.md" not in filenames(search(client, auth, "refunds processed business days", top_k=20))


def test_empty_index(client, auth):
    body = search(client, auth, "anything").json()
    assert body["results"] == []
    assert "No indexed document matched" in body["notes"][0]


def test_embedding_unavailable_is_503(client, auth, app, corpus):
    app.state.indexer.embedder.unavailable = "OPENAI_API_KEY is not set."
    response = search(client, auth, "refunds")
    assert (response.status_code, response.json()["detail"]) == (503, "OPENAI_API_KEY is not set.")


@pytest.mark.parametrize("body", [{"query": ""}, {"query": "x", "top_k": 0}, {"query": "x", "top_k": 21}])
def test_search_validation(client, auth, body):
    assert client.post("/api/search", headers=auth, json=body).status_code == 422


def test_search_requires_token(client):
    assert client.post("/api/search", json={"query": "x"}).status_code == 401


def test_inject_is_disabled_by_default(client, auth, corpus):
    body = client.post("/api/inject", headers=auth, json={"prompt": "quando acontece a entrega do projeto?"}).json()
    assert body == {"context": None, "reason": "disabled"}


@pytest.fixture
def inject_enabled(paths):
    with paths.env.open("a") as f:
        f.write("VIBECONTEXT_AUTO_INJECT=true\n")


def test_inject_builds_context_above_min_score(inject_enabled, client, auth, corpus):
    body = client.post(
        "/api/inject", headers=auth, json={"prompt": "quando acontece a entrega do projeto?", "session_id": "s1"}
    ).json()
    assert body["context"].startswith(injection.HEADER)
    assert "--- prazos.md (Prazos), score" in body["context"]
    assert "A entrega do projeto acontece em outubro." in body["context"]
    assert "billing.md" not in body["context"]

    short = client.post("/api/inject", headers=auth, json={"prompt": "ok, continue"}).json()
    assert short == {"context": None, "reason": "prompt too short"}


def test_inject_skips_unreranked_results():
    response = SearchResponse([{"filename": "a.md", "location": {}, "score": 0.9, "text": "x"}], reranked=False)
    assert injection.build_context(response, min_score=0.5, top_k=3) is None


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def fire_prompt(paths, prompt):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/repo", "prompt": prompt}),
        capture_output=True, text=True, env={"VIBECONTEXT_HOME": str(paths.root)}, check=True,
    )


def test_hook_injects_from_running_backend(inject_enabled, paths, auth, monkeypatch):
    from vibecontext.ingest import resources

    port = _free_port()
    with paths.env.open("a") as f:
        f.write(f"VIBECONTEXT_PORT={port}\n")
    monkeypatch.setattr(resources, "configure", lambda paths: None)
    app = create_app(paths, indexer=fake_indexer(load_settings(paths)), reranker=FakeReranker())
    client = TestClient(app, base_url="http://127.0.0.1:8765")
    index(client, auth, app, "prazos.md", "# Prazos\n\nA entrega do projeto acontece em outubro.".encode())

    # No backend listening yet: the hook stays silent and logs why.
    silent = fire_prompt(paths, "quando acontece a entrega do projeto?")
    assert silent.stdout == ""
    assert "auto-inject skipped" in (paths.logs / "hooks.log").read_text()

    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        while not server.started:
            time.sleep(0.05)
        output = json.loads(fire_prompt(paths, "quando acontece a entrega do projeto?").stdout)
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "A entrega do projeto acontece em outubro." in context

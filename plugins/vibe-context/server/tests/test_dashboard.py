import datetime

import pytest

from vibecontext.db import store

HX = {"HX-Request": "true"}


def add_session(paths, session_id="s1"):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES (?, '/work/app', 'open', ?, ?)",
            (session_id, ts, ts),
        )
    conn.close()


def login(client, auth):
    path = client.post("/api/dashboard/login-code", headers=auth).json()["path"]
    return client.get(path, follow_redirects=False)


@pytest.fixture
def dash(client, auth):
    assert login(client, auth).status_code == 303
    return client


def upload(dash, name, content, **form):
    form.setdefault("scope", "global")
    return dash.post("/ui/upload", headers=HX, data=form, files=[("files", (name, content))])


def test_login_sets_strict_httponly_cookie_and_hides_code(client, auth):
    response = login(client, auth)
    assert response.headers["location"] == "/sessions"
    cookie = response.headers["set-cookie"].lower()
    assert "vibecontext_dashboard=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert response.headers["referrer-policy"] == "no-referrer"


def test_login_code_is_single_use_and_needs_bearer(client, auth):
    assert client.post("/api/dashboard/login-code").status_code == 401
    path = client.post("/api/dashboard/login-code", headers=auth).json()["path"]
    assert client.get(path, follow_redirects=False).status_code == 303
    client.cookies.clear()
    reused = client.get(path, follow_redirects=False)
    assert reused.status_code == 401
    assert "invalid, expired or already used" in reused.text
    assert client.get("/login?code=made-up", follow_redirects=False).status_code == 401


@pytest.mark.parametrize("path", ["/sessions", "/global", "/library", "/ui/documents", "/ui/status", "/"])
def test_dashboard_requires_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 401
    assert "/vibe-context:dashboard" in response.text


def test_htmx_request_without_login_asks_for_reload(client):
    assert client.get("/ui/status", headers=HX).headers["hx-refresh"] == "true"


def test_cookie_does_not_open_the_api(dash):
    assert dash.get("/api/sessions").status_code == 401


def test_static_assets_are_public(client):
    assert client.get("/static/htmx.min.js").status_code == 200
    assert "text/css" in client.get("/static/style.css").headers["content-type"]


@pytest.mark.parametrize("path", ["/sessions", "/global", "/library"])
def test_pages_render_with_security_headers(dash, path):
    response = dash.get(path)
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe" not in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"


def test_changes_without_htmx_header_are_refused(dash):
    response = dash.post("/ui/upload", data={"scope": "global"}, files=[("files", ("a.md", b"# A"))])
    assert response.status_code == 403
    assert dash.delete("/ui/documents/whatever").status_code == 403


def test_upload_queues_and_signals_table(dash, app):
    response = upload(dash, "spec.md", b"# Spec\n\nBilling is monthly.")
    assert response.status_code == 200
    assert "spec.md: queued for indexing" in response.text
    assert response.headers["hx-trigger"] == "documents-changed"

    table = dash.get("/ui/documents?scope=global").text
    assert "spec.md" in table and "load delay:3s" in table  # polls while queued
    app.state.worker.run_once()
    table = dash.get("/ui/documents?scope=global").text
    assert "doc-indexed" in table and "load delay:3s" not in table


def test_upload_reports_rejections_per_file(dash):
    response = dash.post(
        "/ui/upload", headers=HX, data={"scope": "global"},
        files=[("files", ("ok.md", b"# ok")), ("files", ("virus.exe", b"MZ")), ("files", ("ok.md", b"# ok"))],
    )
    assert "ok.md: queued for indexing" in response.text
    assert "virus.exe: Unsupported file type .exe" in response.text
    assert "ok.md: already added" in response.text


def test_filenames_and_chunks_are_escaped(dash, app):
    upload(dash, "<svg onload=alert(1)>.md", b"# T\n\n<img src=x onerror=alert(2)>")
    app.state.worker.run_once()
    table = dash.get("/ui/documents").text
    assert "<svg onload" not in table
    assert "&lt;svg onload=alert(1)&gt;.md" in table
    doc_id = table.split("/ui/documents/")[1].split("/")[0]
    chunks = dash.get(f"/ui/documents/{doc_id}/chunks").text
    assert "<img src=x" not in chunks and "&lt;img src=x onerror=alert(2)&gt;" in chunks


def test_session_page_upload_and_chunk_preview(dash, app, paths):
    add_session(paths)
    page = dash.get("/sessions/s1")
    assert page.status_code == 200 and "/work/app" in page.text
    assert 'name="session_id" value="s1"' in page.text
    upload(dash, "ata.md", b"# Ata\n\nDecidimos usar Qdrant.", scope="session", session_id="s1")
    app.state.worker.run_once()

    sessions = dash.get("/sessions").text
    assert "/sessions/s1" in sessions
    table = dash.get("/ui/documents?session_id=s1").text
    doc_id = table.split("/ui/documents/")[1].split("/")[0]
    preview = dash.get(f"/ui/documents/{doc_id}/chunks").text
    assert "Decidimos usar Qdrant." in preview and "chunks-row open" in preview
    assert 'class="chunks-row"' in dash.get(f"/ui/documents/{doc_id}/chunks?close=true").text
    assert dash.get("/sessions/unknown").status_code == 404


def test_reindex_and_delete_from_library(dash, app):
    upload(dash, "a.md", b"# A\n\ntext")
    app.state.worker.run_once()
    doc_id = dash.get("/ui/documents").text.split("/ui/documents/")[1].split("/")[0]
    reindexed = dash.post(f"/ui/documents/{doc_id}/reindex?scope=global", headers=HX)
    assert "doc-pending" in reindexed.text
    deleted = dash.delete(f"/ui/documents/{doc_id}?scope=global", headers=HX)
    assert "a.md" not in deleted.text and "No files here yet." in deleted.text


def test_search_and_status_fragments(dash, app):
    upload(dash, "prazos.md", "# Prazos\n\nA entrega acontece em outubro.".encode())
    app.state.worker.run_once()
    results = dash.post("/ui/search", headers=HX, data={"query": "quando acontece a entrega"}).text
    assert "prazos.md" in results and "A entrega acontece em outubro." in results
    assert "Type a question." in dash.post("/ui/search", headers=HX, data={"query": " "}).text
    status = dash.get("/ui/status").text
    assert "1 chunks indexed" in status and "fake/hash-embedder" in status and "auto-inject off" in status

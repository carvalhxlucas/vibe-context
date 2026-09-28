import datetime
import json

import pytest

from vibecontext.db import documents, store

HX = {"HX-Request": "true"}


def add_session(paths, session_id="s1", cwd="/work/atlas-api"):
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES (?, ?, 'open', ?, ?)",
            (session_id, cwd, ts, ts),
        )
    conn.close()


def login(client, auth):
    path = client.post("/api/dashboard/login-code", headers=auth).json()["path"]
    return client.get(path, follow_redirects=False)


@pytest.fixture
def dash(client, auth):
    assert login(client, auth).status_code == 303
    return client


def upload(dash, target, *files):
    return dash.post("/ui/upload", headers=HX, data={"target": target}, files=[("files", f) for f in files])


def doc_id(paths, filename):
    conn = store.connect(paths.db)
    try:
        return conn.execute("SELECT id FROM documents WHERE filename = ?", (filename,)).fetchone()["id"]
    finally:
        conn.close()


def attachments(paths, filename):
    conn = store.connect(paths.db)
    try:
        return documents.get_document(conn, doc_id(paths, filename))["attachments"]
    finally:
        conn.close()


def events(response):
    return json.loads(response.headers["hx-trigger"])


# --- login and protection ------------------------------------------------------


def test_login_sets_strict_httponly_cookie_and_hides_code(client, auth):
    response = login(client, auth)
    assert response.headers["location"] == "/sessions"
    cookie = response.headers["set-cookie"].lower()
    assert "vibecontext_dashboard=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert response.headers["referrer-policy"] == "no-referrer"


def test_login_code_is_single_use_and_needs_bearer(client, auth):
    assert client.post("/api/dashboard/login-code").status_code == 401
    path = client.post("/api/dashboard/login-code", headers=auth).json()["path"]
    assert client.get(path, follow_redirects=False).status_code == 303
    client.cookies.clear()
    reused = client.get(path, follow_redirects=False)
    assert reused.status_code == 401
    assert "inválido, expirou ou já foi usado" in reused.text


@pytest.mark.parametrize("path", ["/sessions", "/global", "/library", "/ui/status", "/ui/library/table", "/"])
def test_dashboard_requires_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 401
    assert "/vibe-context:dashboard" in response.text


def test_htmx_request_without_login_asks_for_reload(client):
    assert client.get("/ui/status", headers=HX).headers["hx-refresh"] == "true"


def test_cookie_does_not_open_the_api(dash):
    assert dash.get("/api/sessions").status_code == 401


def test_static_assets_are_public(client):
    for path in ("/static/htmx.min.js", "/static/app.js", "/static/style.css", "/static/fonts/Geist-Variable.woff2"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", ["/sessions", "/global", "/library"])
def test_pages_render_under_strict_csp_without_inline_styles(dash, paths, path):
    add_session(paths)
    response = dash.get(path)
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "style-src 'self'" in csp and "unsafe" not in csp
    # An inline style attribute would be blocked by that CSP and silently break the layout.
    assert ' style="' not in response.text
    assert "<script>" not in response.text


def test_changes_without_htmx_header_are_refused(dash):
    assert dash.post("/ui/upload", data={"target": "global"}, files=[("files", ("a.md", b"# A"))]).status_code == 403
    assert dash.post("/ui/attach", data={"doc_id": "x", "target": "global"}).status_code == 403
    assert dash.delete("/ui/documents/whatever").status_code == 403


# --- sessions --------------------------------------------------------------------


def test_sessions_page_shows_session_and_brain_spec(dash, paths, app):
    add_session(paths, "s1", "/work/atlas-api")
    upload(dash, "s1", ("ata.md", b"# Ata\n\nDecidimos usar Qdrant."))
    upload(dash, "global", ("spec.md", b"# Spec\n\nBilling is monthly."))
    app.state.worker.run_once()
    app.state.worker.run_once()

    page = dash.get("/sessions").text
    assert "atlas-api" in page and "Desta sessão" in page and "Herdado do global" in page
    assert "ata.md" in page and "spec.md" in page
    spec = page.split('data-spec="')[1].split('"')[0].replace("&#34;", '"')
    parsed = json.loads(spec)
    assert {s["label"] for s in parsed["segs"]} == {"Global", "atlas-api"}
    assert parsed["tokens"] > 0
    assert dash.get("/sessions?s=unknown").status_code == 404


def test_upload_to_session_attaches_and_feeds(dash, paths):
    add_session(paths)
    response = upload(dash, "s1", ("ata.md", b"# Ata\n\ntext"))
    body = response.json()
    assert body["messages"] == [{"kind": "ok", "text": "ata.md: na fila de indexação"}]
    assert events(response) == {"vc-changed": True, "vc-toast": body["toast"], "vc-fed": True}
    assert attachments(paths, "ata.md") == {"global": False, "sessions": ["s1"]}


def test_upload_reports_rejections_and_dedups(dash, paths):
    response = upload(dash, "global", ("ok.md", b"# ok"), ("virus.exe", b"MZ"), ("copy.md", b"# ok"))
    texts = [m["text"] for m in response.json()["messages"]]
    assert "ok.md: na fila de indexação" in texts
    assert any(t.startswith("virus.exe: Unsupported file type .exe") for t in texts)
    assert "ok.md: já estava na biblioteca" in texts
    assert "1 recusado" in response.json()["toast"]


def test_session_panel_polls_while_indexing(dash, paths, app):
    add_session(paths)
    upload(dash, "s1", ("ata.md", b"# Ata\n\ntext"))
    busy = dash.get("/ui/sessions/s1/panel").text
    assert "load delay:3s" in busy and 'id="ctx-overlay"' in busy and 'hx-swap-oob="true"' in busy
    assert "INDEXANDO" in busy
    app.state.worker.run_once()
    idle = dash.get("/ui/sessions/s1/panel").text
    assert "load delay:3s" not in idle and "SINCRONIZADO" in idle


def test_detach_from_session_keeps_the_file_in_the_library(dash, paths):
    add_session(paths)
    upload(dash, "s1", ("ata.md", b"# Ata\n\ntext"))
    response = dash.post("/ui/detach", headers=HX, data={"doc_id": doc_id(paths, "ata.md"), "target": "s1"})
    assert events(response)["vc-toast"] == "ata.md desanexado de atlas-api"
    assert attachments(paths, "ata.md") == {"global": False, "sessions": []}
    assert "ata.md" in dash.get("/ui/library/table").text


# --- global, library and picker ----------------------------------------------------


def test_library_import_attaches_nothing(dash, paths):
    upload(dash, "library", ("ref.md", b"# Ref"))
    assert attachments(paths, "ref.md") == {"global": False, "sessions": []}
    assert "ref.md" in dash.get("/library").text


def test_picker_attaches_and_toggles(dash, paths):
    add_session(paths)
    upload(dash, "library", ("ref.md", b"# Ref"))
    picker = dash.get("/ui/picker?target=s1").text
    assert "atlas-api" in picker and "ref.md" in picker and ">Anexar<" in picker

    response = dash.post("/ui/attach", headers=HX, data={"doc_id": doc_id(paths, "ref.md"), "target": "s1"})
    assert events(response)["vc-fed"] is True
    assert attachments(paths, "ref.md")["sessions"] == ["s1"]
    assert ">Anexado<" in dash.get("/ui/picker?target=s1&rows_only=true").text
    assert "ref.md" not in dash.get("/ui/picker?target=s1&rows_only=true&q=zzz").text
    assert dash.get("/ui/picker?target=nope").status_code == 404


def test_library_menu_lists_targets_with_checks(dash, paths, app):
    add_session(paths)
    upload(dash, "global", ("spec.md", b"# Spec\n\ntext"))
    app.state.worker.run_once()
    table = dash.get("/ui/library/table").text
    assert "Contexto global" in table and "atlas-api" in table
    assert "hx-post=\"/ui/detach\"" in table  # global is checked, so clicking it detaches
    assert "Ver chunks" in table and "Excluir da biblioteca" in table


def test_library_filters(dash, paths):
    upload(dash, "library", ("guide.md", b"# Guide"), ("billing.py", b"def charge():\n    return 1\n"),
           ("openapi.yaml", b"openapi: 3.1.0\n"))
    by_kind = lambda kind: dash.get(f"/ui/library/table?kind={kind}").text
    assert "billing.py" in by_kind("code") and "guide.md" not in by_kind("code")
    assert "openapi.yaml" in by_kind("spec") and "billing.py" not in by_kind("spec")
    assert "guide.md" in by_kind("doc")
    named = dash.get("/ui/library/table?q=bill").text
    assert "billing.py" in named and "guide.md" not in named


def test_delete_and_reindex_from_library(dash, paths, app):
    upload(dash, "global", ("a.md", b"# A\n\ntext"))
    app.state.worker.run_once()
    target = doc_id(paths, "a.md")
    assert events(dash.post(f"/ui/documents/{target}/reindex", headers=HX))["vc-toast"] == "Reindexação na fila"
    assert events(dash.delete(f"/ui/documents/{target}", headers=HX))["vc-toast"] == "a.md removido da biblioteca"
    assert "a.md" not in dash.get("/ui/library/table").text


def test_global_page(dash, paths, app):
    add_session(paths)
    upload(dash, "global", ("spec.md", b"# Spec\n\ntext"))
    app.state.worker.run_once()
    page = dash.get("/global").text
    assert "Contexto global" in page and "spec.md" in page and "Sessões conectadas" in page
    assert 'data-drop-target="global"' in page
    assert "injetados" not in page  # VibeContext searches; it never loads whole files into context


def test_names_and_text_are_escaped(dash, paths, app):
    upload(dash, "global", ("<svg onload=alert(1)>.md", b"# T\n\n<img src=x onerror=alert(2)>"))
    app.state.worker.run_once()
    table = dash.get("/ui/library/table").text
    assert "<svg onload" not in table and "&lt;svg onload=alert(1)&gt;.md" in table
    chunks = dash.get(f"/ui/documents/{doc_id(paths, '<svg onload=alert(1)>.md')}/chunks").text
    assert "<img src=x" not in chunks and "&lt;img src=x onerror=alert(2)&gt;" in chunks


def test_search_modal_and_status(dash, paths, app):
    upload(dash, "global", ("prazos.md", "# Prazos\n\nA entrega acontece em outubro.".encode()))
    app.state.worker.run_once()
    assert "Testar busca" in dash.get("/ui/search").text
    results = dash.post("/ui/search", headers=HX, data={"query": "quando acontece a entrega"}).text
    assert "prazos.md" in results and "A entrega acontece em outubro." in results
    status = dash.get("/ui/status").text
    assert "Backend local" in status and "1 arquivos · 1 chunks" in status

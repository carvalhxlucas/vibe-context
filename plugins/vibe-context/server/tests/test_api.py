import datetime

from vibecontext.db import store


def add_session(paths, session_id, status="open", hours_idle=0):
    ts = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=hours_idle)).isoformat(timespec="seconds")
    conn = store.connect(paths.db)
    with conn:
        conn.execute(
            "INSERT INTO sessions (id, cwd, status, started_at, last_activity_at) VALUES (?, '/repo', ?, ?, ?)",
            (session_id, status, ts, ts),
        )
    conn.close()


def test_health_needs_no_token(client):
    assert client.get("/health").json() == {"status": "ok", "service": "vibecontext"}


def test_api_rejects_missing_and_wrong_token(client):
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/sessions", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert client.get("/api/status").status_code == 401


def test_rejects_foreign_host_header(client, auth):
    response = client.get("/api/sessions", headers={**auth, "Host": "evil.example:8765"})
    assert response.status_code == 400


def test_no_cors_headers(client, auth):
    response = client.get("/api/sessions", headers={**auth, "Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in response.headers


def test_openapi_docs_disabled(client, auth):
    # Unknown non-API paths fall under the dashboard login, then 404.
    assert client.get("/docs").status_code == 401
    assert client.get("/api/openapi.json", headers=auth).status_code == 404


def test_lists_sessions_with_stale_status(client, auth, paths):
    add_session(paths, "live")
    add_session(paths, "crashed", hours_idle=48)
    add_session(paths, "done", status="ended")

    everything = client.get("/api/sessions", headers=auth).json()["sessions"]
    assert {s["id"]: s["status"] for s in everything} == {"live": "open", "crashed": "stale", "done": "ended"}

    open_only = client.get("/api/sessions", params={"status": "open"}, headers=auth).json()["sessions"]
    assert [s["id"] for s in open_only] == ["live"]

    stale_only = client.get("/api/sessions", params={"status": "stale"}, headers=auth).json()["sessions"]
    assert [s["id"] for s in stale_only] == ["crashed"]


def test_get_session(client, auth, paths):
    add_session(paths, "s1")
    assert client.get("/api/sessions/s1", headers=auth).json()["id"] == "s1"
    assert client.get("/api/sessions/missing", headers=auth).status_code == 404


def test_status_counts_sessions(client, auth, paths):
    add_session(paths, "live")
    add_session(paths, "done", status="ended")
    body = client.get("/api/status", headers=auth).json()
    assert body["sessions"] == {"open": 1, "stale": 0, "ended": 1}
    assert "reachable" in body["qdrant"]

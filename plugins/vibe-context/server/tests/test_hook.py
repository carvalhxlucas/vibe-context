import json
import subprocess
import sys

from vibecontext.config import PLUGIN_ROOT
from vibecontext.db import store

HOOK = PLUGIN_ROOT / "hooks" / "scripts" / "session_hook.py"


def fire(paths, event):
    return subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(event) if isinstance(event, dict) else event,
        capture_output=True,
        text=True,
        env={"VIBECONTEXT_HOME": str(paths.root)},
        check=True,
    )


def session(paths, session_id):
    conn = store.connect(paths.db)
    try:
        return store.get_session(conn, session_id, stale_after_hours=12)
    finally:
        conn.close()


def test_session_lifecycle(paths):
    result = fire(paths, {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/repo", "source": "startup"})
    context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "s1" in context
    assert session(paths, "s1")["status"] == "open"

    fire(paths, {"hook_event_name": "UserPromptSubmit", "session_id": "s1", "cwd": "/repo", "prompt": "hi"})
    assert session(paths, "s1")["prompt_count"] == 1

    fire(paths, {"hook_event_name": "SessionEnd", "session_id": "s1", "reason": "prompt_input_exit"})
    ended = session(paths, "s1")
    assert ended["status"] == "ended"
    assert ended["end_reason"] == "prompt_input_exit"

    fire(paths, {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": "/repo", "source": "resume"})
    resumed = session(paths, "s1")
    assert resumed["status"] == "open"
    assert resumed["ended_at"] is None


def test_prompt_creates_session_started_before_install(paths):
    fire(paths, {"hook_event_name": "UserPromptSubmit", "session_id": "old", "cwd": "/repo", "prompt": "hi"})
    assert session(paths, "old")["status"] == "open"


def test_bad_input_never_fails(paths):
    result = fire(paths, "not json")
    assert result.returncode == 0
    assert result.stdout == ""
    assert "Traceback" in (paths.logs / "hooks.log").read_text()

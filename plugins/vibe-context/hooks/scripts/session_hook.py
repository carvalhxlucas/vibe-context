#!/usr/bin/env python3
"""Tracks Claude Code sessions in the VibeContext SQLite database.

Handles SessionStart, UserPromptSubmit and SessionEnd. Standard library only and
compatible with Python 3.9, because it runs on the system python3 before the
backend virtualenv exists. It writes straight to SQLite so session tracking keeps
working when the backend is down.

A hook failure must never block Claude Code: every error is logged and the script
exits 0.
"""

import datetime
import json
import os
import sqlite3
import sys
import traceback
from pathlib import Path

HOME = Path(os.environ.get("VIBECONTEXT_HOME", Path.home() / ".vibecontext"))
DB_PATH = HOME / "vibecontext.db"
LOG_PATH = HOME / "logs" / "hooks.log"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "server" / "vibecontext" / "db" / "schema.sql"


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")


def log(message):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write("{} {}\n".format(now(), message))


def connect():
    HOME.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=3)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def on_session_start(conn, event):
    ts = now()
    # A resumed session keeps its id, so reopen it instead of failing on the primary key.
    conn.execute(
        """
        INSERT INTO sessions (id, cwd, transcript_path, start_source, status, started_at, last_activity_at)
        VALUES (?, ?, ?, ?, 'open', ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            cwd = excluded.cwd,
            transcript_path = excluded.transcript_path,
            start_source = excluded.start_source,
            status = 'open',
            last_activity_at = excluded.last_activity_at,
            ended_at = NULL,
            end_reason = NULL
        """,
        (event["session_id"], event.get("cwd", ""), event.get("transcript_path"), event.get("source"), ts, ts),
    )
    # Claude has no other way to learn its own session id, and search_context needs it
    # to include the files attached to this session.
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": (
                "VibeContext session id: {}. Pass it as session_id to the vibecontext "
                "search_context tool to include documents attached to this session."
            ).format(event["session_id"]),
        }
    }))


def on_user_prompt_submit(conn, event):
    ts = now()
    # Sessions opened before the plugin was installed have no row yet.
    conn.execute(
        """
        INSERT INTO sessions (id, cwd, transcript_path, status, started_at, last_activity_at, prompt_count)
        VALUES (?, ?, ?, 'open', ?, ?, 1)
        ON CONFLICT (id) DO UPDATE SET
            status = 'open',
            last_activity_at = excluded.last_activity_at,
            prompt_count = sessions.prompt_count + 1
        """,
        (event["session_id"], event.get("cwd", ""), event.get("transcript_path"), ts, ts),
    )


def on_session_end(conn, event):
    ts = now()
    conn.execute(
        """
        UPDATE sessions
        SET status = 'ended', ended_at = ?, last_activity_at = ?, end_reason = ?
        WHERE id = ?
        """,
        (ts, ts, event.get("reason"), event["session_id"]),
    )


HANDLERS = {
    "SessionStart": on_session_start,
    "UserPromptSubmit": on_user_prompt_submit,
    "SessionEnd": on_session_end,
}


def main():
    try:
        event = json.load(sys.stdin)
        handler = HANDLERS.get(event.get("hook_event_name"))
        if handler is None or not event.get("session_id"):
            return
        conn = connect()
        try:
            with conn:
                handler(conn, event)
        finally:
            conn.close()
    except Exception:
        try:
            log(traceback.format_exc())
        except Exception:
            pass


if __name__ == "__main__":
    main()
    sys.exit(0)

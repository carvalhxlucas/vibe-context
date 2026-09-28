"""Turns sessions, the library and attachments into what the dashboard shows.

Colors, kinds and number formats follow the VibeContext App design.
"""

import datetime
import json
import sqlite3
from pathlib import Path

from vibecontext.db import documents, store

PALETTE = {
    "violet": (163, 147, 255),
    "blue": (111, 155, 255),
    "cyan": (92, 208, 230),
    "rose": (227, 139, 208),
    "green": (127, 211, 162),
}
SESSION_COLORS = ("blue", "cyan", "rose", "green")

# The design groups files into three kinds; structured data counts as a spec.
KINDS = {"code": ("Código", "blue"), "doc": ("Doc", "violet"), "spec": ("Spec", "cyan")}
SPEC_LANGUAGES = {"yaml", "json", "toml", "sql"}

# Only sizes the brain drawing: VibeContext searches documents, it does not load them
# into Claude's context window, so there is no budget to show.
BRAIN_REFERENCE_TOKENS = 200_000

STATUS_LABELS = {"open": "ativa", "stale": "ociosa", "ended": "encerrada"}
SESSIONS_SHOWN = 30


def fmt_tokens(n: int) -> str:
    if n >= 100_000:
        return f"{round(n / 1000)}k"
    if n >= 1000:
        return f"{n / 1000:.1f}".replace(".0", "").replace(".", ",") + "k"
    return str(n)


def fmt_size(size_bytes: int) -> str:
    for unit, size in (("MB", 1024 * 1024), ("KB", 1024)):
        if size_bytes >= size:
            return f"{size_bytes / size:.1f} {unit}".replace(".", ",")
    return f"{size_bytes} B"


def ago(timestamp: str | None) -> str:
    if not timestamp:
        return ""
    seconds = (datetime.datetime.now(datetime.timezone.utc) - datetime.datetime.fromisoformat(timestamp)).total_seconds()
    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return f"há {int(seconds // 60)} min"
    if seconds < 86400:
        return f"há {int(seconds // 3600)} h"
    if seconds < 2 * 86400:
        return "ontem"
    if seconds < 14 * 86400:
        return f"há {int(seconds // 86400)} d"
    return f"há {int(seconds // (7 * 86400))} sem"


def rgb(color: str) -> str:
    r, g, b = PALETTE[color]
    return f"rgb({r},{g},{b})"


def kind_of(document: dict) -> str:
    if document["kind"] != "code":
        return "doc"
    return "spec" if document["language"] in SPEC_LANGUAGES else "code"


def _home_relative(path: str) -> str:
    home = str(Path.home())
    return "~" + path[len(home):] if path.startswith(home) else path


def session_view(session: dict, own_tokens: int, global_tokens: int, selected_id: str | None, color: str) -> dict:
    cwd = session["cwd"] or ""
    total = own_tokens + global_tokens
    return {
        "id": session["id"],
        "name": Path(cwd).name or session["id"][:8],
        "repo": _home_relative(cwd) or "(diretório desconhecido)",
        "short_id": session["id"][:8],
        "status": session["status"],
        "status_label": STATUS_LABELS[session["status"]],
        "since": ago(session["started_at"]),
        "last_activity": ago(session["last_activity_at"]),
        "color": color,
        "own_tokens": own_tokens,
        "own_tokens_fmt": fmt_tokens(own_tokens),
        "tokens_fmt": fmt_tokens(total),
        "pct": f"{min(100.0, total / BRAIN_REFERENCE_TOKENS * 100):.1f}%",
        "selected": session["id"] == selected_id,
    }


def file_view(document: dict, sessions_by_id: dict[str, dict] | None = None) -> dict:
    kind = kind_of(document)
    label, color = KINDS[kind]
    detail = document["language"] or document["kind"]
    if document["status"] == "failed":
        state, note = "failed", f"falhou: {document['error']}"
    elif document["status"] in ("pending", "processing"):
        waiting = document["error"]  # an environment problem keeps it queued with the reason
        state, note = "waiting", (waiting or "indexando…")
    else:
        state, note = "indexed", f"{detail} · {fmt_size(document['size_bytes'])}"
    usage = []
    attachments = document["attachments"]
    if attachments["global"]:
        usage.append({"label": "global", "color": "violet"})
    for session_id in attachments["sessions"]:
        session = (sessions_by_id or {}).get(session_id)
        usage.append({
            "label": session["name"] if session else session_id[:8],
            "color": session["color"] if session else "violet",
        })
    return {
        "id": document["id"],
        "name": document["filename"],
        "kind": kind,
        "kind_label": label,
        "kind_color": color,
        "state": state,
        "note": note,
        "tokens": fmt_tokens(document["token_count"]) if document["status"] == "indexed" else "—",
        "token_count": document["token_count"],
        "chunks": document["chunk_count"] if document["status"] == "indexed" else "—",
        "chunk_count": document["chunk_count"],
        "updated": ago(document["indexed_at"] or document["created_at"]),
        "usage": usage,
        "global": attachments["global"],
        "sessions": attachments["sessions"],
    }


def brain_spec(segments: list[tuple[str, int, str]], tokens: int) -> str:
    """JSON for the canvas: each segment is (label, tokens, palette color)."""
    return json.dumps({
        "tokens": tokens,
        "budget": BRAIN_REFERENCE_TOKENS,
        "segs": [{"label": label, "tokens": t, "rgb": PALETTE[color]} for label, t, color in segments if t > 0],
    })


def bar(segments: list[tuple[str, int, str]], total: int) -> list[dict]:
    """Legend and bar widths. Widths are shares of what is shown, so the bar is always full."""
    shown = [s for s in segments if s[1] > 0]
    return [
        {"label": label, "tokens": fmt_tokens(t), "color": color, "pct": f"{t / max(total, 1) * 100:.2f}%"}
        for label, t, color in shown
    ]


class Snapshot:
    """Everything one dashboard render needs, read in one pass."""

    def __init__(self, conn: sqlite3.Connection, stale_after_hours: int, selected_id: str | None = None):
        self.docs = documents.list_documents(conn, limit=5000)
        raw_sessions = store.list_sessions(conn, None, SESSIONS_SHOWN, stale_after_hours)
        # Attached sessions stay addressable even when older than the listed ones.
        listed = {s["id"] for s in raw_sessions}
        for doc in self.docs:
            for session_id in doc["attachments"]["sessions"]:
                if session_id not in listed:
                    extra = store.get_session(conn, session_id, stale_after_hours)
                    if extra:
                        raw_sessions.append(extra)
                        listed.add(session_id)

        self.global_docs = [d for d in self.docs if d["attachments"]["global"]]
        self.global_tokens = sum(d["token_count"] for d in self.global_docs)
        global_ids = {d["id"] for d in self.global_docs}

        def own(session_id: str) -> list[dict]:
            return [d for d in self.docs if session_id in d["attachments"]["sessions"] and d["id"] not in global_ids]

        self._own = {s["id"]: own(s["id"]) for s in raw_sessions}
        if selected_id is None:
            live = [s for s in raw_sessions if s["status"] != "ended"]
            selected_id = (live or raw_sessions or [{"id": None}])[0]["id"]
        self.selected_id = selected_id
        # Colors follow the order sessions were opened in, so neighbours never share one.
        by_age = sorted(raw_sessions, key=lambda s: (s["started_at"], s["id"]))
        colors = {s["id"]: SESSION_COLORS[i % len(SESSION_COLORS)] for i, s in enumerate(by_age)}
        self.sessions = [
            session_view(
                s, sum(d["token_count"] for d in self._own[s["id"]]), self.global_tokens, selected_id, colors[s["id"]]
            )
            for s in raw_sessions
        ]
        self.sessions_by_id = {s["id"]: s for s in self.sessions}
        self.live_sessions = [s for s in self.sessions if s["status"] != "ended"]

    @property
    def selected(self) -> dict | None:
        return self.sessions_by_id.get(self.selected_id)

    def files(self, docs: list[dict]) -> list[dict]:
        return [file_view(d, self.sessions_by_id) for d in docs]

    def session_docs(self, session_id: str) -> list[dict]:
        return [d for d in self.docs if session_id in d["attachments"]["sessions"]]

    def session_context(self, session: dict) -> dict:
        own_docs = self._own.get(session["id"], [])
        own_tokens = sum(d["token_count"] for d in own_docs)
        docs = self.global_docs + own_docs
        total = self.global_tokens + own_tokens
        segments = [("Global", self.global_tokens, "violet"), (session["name"], own_tokens, session["color"])]
        return {
            "tokens": fmt_tokens(total),
            "chunks": sum(d["chunk_count"] for d in docs),
            "files": len(docs),
            "busy": any(d["status"] in ("pending", "processing") for d in docs + self.session_docs(session["id"])),
            "segs": bar(segments, total),
            "spec": brain_spec(segments, total),
        }

    def global_context(self) -> dict:
        segments = [("Global", self.global_tokens, "violet")] + [
            (s["name"], s["own_tokens"], s["color"]) for s in self.sessions
        ]
        attached = [d for d in self.docs if d["attachments"]["global"] or d["attachments"]["sessions"]]
        total = sum(d["token_count"] for d in attached)
        return {
            "tokens": fmt_tokens(total),
            "chunks": sum(d["chunk_count"] for d in attached),
            "files": len(attached),
            "sessions": len(self.live_sessions),
            "busy": any(d["status"] in ("pending", "processing") for d in attached),
            "segs": bar(segments, total),
            "spec": brain_spec(segments, total),
        }

    def library_stats(self) -> str:
        return f"{len(self.docs)} arquivos · {sum(d['chunk_count'] for d in self.docs)} chunks"

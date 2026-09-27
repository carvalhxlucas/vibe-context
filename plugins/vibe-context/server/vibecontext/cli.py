"""Command line entry point behind bin/vibecontext and the plugin's slash commands."""

import argparse
import sys
import time
from pathlib import Path

from vibecontext import client, runtime
from vibecontext.config import Paths, ensure_home, load_settings
from vibecontext.db import documents, store
from vibecontext.embeddings.base import collection_name, model_id

ADD_WAIT_SECONDS = 300


def cmd_start(paths: Paths, args: argparse.Namespace) -> int:
    settings = runtime.ensure_running(paths)
    print(f"qdrant    ready    {settings.qdrant_url}")
    print(f"backend   ready    {runtime.backend_url(settings)}")
    print("dashboard run /vibe-context:dashboard (available from phase 5)")
    return 0


def cmd_stop(paths: Paths, args: argparse.Namespace) -> int:
    ensure_home(paths)
    settings = load_settings(paths)
    backend = runtime.stop_backend(paths, settings)
    qdrant = runtime.stop_qdrant(paths, settings)
    print(f"backend   {'stopped' if backend else 'was not running'}")
    print(f"qdrant    {'stopped' if qdrant else 'not managed here or not running'}")
    print("Indexed data is kept.")
    return 0


def cmd_status(paths: Paths, args: argparse.Namespace) -> int:
    ensure_home(paths)
    settings = load_settings(paths)
    backend = runtime.backend_healthy(settings)
    qdrant = runtime.qdrant_healthy(settings)
    conn = store.connect(paths.db)
    try:
        sessions = store.count_sessions(conn, settings.vibecontext_stale_after_hours)
        docs = documents.count_documents(conn)
        failed = documents.list_documents(conn, status="failed", limit=10)
        waiting = [d for d in documents.list_documents(conn, status="pending", limit=50) if d["error"]][:5]
    finally:
        conn.close()
    print(f"backend   {'running' if backend else 'stopped'}  {runtime.backend_url(settings)}")
    print(f"qdrant    {'running' if qdrant else 'stopped'}  {settings.qdrant_url}")
    print(f"embedding {model_id(settings)}  (collection {collection_name(settings)})")
    print(f"sessions  {sessions['open']} open, {sessions['stale']} stale, {sessions['ended']} ended")
    print(
        f"documents {docs['indexed']} indexed, {docs['pending'] + docs['processing']} in queue, {docs['failed']} failed"
    )
    for doc in failed:
        print(f"  failed  {doc['filename']} ({doc['id']}): {doc['error']}")
    for doc in waiting:
        print(f"  waiting {doc['filename']} ({doc['id']}): {doc['error']}")
    print(f"logs      {paths.logs}")
    return 0


def cmd_dashboard(paths: Paths, args: argparse.Namespace) -> int:
    print("The dashboard arrives in phase 5.")
    return 0


def cmd_add(paths: Paths, args: argparse.Namespace) -> int:
    path = Path(args.file).expanduser()
    if not path.is_file():
        print(f"vibecontext: not a file: {path}", file=sys.stderr)
        return 1
    form = {"scope": "global"} if args.is_global else {"scope": "session", "session_id": args.session}
    try:
        with path.open("rb") as f:
            result = client.call(paths, "POST", "/api/documents", timeout=300, data=form, files={"file": (path.name, f)})
    except client.ApiError as error:
        print(f"vibecontext: upload rejected: {error}", file=sys.stderr)
        return 1

    document = result["document"]
    if result["duplicate"]:
        print(f"already added   {document['filename']} ({document['id']}), status {document['status']}")
        return 0

    deadline = time.monotonic() + ADD_WAIT_SECONDS
    while document["status"] in ("pending", "processing") and not document["error"] and time.monotonic() < deadline:
        time.sleep(1)
        document = client.call(paths, "GET", f"/api/documents/{document['id']}")

    if document["status"] == "indexed":
        print(f"indexed   {document['filename']} ({document['id']}): {document['chunk_count']} chunks")
        return 0
    if document["status"] == "failed":
        print(f"failed    {document['filename']} ({document['id']}): {document['error']}", file=sys.stderr)
        return 1
    reason = document["error"] or f"still {document['status']}"
    print(f"queued    {document['filename']} ({document['id']}): {reason}")
    return 0


COMMANDS = {
    "start": cmd_start,
    "stop": cmd_stop,
    "status": cmd_status,
    "dashboard": cmd_dashboard,
    "add": cmd_add,
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vibecontext")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("start", "stop", "status", "dashboard"):
        commands.add_parser(name)
    add = commands.add_parser("add", help="attach a file to a session or to the global context")
    add.add_argument("file")
    target = add.add_mutually_exclusive_group(required=True)
    target.add_argument("--session", help="Claude Code session id")
    target.add_argument("--global", dest="is_global", action="store_true", help="available to every session")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return COMMANDS[args.command](Paths.from_env(), args)
    except runtime.RuntimeFailure as error:
        print(f"vibecontext: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())

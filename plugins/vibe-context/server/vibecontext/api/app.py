import logging
import threading
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vibecontext import __version__
from vibecontext.api import routes_documents, routes_search, routes_sessions, routes_system
from vibecontext.api.auth import has_valid_token
from vibecontext.config import Paths, ensure_home, load_api_token, load_settings
from vibecontext.dashboard import routes as dashboard
from vibecontext.dashboard.auth import COOKIE_NAME, DashboardAuth
from vibecontext.ingest import resources
from vibecontext.ingest.indexer import Indexer
from vibecontext.ingest.worker import IngestWorker
from vibecontext.rerank.base import Reranker, create_reranker
from vibecontext.retrieval.search import Searcher

# Multipart framing around the file itself: boundaries, headers, the scope fields.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


STATIC_DIR = Path(__file__).resolve().parents[1] / "dashboard" / "static"
PUBLIC_PATHS = {"/health", "/login"}

# Everything is served from this origin: no inline script, no eval, no framing.
DASHBOARD_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    # The one-time login code sits in a URL; never send it anywhere as a referrer.
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}

# Distinguishes "build the reranker from settings" from an explicit None (no reranking).
_FROM_SETTINGS = object()


def _log_to_file(logger_name: str, target: str) -> None:
    logger = logging.getLogger(logger_name)
    if any(getattr(h, "baseFilename", None) == target for h in logger.handlers):
        return
    handler = logging.FileHandler(target)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app(
    paths: Paths | None = None,
    indexer: Indexer | None = None,
    reranker: Reranker | None | object = _FROM_SETTINGS,
) -> FastAPI:
    paths = paths or Paths.from_env()
    ensure_home(paths)
    resources.configure(paths)
    _log_to_file("vibecontext.ingest", str(paths.logs / "ingest.log"))
    _log_to_file("vibecontext.search", str(paths.logs / "search.log"))
    settings = load_settings(paths)
    indexer = indexer or Indexer(settings, paths)
    worker = IngestWorker(paths, settings, indexer)
    if reranker is _FROM_SETTINGS:
        reranker = create_reranker(settings)
    searcher = Searcher(indexer, reranker, paths.db, settings.search_candidates)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
        threading.Thread(target=searcher.warm_up, name="search-warm-up", daemon=True).start()
        yield
        worker.stop()

    # No CORS middleware: browsers block cross-origin reads, and the dashboard is same-origin.
    app = FastAPI(
        title="VibeContext", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.state.paths = paths
    app.state.settings = settings
    app.state.api_token = load_api_token(paths)
    app.state.worker = worker
    app.state.indexer = indexer
    app.state.searcher = searcher

    app.state.dashboard_auth = DashboardAuth()

    max_upload_bytes = settings.max_upload_mb * 1024 * 1024
    upload_limits = {
        "/api/documents": max_upload_bytes,
        "/ui/upload": max_upload_bytes * dashboard.MAX_FILES_PER_UPLOAD,
    }

    # Runs before any route reads the body, so an unauthenticated or oversized
    # upload is rejected without being spooled to disk.
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            # The API is for the CLI, the MCP server and the hooks: bearer token only.
            if not has_valid_token(request):
                return JSONResponse(
                    {"detail": "Invalid or missing token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
        elif path not in PUBLIC_PATHS and not path.startswith("/static/"):
            # Dashboard pages and fragments: the session cookie set by /login.
            if not app.state.dashboard_auth.is_valid(request.cookies.get(COOKIE_NAME)):
                return dashboard.login_required(request)
            # SameSite=Strict already keeps the cookie off cross-site requests; a custom
            # header on every change is a second lock, since forms cannot set one.
            if request.method not in ("GET", "HEAD") and request.headers.get("hx-request") != "true":
                return PlainTextResponse("Changes must come from the dashboard page.", status_code=403)

        limit = upload_limits.get(path) if request.method == "POST" else None
        if limit is not None:
            length = request.headers.get("content-length")
            if length is None or not length.isdigit():
                return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
            if int(length) > limit + MULTIPART_OVERHEAD_BYTES:
                return JSONResponse(
                    {"detail": f"File is larger than MAX_UPLOAD_MB ({settings.max_upload_mb} MB)"}, status_code=413
                )

        response = await call_next(request)
        if not path.startswith("/api/"):
            response.headers.update(DASHBOARD_HEADERS)
        return response

    # Added last so it runs first. Rejects DNS rebinding: a page on evil.example
    # resolving to 127.0.0.1 still sends its own Host header.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    app.include_router(routes_system.router)
    app.include_router(routes_sessions.router)
    app.include_router(routes_documents.router)
    app.include_router(routes_search.router)
    app.include_router(dashboard.router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app

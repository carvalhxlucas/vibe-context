import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from vibecontext import __version__
from vibecontext.api import routes_documents, routes_sessions, routes_system
from vibecontext.api.auth import has_valid_token
from vibecontext.config import Paths, ensure_home, load_api_token, load_settings
from vibecontext.ingest import resources
from vibecontext.ingest.indexer import Indexer
from vibecontext.ingest.worker import IngestWorker

# Multipart framing around the file itself: boundaries, headers, the scope fields.
MULTIPART_OVERHEAD_BYTES = 64 * 1024


def _log_ingestion_to_file(paths: Paths) -> None:
    logger = logging.getLogger("vibecontext.ingest")
    target = str(paths.logs / "ingest.log")
    if any(getattr(h, "baseFilename", None) == target for h in logger.handlers):
        return
    handler = logging.FileHandler(target)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def create_app(paths: Paths | None = None, indexer: Indexer | None = None) -> FastAPI:
    paths = paths or Paths.from_env()
    ensure_home(paths)
    resources.configure(paths)
    _log_ingestion_to_file(paths)
    settings = load_settings(paths)
    indexer = indexer or Indexer(settings, paths)
    worker = IngestWorker(paths, settings, indexer)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        worker.start()
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

    max_upload_bytes = settings.max_upload_mb * 1024 * 1024

    # Runs before any route reads the body, so an unauthenticated or oversized
    # upload is rejected without being spooled to disk.
    @app.middleware("http")
    async def guard_api(request: Request, call_next):
        if request.url.path.startswith("/api/"):
            if not has_valid_token(request):
                return JSONResponse(
                    {"detail": "Invalid or missing token"}, status_code=401, headers={"WWW-Authenticate": "Bearer"}
                )
            if request.method == "POST" and request.url.path == "/api/documents":
                length = request.headers.get("content-length")
                if length is None or not length.isdigit():
                    return JSONResponse({"detail": "Content-Length is required"}, status_code=411)
                if int(length) > max_upload_bytes + MULTIPART_OVERHEAD_BYTES:
                    return JSONResponse(
                        {"detail": f"File is larger than MAX_UPLOAD_MB ({settings.max_upload_mb} MB)"}, status_code=413
                    )
        return await call_next(request)

    # Added last so it runs first. Rejects DNS rebinding: a page on evil.example
    # resolving to 127.0.0.1 still sends its own Host header.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    app.include_router(routes_system.router)
    app.include_router(routes_sessions.router)
    app.include_router(routes_documents.router)
    return app

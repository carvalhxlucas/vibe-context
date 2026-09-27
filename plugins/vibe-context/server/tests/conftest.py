import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.fakes import fake_indexer
from vibecontext.api.app import create_app
from vibecontext.config import Paths, ensure_home, load_api_token, load_settings
from vibecontext.ingest import resources

# Keep the tiktoken vocabulary between test runs instead of downloading it per temp dir.
os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(Path(__file__).parent / ".cache" / "tiktoken"))


@pytest.fixture
def paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    # Pre-created with a loose mode, like uv does when it builds the venv inside it.
    home.mkdir(mode=0o755)
    monkeypatch.setenv("VIBECONTEXT_HOME", str(home))
    paths = Paths(home)
    ensure_home(paths)
    return paths


@pytest.fixture
def app(paths, monkeypatch):
    # Leave grammar caches at their defaults so tests do not download them per temp dir.
    monkeypatch.setattr(resources, "configure", lambda paths: None)
    return create_app(paths, indexer=fake_indexer(load_settings(paths)))


@pytest.fixture
def client(app):
    # TrustedHostMiddleware rejects the default "testserver" host. No lifespan here:
    # tests drive the ingestion worker with app.state.worker.run_once().
    return TestClient(app, base_url="http://127.0.0.1:8765")


@pytest.fixture
def auth(paths):
    return {"Authorization": f"Bearer {load_api_token(paths)}"}

"""Paths, secrets and settings.

Everything VibeContext writes lives under VIBECONTEXT_HOME (default ~/.vibecontext),
outside the plugin directory, so data survives plugin updates.
"""

import json
import os
import secrets
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
ENV_EXAMPLE = PLUGIN_ROOT / ".env.example"


class Paths:
    def __init__(self, root: Path):
        self.root = root
        self.db = root / "vibecontext.db"
        self.env = root / ".env"
        self.secrets = root / "secrets.json"
        self.files = root / "files"
        self.logs = root / "logs"
        self.run = root / "run"
        self.cache = root / "cache"

    @classmethod
    def from_env(cls) -> "Paths":
        return cls(Path(os.environ.get("VIBECONTEXT_HOME", Path.home() / ".vibecontext")))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    vibecontext_port: int = 8765
    vibecontext_stale_after_hours: int = 12
    qdrant_url: str = "http://127.0.0.1:6333"
    qdrant_api_key: str = ""

    embedding_provider: Literal["openai", "local"] = "openai"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    local_embedding_model: str = "intfloat/multilingual-e5-base"
    embedding_batch_size: int = 64
    bm25_language: str = "portuguese"

    # Defaults fit a 512-token local embedding model; raise them for OpenAI embeddings.
    text_chunk_tokens: int = 400
    text_chunk_overlap: int = 60
    code_chunk_max_tokens: int = 480
    max_upload_mb: int = 50

    @model_validator(mode="after")
    def _check_chunking(self) -> "Settings":
        if not 0 <= self.text_chunk_overlap < self.text_chunk_tokens:
            raise ValueError("TEXT_CHUNK_OVERLAP must be at least 0 and smaller than TEXT_CHUNK_TOKENS")
        return self


def load_settings(paths: Paths) -> Settings:
    return Settings(_env_file=paths.env if paths.env.exists() else None)


def _write_private(path: Path, content: str) -> None:
    # O_EXCL: never overwrite secrets another process created a moment ago.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(content)


def ensure_home(paths: Paths) -> None:
    """Create the data directory, the .env file and the API token on first run."""
    paths.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    # uv may have created the directory first (for the venv) with the default umask.
    paths.root.chmod(0o700)
    for directory in (paths.files, paths.logs, paths.run, paths.cache):
        directory.mkdir(mode=0o700, exist_ok=True)

    if not paths.env.exists():
        lines = []
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            if line.strip() == "QDRANT_API_KEY=":
                line = "QDRANT_API_KEY=" + secrets.token_urlsafe(32)
            lines.append(line)
        try:
            _write_private(paths.env, "\n".join(lines) + "\n")
        except FileExistsError:
            pass

    if not paths.secrets.exists():
        try:
            _write_private(paths.secrets, json.dumps({"api_token": secrets.token_urlsafe(32)}) + "\n")
        except FileExistsError:
            pass


def load_api_token(paths: Paths) -> str:
    return json.loads(paths.secrets.read_text(encoding="utf-8"))["api_token"]

"""Starts and stops Qdrant and the backend. Shared by the CLI and the MCP server.

Every subprocess captures its output: the MCP server speaks JSON-RPC over stdout,
and a stray line from docker or colima would corrupt the protocol.
"""

import fcntl
import os
import signal
import shutil
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from urllib.parse import urlparse

import httpx

from vibecontext.config import PLUGIN_ROOT, Paths, Settings, ensure_home, load_settings
from vibecontext.ingest import resources

COMPOSE_FILE = PLUGIN_ROOT / "docker-compose.yml"
BACKEND_APP = "vibecontext.api.app:create_app"

# A busy backend can answer /health slowly (a native extension holding the GIL
# while loading); give it this long before concluding the port holds something else.
BUSY_BACKEND_GRACE_SECONDS = 30


class RuntimeFailure(RuntimeError):
    pass


def backend_url(settings: Settings) -> str:
    return f"http://127.0.0.1:{settings.vibecontext_port}"


def backend_healthy(settings: Settings) -> bool:
    try:
        response = httpx.get(backend_url(settings) + "/health", timeout=3.0)
        return response.status_code == 200 and response.json().get("service") == "vibecontext"
    except (httpx.HTTPError, ValueError):
        return False


def qdrant_is_local(settings: Settings) -> bool:
    return urlparse(settings.qdrant_url).hostname in ("127.0.0.1", "localhost")


def qdrant_healthy(settings: Settings) -> bool:
    try:
        response = httpx.get(
            settings.qdrant_url.rstrip("/") + "/readyz",
            headers={"api-key": settings.qdrant_api_key} if settings.qdrant_api_key else {},
            timeout=2.0,
        )
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def _wait(check, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if check():
            return True
        time.sleep(0.5)
    return False


@contextmanager
def _start_lock(paths: Paths):
    # Two sessions opening at once would both try to start the same services.
    with open(paths.run / "start.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _ensure_docker() -> None:
    if shutil.which("docker") is None:
        raise RuntimeFailure("docker CLI not found. Install Docker (or colima + docker) and retry.")
    if _run(["docker", "info"], timeout=15).returncode == 0:
        return
    if shutil.which("colima") is None:
        raise RuntimeFailure("Docker daemon is not running. Start Docker and retry.")
    result = _run(["colima", "start"], timeout=300)
    if result.returncode != 0:
        raise RuntimeFailure("colima start failed: " + (result.stderr.strip().splitlines() or ["no output"])[-1])


def _compose(paths: Paths, *args: str, timeout: int = 300) -> subprocess.CompletedProcess:
    return _run(["docker", "compose", "-f", str(COMPOSE_FILE), "--env-file", str(paths.env), *args], timeout=timeout)


def start_qdrant(paths: Paths, settings: Settings) -> None:
    if qdrant_healthy(settings):
        return
    if not qdrant_is_local(settings):
        raise RuntimeFailure(f"Qdrant at {settings.qdrant_url} is unreachable. Check QDRANT_URL and QDRANT_API_KEY.")
    _ensure_docker()
    result = _compose(paths, "up", "-d")
    if result.returncode != 0:
        raise RuntimeFailure("docker compose up failed: " + (result.stderr.strip().splitlines() or ["no output"])[-1])
    if not _wait(lambda: qdrant_healthy(settings), 60):
        raise RuntimeFailure("Qdrant did not become ready in 60s. See: docker compose logs qdrant")


def stop_qdrant(paths: Paths, settings: Settings) -> bool:
    if not qdrant_is_local(settings) or shutil.which("docker") is None:
        return False
    return _compose(paths, "stop", timeout=60).returncode == 0


def _pid_file(paths: Paths):
    return paths.run / "backend.pid"


def _read_pid(paths: Paths) -> int | None:
    try:
        return int(_pid_file(paths).read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _is_our_backend(pid: int) -> bool:
    # A stale pid file may point at an unrelated process that reused the pid.
    result = _run(["ps", "-p", str(pid), "-o", "command="], timeout=5)
    return result.returncode == 0 and BACKEND_APP in result.stdout


def _port_open(settings: Settings) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", settings.vibecontext_port)) == 0


def _listening_backend_pid(settings: Settings) -> int | None:
    result = _run(["lsof", "-nP", "-t", f"-iTCP:{settings.vibecontext_port}", "-sTCP:LISTEN"], timeout=5)
    for line in result.stdout.split():
        if line.isdigit() and _is_our_backend(int(line)):
            return int(line)
    return None


def start_backend(paths: Paths, settings: Settings) -> None:
    if backend_healthy(settings):
        return
    if _port_open(settings):
        # Something already listens. If it is our backend under load, wait for it;
        # starting a second one would fail to bind and could disturb the first.
        if _wait(lambda: backend_healthy(settings), BUSY_BACKEND_GRACE_SECONDS):
            return
        raise RuntimeFailure(
            f"Port {settings.vibecontext_port} is taken by a process that does not answer /health. "
            "Stop it or set VIBECONTEXT_PORT in ~/.vibecontext/.env."
        )

    # Grammars and the token vocabulary download once, here, so ingestion works offline later.
    resources.configure(paths)
    resources.prefetch(paths)
    log_path = paths.logs / "backend.log"
    with open(log_path, "ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", BACKEND_APP, "--factory",
             "--host", "127.0.0.1", "--port", str(settings.vibecontext_port)],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            start_new_session=True,
            env={**os.environ, "VIBECONTEXT_HOME": str(paths.root)},
        )
    _wait(lambda: backend_healthy(settings) or process.poll() is not None, 30)
    # Record the pid only once this process is the one serving, never a loser of a bind race.
    if process.poll() is not None or not backend_healthy(settings):
        raise RuntimeFailure(f"Backend did not start. See {log_path}")
    _pid_file(paths).write_text(str(process.pid))


def stop_backend(paths: Paths, settings: Settings) -> bool:
    pid = _read_pid(paths)
    _pid_file(paths).unlink(missing_ok=True)
    if pid is None or not _is_our_backend(pid):
        # Pid file missing or stale: find the backend by the port it listens on.
        pid = _listening_backend_pid(settings)
    if pid is None:
        return False
    os.kill(pid, signal.SIGTERM)
    _wait(lambda: not _is_our_backend(pid), 10)
    return True


def ensure_running(paths: Paths | None = None) -> Settings:
    """Start whatever is not running yet. Safe to call from several processes at once."""
    paths = paths or Paths.from_env()
    ensure_home(paths)
    settings = load_settings(paths)
    with _start_lock(paths):
        start_qdrant(paths, settings)
        start_backend(paths, settings)
    return settings

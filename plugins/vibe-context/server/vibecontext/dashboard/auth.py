"""Dashboard login: one-time codes exchanged for a session cookie.

`vibecontext dashboard` asks the API (with the bearer token) for a code and opens
/login?code=... in the browser. The code works once, for two minutes, so the API
token never reaches the browser and a leaked URL is useless after use.

Sessions live in memory: restarting the backend logs the dashboard out, and
/vibe-context:dashboard logs it back in.
"""

import secrets
import threading
import time

COOKIE_NAME = "vibecontext_dashboard"
CODE_TTL_SECONDS = 120


class DashboardAuth:
    def __init__(self) -> None:
        self._codes: dict[str, float] = {}
        self._sessions: set[str] = set()
        self._lock = threading.Lock()

    def issue_code(self) -> str:
        code = secrets.token_urlsafe(32)
        with self._lock:
            now = time.monotonic()
            self._codes = {c: expiry for c, expiry in self._codes.items() if expiry > now}
            self._codes[code] = now + CODE_TTL_SECONDS
        return code

    def redeem(self, code: str) -> str | None:
        """Exchange a valid code for a new session. Each code works once."""
        with self._lock:
            expiry = self._codes.pop(code, None)
            if expiry is None or expiry < time.monotonic():
                return None
            session = secrets.token_urlsafe(32)
            self._sessions.add(session)
            return session

    def is_valid(self, session: str | None) -> bool:
        return bool(session) and session in self._sessions

"""In-memory sliding-window rate limiter (single-process)."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, *, limit: int, window_s: float) -> bool:
        """Return True if allowed, False if limited."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Limit login/token and publish by client IP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        login_limit: int = 20,
        publish_limit: int = 60,
        window_s: float = 60.0,
        path_prefix: str = "",
    ) -> None:
        super().__init__(app)
        self._limiter = RateLimiter()
        self.login_limit = login_limit
        self.publish_limit = publish_limit
        self.window_s = window_s
        self.path_prefix = path_prefix.rstrip("/")

    def _strip(self, path: str) -> str:
        if self.path_prefix and path.startswith(self.path_prefix):
            return path[len(self.path_prefix) :] or "/"
        return path

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        path = self._strip(request.url.path)
        method = request.method.upper()
        client = request.client.host if request.client else "unknown"

        limit: int | None = None
        bucket = ""
        if method == "POST" and path in ("/auth/login", "/auth/token", "/auth/register"):
            limit = self.login_limit
            bucket = f"auth:{client}"
        elif method == "POST" and path == "/publish":
            limit = self.publish_limit
            bucket = f"publish:{client}"

        if limit is not None and not self._limiter.hit(bucket, limit=limit, window_s=self.window_s):
            return JSONResponse(
                {"detail": "rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(int(self.window_s))},
            )
        return await call_next(request)
